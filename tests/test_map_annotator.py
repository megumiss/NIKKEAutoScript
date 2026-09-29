import copy
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

from dev_tools.map_annotator import AnnotationStore, ConflictError, COORDINATES, make_server, validate_annotations


class MapFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'chapter_01'
        self.package.mkdir()
        Image.new('RGB', (320, 200), '#20313e').save(self.package / 'map.png')
        Image.new('RGB', (320, 200), '#324553').save(self.package / 'reference.png')
        self.image_hash = hashlib.sha256((self.package / 'map.png').read_bytes()).hexdigest()
        self.metadata = {'chapter': 1, 'image_sha256': self.image_hash, 'size': [320, 200],
                         'coordinates': COORDINATES, 'transforms': {'origin': [40, 80]}}
        (self.package / 'map.json').write_text(json.dumps(self.metadata))
        self.store = AnnotationStore(self.root)

    def document(self):
        data = self.store.load('chapter_01')
        document = data['annotations']
        document['objects'] = [
            {'id': 'a', 'type': 'point', 'label': '路口', 'points': [[30.5, 50.2]], 'color': '#ffb454'},
            {'id': 'b', 'type': 'polyline', 'label': '道路', 'points': [[50, 80], [80, 80], [80, 140]]},
            {'id': 'c', 'type': 'polygon', 'label': '障碍', 'points': [[150, 80], [190, 80], [190, 120], [150, 120]]},
        ]
        document['connections'] = [{'id': 'edge', 'from': 'a', 'to': 'b', 'directed': True}]
        return data


class AnnotationTests(MapFixture, unittest.TestCase):
    def test_roundtrip_all_types_preserves_source_and_additional_fields(self):
        loaded = self.document()
        loaded['annotations']['objects'][0]['note'] = '战斗后开放'
        loaded['annotations']['custom'] = {'preserve': True}
        metadata_bytes = (self.package / 'map.json').read_bytes()
        image_bytes = (self.package / 'map.png').read_bytes()
        self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        saved = self.store.load('chapter_01')
        self.assertEqual(saved['annotations'], loaded['annotations'])
        self.assertEqual(metadata_bytes, (self.package / 'map.json').read_bytes())
        self.assertEqual(image_bytes, (self.package / 'map.png').read_bytes())
        self.assertNotEqual(saved['revision'], loaded['revision'])

    def test_backup_and_concurrent_save_conflict(self):
        loaded = self.document()
        result = self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        first = (self.package / 'annotations.json').read_bytes()
        loaded['annotations']['objects'][0]['label'] = '已修改'
        saved = self.store.save('chapter_01', loaded['annotations'], result['revision'])
        self.assertEqual(Path(saved['backup']).read_bytes(), first)
        with self.assertRaises(ConflictError):
            self.store.save('chapter_01', loaded['annotations'], result['revision'])
        self.assertEqual(len(list((self.package / '.annotation_backups').iterdir())), 1)

    def test_changed_image_or_annotation_binding_is_rejected(self):
        loaded = self.document()
        loaded['annotations']['image_sha256'] = 'different'
        with self.assertRaises(ConflictError):
            self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        Image.new('RGB', (320, 200), 'red').save(self.package / 'map.png')
        with self.assertRaises(ConflictError):
            self.store.load('chapter_01')
        self.assertFalse((self.package / 'annotations.json').exists())

    def test_reference_must_share_geometry(self):
        Image.new('RGB', (160, 100)).save(self.package / 'reference.png')
        with self.assertRaisesRegex(ValueError, '尺寸不同'):
            self.store.load('chapter_01')

    def test_invalid_geometry_ids_and_connections_are_rejected(self):
        original = self.document()['annotations']
        invalid = []
        for coordinate in [-1, 320, float('nan'), float('inf'), True, '10']:
            document = copy.deepcopy(original)
            document['objects'][0]['points'][0][0] = coordinate
            invalid.append(document)
        for field, value in [('type', 'circle'), ('points', [[1, 1], [1, 1]]), ('color', 'red')]:
            document = copy.deepcopy(original)
            document['objects'][1][field] = value
            invalid.append(document)
        for field, value in [('from', 'missing'), ('to', 'a'), ('directed', 'yes'), ('id', 'a')]:
            document = copy.deepcopy(original)
            document['connections'][0][field] = value
            invalid.append(document)
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(ValueError):
                validate_annotations(document, self.image_hash, [320, 200])

    def test_failed_replace_keeps_existing_annotation_and_removes_temp_file(self):
        loaded = self.document()
        first = self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        before = (self.package / 'annotations.json').read_bytes()
        loaded['annotations']['objects'] = []
        loaded['annotations']['connections'] = []
        with patch('dev_tools.map_annotator.os.replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.store.save('chapter_01', loaded['annotations'], first['revision'])
        self.assertEqual((self.package / 'annotations.json').read_bytes(), before)
        self.assertEqual(list(self.package.glob('*.tmp')), [])

    def test_package_cannot_escape_root(self):
        with self.assertRaises(ValueError):
            self.store.package('../outside')
        self.assertEqual(self.store.catalog()[0]['id'], 'chapter_01')


class HTTPTests(MapFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = make_server(self.store, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()

    def request(self, path, data=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Annotation-Token'] = token
        raw = json.dumps(data).encode() if data is not None else None
        with urlopen(Request(self.url + path, data=raw, headers=headers), timeout=5) as response:
            return response.read()

    def test_http_open_save_and_conflict(self):
        catalog = json.loads(self.request('/api/maps'))
        self.assertEqual(catalog['maps'][0]['id'], 'chapter_01')
        loaded = json.loads(self.request('/api/map?map=chapter_01'))
        payload = {'id': loaded['id'], 'revision': loaded['revision'], 'annotations': loaded['annotations']}
        with self.assertRaises(HTTPError) as error:
            self.request('/api/save', payload)
        self.assertEqual(error.exception.code, 403)
        saved = json.loads(self.request('/api/save', payload, catalog['token']))
        self.assertIn('revision', saved)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/save', payload, catalog['token'])
        self.assertEqual(error.exception.code, 409)
        self.assertEqual(self.request('/api/image?map=chapter_01&image=map.png'),
                         (self.package / 'map.png').read_bytes())
        self.assertIn(b'editor.js', self.request('/'))

    def test_rejects_unexpected_host_and_image_path(self):
        request = Request(self.url + '/api/maps', headers={'Host': 'unrelated.example'})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.request('/api/image?map=chapter_01&image=../map.json')
        self.assertEqual(error.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
