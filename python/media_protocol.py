"""Shared framing for the UNO Q media and speech TCP streams."""

from __future__ import annotations

import socket
import struct


AUDIO_MAGIC = b"AUD0"
AUDIO_HEADER = struct.Struct("!IHH")  # sample rate, channels, bits/sample
FRAME_LENGTH = struct.Struct("!I")


def stream_header(sample_rate: int, channels: int, bits_per_sample: int = 16) -> bytes:
    return AUDIO_MAGIC + AUDIO_HEADER.pack(sample_rate, channels, bits_per_sample)


def read_exact(
    connection: socket.socket,
    size: int,
    closed_message: str = "media stream closed",
) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ConnectionError(closed_message)
        data.extend(chunk)
    return bytes(data)
