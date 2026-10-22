"""
Wire format between the automation worker and the OCR sidecar (module/ocr/sidecar.py).

Each message is a 4-byte big-endian header length, a UTF-8 JSON header, then
the raw bytes of every array listed in header['arrays'] back to back. No
pickle: the sidecar listens on loopback, where other local apps can connect.
"""
import json
import struct

import numpy as np

MAX_HEADER_BYTES = 1024 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
ALLOWED_DTYPES = ('uint8', 'float32')


class OcrProtocolError(Exception):
    pass


def _recv_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(min(size - len(data), 1024 * 1024))
        if not chunk:
            raise OcrProtocolError('OCR sidecar connection closed')
        data.extend(chunk)
    return bytes(data)


def send_message(stream, header, arrays=()):
    """
    Args:
        stream (socket.socket):
        header (dict): JSON-serializable message fields.
        arrays (list[np.ndarray]): Images sent after the header.
    """
    arrays = [np.ascontiguousarray(a) for a in arrays]
    for a in arrays:
        if a.dtype.name not in ALLOWED_DTYPES:
            raise OcrProtocolError(f'Unsupported image dtype {a.dtype}')
    header = dict(header, arrays=[{'shape': list(a.shape), 'dtype': a.dtype.name} for a in arrays])
    raw = json.dumps(header, ensure_ascii=False).encode('utf-8')
    stream.sendall(struct.pack('>I', len(raw)) + raw)
    for a in arrays:
        stream.sendall(memoryview(a).cast('B'))


def recv_message(stream):
    """
    Returns:
        tuple[dict, list[np.ndarray]]: header and arrays
    """
    (length,) = struct.unpack('>I', _recv_exact(stream, 4))
    if length > MAX_HEADER_BYTES:
        raise OcrProtocolError(f'OCR message header too large: {length}')
    header = json.loads(_recv_exact(stream, length).decode('utf-8'))
    arrays, total = [], 0
    for spec in header.get('arrays', []):
        dtype = spec.get('dtype')
        shape = [int(s) for s in spec.get('shape', [])]
        if dtype not in ALLOWED_DTYPES or not shape or any(s <= 0 for s in shape):
            raise OcrProtocolError(f'Invalid OCR array spec: {spec}')
        size = int(np.prod(shape)) * np.dtype(dtype).itemsize
        total += size
        if total > MAX_PAYLOAD_BYTES:
            raise OcrProtocolError('OCR message payload too large')
        arrays.append(np.frombuffer(_recv_exact(stream, size), dtype=dtype).reshape(shape))
    return header, arrays


def _plain(value):
    return value.tolist() if isinstance(value, np.ndarray) else value


def slim_pages(pages):
    """Keep the fields `Ocr._process_ocr_result` and `OcrModel.get_location` read from predict()."""
    return [
        {
            'rec_texts': list(page.get('rec_texts', [])),
            'rec_scores': [float(s) for s in page.get('rec_scores', [])],
            'rec_boxes': _plain(page.get('rec_boxes', [])),
        }
        for page in pages
    ]


def slim_lines(results):
    """Keep the fields arena/shop/warehouse OCR read from recognize()."""
    return [{'rec_text': r.get('rec_text', ''), 'rec_score': float(r.get('rec_score', 0))} for r in results]
