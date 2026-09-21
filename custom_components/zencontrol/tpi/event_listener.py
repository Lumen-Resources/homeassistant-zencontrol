"""Shared UDP event listener for TPI Advanced unicast/multicast events.

A single EventListener instance is shared across all controller coordinators
registered in one Home Assistant instance. Incoming events are dispatched to
the coordinator registered for the source IP address of the packet.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import struct
from typing import Callable

from .protocol import TpiEvent, parse_event

_LOGGER = logging.getLogger(__name__)

# Callback signature: (source_ip: str, event: TpiEvent) -> None
EventCallback = Callable[[str, TpiEvent], None]


class _EventProtocol(asyncio.DatagramProtocol):
    """asyncio DatagramProtocol — parses arriving datagrams into TpiEvent objects."""

    def __init__(
        self,
        dispatch: Callable[[str, TpiEvent], None],
        closed: asyncio.Event | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._closed = closed

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        source_ip = addr[0]
        event = parse_event(data)
        if event is not None:
            self._dispatch(source_ip, event)
        else:
            _LOGGER.debug("Received non-event UDP datagram from %s (len=%d)", source_ip, len(data))

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("Event listener UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        _LOGGER.debug("Event listener connection lost: %s", exc)
        if self._closed is not None:
            self._closed.set()


class EventListener:
    """Shared listener that dispatches TPI events to per-controller callbacks.

    Supports both unicast (HA listens on a local port) and multicast
    (HA joins the zencontrol IGMP group).  In unicast mode the source IP
    of each arriving datagram is used to route the event to the correct
    coordinator.  In multicast mode the MAC address embedded in the event
    frame is used as the routing key instead.
    """

    def __init__(self, port: int, use_multicast: bool = False) -> None:
        self._port = port
        self._use_multicast = use_multicast
        self._handlers: dict[str, EventCallback] = {}  # ip (or mac-str) → callback
        self._transport: asyncio.DatagramTransport | None = None
        self._closed_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start listening for incoming event datagrams."""
        loop = asyncio.get_running_loop()
        self._closed_event = asyncio.Event()
        if self._use_multicast:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _EventProtocol(self._dispatch_multicast, self._closed_event),
                sock=self._make_multicast_socket(),
            )
        else:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _EventProtocol(self._dispatch_unicast, self._closed_event),
                sock=self._make_unicast_socket(),
            )
        self._transport = transport  # type: ignore[assignment]
        _LOGGER.debug(
            "Event listener started on port %d (multicast=%s)",
            self._port,
            self._use_multicast,
        )

    async def stop(self) -> None:
        """Stop the listener and wait for the socket to actually be released.

        Closing an asyncio transport only schedules the underlying socket
        close; without waiting for connection_lost() a fast reload (unload
        followed immediately by setup) can race the new bind against the
        still-open old socket, producing a spurious "Address in use" error.
        """
        if self._transport:
            transport = self._transport
            closed_event = self._closed_event
            self._transport = None
            transport.close()
            if closed_event is not None:
                try:
                    await asyncio.wait_for(closed_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Timed out waiting for event listener socket to close"
                    )
            self._closed_event = None

    @property
    def running(self) -> bool:
        return self._transport is not None

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def register(self, controller_ip: str, callback: EventCallback) -> None:
        """Register *callback* to receive events from *controller_ip*."""
        self._handlers[controller_ip] = callback
        _LOGGER.debug("Registered event handler for controller %s", controller_ip)

    def unregister(self, controller_ip: str) -> None:
        """Remove the handler for *controller_ip*."""
        self._handlers.pop(controller_ip, None)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch_unicast(self, source_ip: str, event: TpiEvent) -> None:
        """Route by UDP source IP (unicast mode)."""
        callback = self._handlers.get(source_ip)
        if callback:
            callback(source_ip, event)
        else:
            _LOGGER.debug(
                "No handler registered for controller IP %s (event type 0x%02X)",
                source_ip,
                event.event_type,
            )

    def _dispatch_multicast(self, source_ip: str, event: TpiEvent) -> None:
        """Route by controller IP (multicast — source IP is the sending controller)."""
        # In multicast the source IP is still the sending controller's IP,
        # so we can use the same routing strategy as unicast.
        self._dispatch_unicast(source_ip, event)

    # ------------------------------------------------------------------
    # Unicast socket factory
    # ------------------------------------------------------------------

    def _make_unicast_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", self._port))
        return sock

    # ------------------------------------------------------------------
    # Multicast socket factory
    # ------------------------------------------------------------------

    @staticmethod
    def _make_multicast_socket() -> socket.socket:
        from .const import MULTICAST_ADDR, MULTICAST_PORT
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # Not available on Windows
        except (AttributeError, OSError):
            pass
        sock.bind(("", MULTICAST_PORT))
        group = socket.inet_aton(MULTICAST_ADDR)
        mreq = struct.pack("4sL", group, socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        return sock
