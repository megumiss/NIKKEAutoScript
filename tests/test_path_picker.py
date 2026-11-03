import asyncio
import unittest
from unittest.mock import patch

from module.webui.api import routes_system


class FakeRequest:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    async def json(self):
        if self.error:
            raise self.error
        return self.payload


def response_json(response):
    import json
    return json.loads(response.body.decode('utf-8'))


class PathPickerTests(unittest.TestCase):
    def run_picker(self, payload=None, error=None):
        return asyncio.run(routes_system.pick_path(FakeRequest(payload, error)))

    @patch('module.webui.api.routes_system._show_path_dialog', return_value='C:/NKAS/config.json')
    def test_file_request_is_normalized(self, dialog):
        response = self.run_picker({
            'mode': 'file', 'title': 'Pick', 'defaultPath': 'C:/NKAS',
            'accept': ['.json', '', 123],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response_json(response), {
            'ok': True, 'canceled': False, 'path': 'C:/NKAS/config.json', 'error': '',
        })
        self.assertEqual(dialog.call_args.args[0], {
            'mode': 'file', 'title': 'Pick', 'defaultPath': 'C:/NKAS',
            'accept': ['.json'],
        })

    @patch('module.webui.api.routes_system._show_path_dialog', return_value='C:/NKAS')
    def test_directory_mode_is_preserved(self, dialog):
        response = self.run_picker({'mode': 'directory'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dialog.call_args.args[0]['mode'], 'directory')

    @patch('module.webui.api.routes_system._show_path_dialog', return_value='')
    def test_cancel_returns_stable_shape(self, _dialog):
        response = self.run_picker({})
        self.assertEqual(response_json(response), {
            'ok': True, 'canceled': True, 'path': '', 'error': '',
        })

    @patch('module.webui.api.routes_system._show_path_dialog', side_effect=OSError('unavailable'))
    def test_native_dialog_failure_returns_503(self, _dialog):
        response = self.run_picker({})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response_json(response), {
            'ok': False,
            'canceled': False,
            'path': '',
            'error': 'File picker is not available on this host: unavailable',
        })

    @patch('module.webui.api.routes_system._show_path_dialog', return_value='')
    def test_invalid_json_and_invalid_fields_use_safe_defaults(self, dialog):
        response = self.run_picker(error=ValueError('bad json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dialog.call_args.args[0], {
            'mode': 'file', 'title': '', 'defaultPath': '', 'accept': [],
        })

    @patch('module.webui.api.routes_system._show_path_dialog', return_value='')
    def test_non_object_json_uses_safe_defaults(self, dialog):
        response = self.run_picker(['unexpected'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dialog.call_args.args[0]['mode'], 'file')

    @patch('module.webui.api.routes_system._show_path_dialog', return_value='')
    def test_invalid_field_types_are_ignored(self, dialog):
        response = self.run_picker({
            'mode': 'invalid', 'title': 123, 'defaultPath': [], 'accept': ['.json', 123, ' '],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(dialog.call_args.args[0], {
            'mode': 'file', 'title': '', 'defaultPath': '', 'accept': ['.json'],
        })


if __name__ == '__main__':
    unittest.main()
