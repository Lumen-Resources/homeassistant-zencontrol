"""Async UDP/TCP transport client for the zencontrol TPI Advanced protocol."""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from .protocol import Response, parse_response

_LOGGER = logging.getLogger(__name__)

# Maximum time to wait for a response to a single request
DEFAULT_TIMEOUT = 5.0


class _UdpProtocol(asyncio.DatagramProtocol):
    """asyncio DatagramProtocol that feeds received datagrams to a callback."""

    def __init__(
        self,
        on_data: Callable[[bytes], None],
        on_lost: Callable[[Exception | None], None],
    ) -> None:
        self._on_data = on_data
        self._on_lost = on_lost

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._on_data(data)

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error received: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("UDP connection lost: %s", exc)
        self._on_lost(exc)


class _TcpProtocol(asyncio.Protocol):
    """asyncio Protocol for TCP; reassembles stream into TPI frames."""

    def __init__(
        self,
        on_data: Callable[[bytes], None],
        on_lost: Callable[[Exception | None], None],
    ) -> None:
        self._on_data = on_data
        self._on_lost = on_lost
        self._buf = bytearray()

    def data_received(self, data: bytes) -> None:
        self._buf.extend(data)
        self._try_parse()

    def _try_parse(self) -> None:
        # TPI Advanced response: [ResponseType, Seq, DataLen, Data..., Checksum]
        # Minimum 4 bytes; total = 3 + DataLen + 1
        while len(self._buf) >= 4:
            data_len = self._buf[2]
            frame_len = 3 + data_len + 1  # header(3) + data + checksum(1)
            if len(self._buf) < frame_len:
                break
            frame = bytes(self._buf[:frame_len])
            del self._buf[:frame_len]
            self._on_data(frame)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("TCP connection lost: %s", exc)
        self._on_lost(exc)


class TpiClient:
    """Low-level async transport for TPI Advanced requests over UDP or TCP.

    Maintains a sequence counter and maps in-flight requests to asyncio Futures
    so responses can be matched to their originating request.

    Usage::

        client = TpiClient(host="192.168.1.10", port=5108)
        await client.connect()
        response = await client.send(frame, seq)
        await client.disconnect()
    """

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = DEFAULT_TIMEOUT,
        use_tcp: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._use_tcp = use_tcp
        self._seq: int = 0
        self._pending: dict[int, asyncio.Future[Response]] = {}
        self._transport: asyncio.BaseTransport | None = None
        self._writer: asyncio.StreamWriter | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the UDP or TCP connection to the controller."""
        if self._use_tcp:
            await self._connect_tcp()
        else:
            await self._connect_udp()

    async def _connect_udp(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self._on_raw_data, self._on_connection_lost),
            remote_addr=(self._host, self._port),
        )
        self._transport = transport
        _LOGGER.debug("UDP connected to %s:%s", self._host, self._port)

    async def _connect_tcp(self) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_connection(
            lambda: _TcpProtocol(self._on_raw_data, self._on_connection_lost),
            host=self._host,
            port=self._port,
        )
        self._transport = transport
        _LOGGER.debug("TCP connected to %s:%s", self._host, self._port)

    def _on_connection_lost(self, exc: Exception | None) -> None:
        """Transport dropped — mark disconnected and fail in-flight requests.

        Clearing the transport makes ``connected`` truthful so the coordinator's
        periodic update reconnects on its next cycle.
        """
        self._transport = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError(f"TPI transport lost: {exc}"))
        self._pending.clear()

    async def disconnect(self) -> None:
        """Close the transport and cancel any pending requests."""
        if self._transport:
            self._transport.close()
            self._transport = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    @property
    def connected(self) -> bool:
        return self._transport is not None

    # ------------------------------------------------------------------
    # Sequence counter
    # ------------------------------------------------------------------

    def next_seq(self) -> int:
        """Return the next free sequence byte (0–255, wrapping).

        Skips sequence numbers that still have a pending request so a slow
        response can never be matched to (or clobbered by) a newer request
        reusing the same seq. With 256 requests genuinely in flight there is
        no safe number left, so we fail loudly rather than corrupt matching.
        """
        for _ in range(256):
            seq = self._seq
            self._seq = (self._seq + 1) & 0xFF
            if seq not in self._pending:
                return seq
        raise ConnectionError("All 256 TPI sequence numbers are in flight")

    # ------------------------------------------------------------------
    # Send / receive
    # ------------------------------------------------------------------

    def _on_raw_data(self, data: bytes) -> None:
        """Called by the protocol when raw bytes arrive."""
        response = parse_response(data)
        if response is None:
            return
        future = self._pending.pop(response.seq, None)
        if future and not future.done():
            future.set_result(response)

    async def send(self, frame: bytes, seq: int) -> Response:
        """Send *frame* and wait for the matching response.

        Raises:
            ConnectionError: if not connected.
            asyncio.TimeoutError: if no response arrives within the timeout.
        """
        if self._transport is None:
            raise ConnectionError("TpiClient not connected")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Response] = loop.create_future()
        self._pending[seq] = future

        if self._use_tcp:
            # TCP transport — use write()
            self._transport.write(frame)  # type: ignore[attr-defined]
        else:
            self._transport.sendto(frame)  # type: ignore[attr-defined]

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise
