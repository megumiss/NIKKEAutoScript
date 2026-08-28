import unittest
import struct
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from module.device.adb.device import Device
from module.device.adb.method.adb import Adb
from module.device.adb.screenshot import Screenshot


class VirtualDisplayTests(unittest.TestCase):
    def test_parse_bridge_display_id(self):
        self.assertEqual(Device._bridge_display_id('NKAS_VD_READY id=19 socket=nkas-vd'), 19)
        self.assertIsNone(Device._bridge_display_id('unrelated output'))

    def test_parse_bridge_display_geometry(self):
        info = Device._bridge_display_info(
            'OK id=19 frames=4 size=1280x720 rotation=1'
        )
        self.assertEqual(info, {
            'id': 19, 'width': 1280, 'height': 720, 'rotation': 1,
        })

    def test_adb_input_targets_virtual_display(self):
        calls = []
        device = SimpleNamespace(_virtual_display_id=19, adb_shell=calls.append)
        Adb._adb_input(device, 'tap', 10, 20)
        self.assertEqual(calls, [['input', '-d', 19, 'tap', 10, 20]])

    def test_adb_input_transforms_transposed_virtual_display(self):
        calls = []
        device = SimpleNamespace(
            _virtual_display_id=19,
            _virtual_display_transform_point=lambda x, y: (1279 - y, x),
            adb_shell=calls.append,
        )
        Adb._adb_input(device, 'tap', 10, 20)
        self.assertEqual(calls, [['input', '-d', 19, 'tap', 1259, 10]])

    def test_current_app_is_read_from_target_display(self):
        output = (
            'Display #0 (activities from top to bottom):\n'
            '  ActivityRecord{a u0 com.android.launcher/.Launcher t1}\n'
            'Display #19 (activities from top to bottom):\n'
            '  ActivityRecord{b u0 com.proximabeta.nikke/.MainActivity t2}\n'
        )
        device = SimpleNamespace(_virtual_display_id=19, adb_shell=lambda command: output)
        current = Adb.app_current_adb.__wrapped__(device)
        self.assertEqual(current, 'com.proximabeta.nikke')

    def test_current_app_supports_android_display_header(self):
        output = (
            'Display: mDisplayId=9\n'
            '  topActivity=ComponentInfo{com.gamemobi.nikke/.MainActivity}\n'
            'Display: mDisplayId=0\n'
            '  topActivity=ComponentInfo{com.android.launcher/.Launcher}\n'
        )
        device = SimpleNamespace(_virtual_display_id=9, adb_shell=lambda command: output)
        current = Adb.app_current_adb.__wrapped__(device)
        self.assertEqual(current, 'com.gamemobi.nikke')

    def test_bridge_frame_protocol(self):
        payload = bytes(range(18))

        class FakeSocket:
            def __init__(self):
                self.data = bytearray(struct.pack('>III', 3, 2, len(payload)) + payload)
                self.sent = b''

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def sendall(self, data):
                self.sent += data

            def recv(self, size):
                chunk = bytes(self.data[:size])
                del self.data[:size]
                return chunk

        stream = FakeSocket()
        device = SimpleNamespace(
            _virtual_capture_port=12345,
            _recv_exact=lambda sock, size: Screenshot._recv_exact(sock, size),
        )
        with patch('socket.create_connection', return_value=stream):
            data = Screenshot._virtual_display_command(device, b'FRAME\n')
        self.assertEqual(data, (3, 2, payload))
        self.assertEqual(stream.sent, b'FRAME\n')

    def test_virtual_display_frame_keeps_rgb_channel_order(self):
        red_then_blue = bytes((255, 0, 0, 0, 0, 255))
        device = SimpleNamespace(
            _virtual_display_command=lambda command: (2, 1, red_then_blue),
            _virtual_display_width=2,
            _virtual_display_height=1,
            _virtual_display_rotation=0,
        )

        width, height, data = device._virtual_display_command(b'FRAME\n')
        image = Screenshot._normalize_virtual_display_image(
            device,
            np.frombuffer(data, np.uint8).reshape((height, width, 3)).copy(),
            width,
            height,
        )

        np.testing.assert_array_equal(
            image,
            np.array([[[255, 0, 0], [0, 0, 255]]], dtype=np.uint8),
        )

    def test_virtual_display_normalizes_transposed_frame(self):
        device = SimpleNamespace(
            _virtual_display_width=2,
            _virtual_display_height=3,
            _virtual_display_rotation=1,
        )
        raw = np.arange(3 * 2 * 3, dtype=np.uint8).reshape((2, 3, 3))
        image = Screenshot._normalize_virtual_display_image(device, raw, 3, 2)
        self.assertEqual(image.shape, (3, 2, 3))
        np.testing.assert_array_equal(image, np.rot90(raw, 1))

    def test_virtual_display_transform_point_matches_rotation(self):
        device = SimpleNamespace(
            _virtual_display_raw_width=1280,
            _virtual_display_raw_height=720,
            _virtual_display_rotation=1,
        )
        self.assertEqual(Screenshot._virtual_display_transform_point(device, 10, 20), (1259, 10))


if __name__ == '__main__':
    unittest.main()
