"""
Client side of the `bin/virtual_display` bridge protocol.

Kept free of device state so the automation worker and the Web UI preview
sampler share one implementation: the worker owns the bridge, the Web process
only reads frames through the same forwarded loopback port.
"""
import socket
import struct

import numpy as np

# The bridge is a fixed-size frame server; anything larger is a corrupt header.
MAX_FRAME_BYTES = 20 * 1024 * 1024


class ScreenshotSizeError(Exception):
    pass


def recv_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ScreenshotSizeError('The virtual-display bridge closed the connection')
        data.extend(chunk)
    return bytes(data)


def read_rgb_frame(stream, recv=recv_exact):
    """
    Read a `width, height, size` big-endian header followed by RGB bytes.

    Returns:
        tuple[int, int, bytes]: width, height, payload
    """
    width, height, size = struct.unpack('>III', recv(stream, 12))
    expected = width * height * 3
    if not width or not height or size != expected or size > MAX_FRAME_BYTES:
        raise ScreenshotSizeError(
            f'The virtual-display bridge returned invalid frame {width}x{height}/{size}'
        )
    return width, height, recv(stream, size)


def bridge_command(port, command, timeout=10, recv=recv_exact):
    """
    Send one command on a fresh connection, as the bridge closes it after replying.

    Args:
        port (int): Local TCP port forwarded to the bridge socket.
        command (bytes): e.g. b'FRAME\\n', b'INFO\\n'.

    Returns:
        tuple[int, int, bytes] for frame commands, otherwise the raw reply bytes.
    """
    if port is None:
        raise ScreenshotSizeError('The virtual-display bridge is not connected')
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as stream:
        stream.sendall(command)
        if command == b'FRAME\n':
            return read_rgb_frame(stream, recv=recv)
        return stream.recv(256)


def rgb_bytes_to_image(data, width, height):
    return np.frombuffer(data, np.uint8).reshape((height, width, 3)).copy()


def normalize_frame(image, width, height, rotation=0, configured_width=720, configured_height=1280):
    """
    Return the configured portrait canvas from any OEM virtual-display orientation.

    Args:
        image (np.ndarray): Raw RGB frame, shape (height, width, 3).
        width (int): Raw frame width reported by the bridge.
        height (int): Raw frame height reported by the bridge.
        rotation (int): Display rotation reported by the bridge INFO command.
        configured_width (int): Canonical canvas width.
        configured_height (int): Canonical canvas height.
    """
    rotation = int(rotation) % 4
    if (width, height) == (configured_width, configured_height):
        if rotation == 2:
            return np.rot90(image, 2).copy()
        return image
    if (width, height) == (configured_height, configured_width):
        # A secondary display on a landscape-native ROM may report the
        # dimensions transposed. Rotation 1/3 is authoritative; when the
        # ROM reports 0/2, use the same clockwise quarter-turn fallback.
        quarter_turn = rotation if rotation in (1, 3) else 1
        return np.rot90(image, quarter_turn).copy()
    raise ScreenshotSizeError(
        f'The virtual display returned unsupported size {width}x{height}; '
        f'expected {configured_width}x{configured_height}'
    )
