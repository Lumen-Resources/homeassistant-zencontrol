"""TpiClient transport-layer tests (no network)."""
import asyncio

import pytest

from tpi.client import TpiClient
from tpi.const import ResponseType


def make_client() -> TpiClient:
    return TpiClient(host="203.0.113.1", port=5108)


# ---------------------------------------------------------------------------
# Sequence-counter guard
# ---------------------------------------------------------------------------

def test_next_seq_wraps():
    client = make_client()
    client._seq = 255
    assert client.next_seq() == 255
    assert client.next_seq() == 0


def test_next_seq_skips_pending():
    client = make_client()
    client._pending[0] = object()  # seq 0 still in flight
    assert client.next_seq() == 1


def test_next_seq_exhaustion_raises():
    client = make_client()
    client._pending = {seq: object() for seq in range(256)}
    with pytest.raises(ConnectionError):
        client.next_seq()


# ---------------------------------------------------------------------------
# Response routing
# ---------------------------------------------------------------------------

def test_on_raw_data_resolves_matching_future():
    async def scenario():
        client = make_client()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client._pending[0] = future
        client._on_raw_data(bytes.fromhex("A10002FFFEA2"))  # ANSWER, seq 0
        response = await future
        assert response.response_type == ResponseType.ANSWER
        assert response.data == bytes([0xFF, 0xFE])
        assert 0 not in client._pending

    asyncio.run(scenario())


def test_on_raw_data_ignores_bad_checksum():
    async def scenario():
        client = make_client()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client._pending[0] = future
        client._on_raw_data(bytes.fromhex("A10002FFFEA3"))  # corrupted checksum
        assert not future.done()
        assert 0 in client._pending

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# Transport-loss handling
# ---------------------------------------------------------------------------

def test_connection_lost_fails_pending_and_disconnects():
    async def scenario():
        client = make_client()
        client._transport = object()  # pretend we're connected
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        client._pending[7] = future

        client._on_connection_lost(RuntimeError("cable pulled"))

        assert client._transport is None
        assert not client.connected
        assert not client._pending
        with pytest.raises(ConnectionError):
            await future

    asyncio.run(scenario())


def test_send_requires_connection():
    async def scenario():
        client = make_client()
        with pytest.raises(ConnectionError):
            await client.send(b"\x04\x00\x27\x00\x00\x00\x00\x23", 0)

    asyncio.run(scenario())
