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
from dev_tools.map_terrain import render_terrain


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
    def test_single_pixel_brush_exports_a_pixel_at_canvas_edge(self):
        edited, mask = render_terrain(Image.new('RGB', (4, 4)),
                                      [{'type': 'brush', 'operation': 'add', 'width': 1, 'points': [[0, 0]]}],
                                      {'add': '#3b8bba', 'erase': '#1c232c'})
        self.assertEqual(mask.getpixel((0, 0)), 2)
        self.assertEqual(mask.getpixel((1, 0)), 0)
        self.assertEqual(edited.getpixel((0, 0)), (59, 139, 186))

    def test_layered_grid_pixels_open_as_raster_coordinates_without_rewriting_metadata(self):
        self.metadata.update(coordinate_model='local_parallax',
                             coordinates={**COORDINATES, 'unit': 'rectified_grid_pixel'})
        (self.package / 'map.json').write_text(json.dumps(self.metadata))
        previous = (self.package / 'map.json').read_bytes()
        loaded = self.store.load('chapter_01')
        self.assertEqual(loaded['annotations']['coordinates'], COORDINATES)
        self.assertEqual(loaded['terrain_colors'], {'add': '#3b8bba', 'erase': '#1c232c'})
        self.assertEqual((self.package / 'map.json').read_bytes(), previous)

    def test_road_edits_roundtrip_export_and_original_preservation(self):
        loaded = self.document()
        original = (self.package / 'map.png').read_bytes()
        metadata = (self.package / 'map.json').read_bytes()
        loaded['annotations']['terrain_edits'] = [
            {'type': 'brush', 'operation': 'add', 'width': 20, 'points': [[20, 40], [100, 40]]},
            {'type': 'brush', 'operation': 'erase', 'width': 8, 'points': [[60, 40]]},
            {'type': 'polygon', 'operation': 'add', 'points': [[150, 60], [200, 60], [200, 100]]},
        ]
        saved = self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        self.assertEqual(self.store.load('chapter_01')['annotations'], loaded['annotations'])
        export = self.store.export_terrain('chapter_01', saved['revision'])
        with Image.open(export['path']) as edited:
            self.assertEqual(edited.getpixel((30, 40)), (45, 141, 199))
            self.assertEqual(edited.getpixel((60, 40)), (15, 20, 28))
            self.assertEqual(edited.getpixel((185, 70)), (45, 141, 199))
            self.assertEqual(edited.getpixel((10, 10)), (32, 49, 62))
        with Image.open(Path(export['path']).with_name('terrain_override.png')) as mask:
            self.assertEqual([mask.getpixel(p) for p in [(10, 10), (60, 40), (30, 40)]], [0, 1, 2])
        self.assertEqual((self.package / 'map.png').read_bytes(), original)
        self.assertEqual((self.package / 'map.json').read_bytes(), metadata)
        with self.assertRaises(ConflictError):
            self.store.export_terrain('chapter_01', loaded['revision'])

    def test_invalid_road_edits_are_rejected_before_save(self):
        loaded = self.document()
        valid = {'type': 'brush', 'operation': 'add', 'width': 12, 'points': [[20, 40]]}
        invalid = [dict(valid, operation='unknown'), dict(valid, width=True), dict(valid, width=161),
                   dict(valid, points=[[float('nan'), 40]]), dict(valid, points=[[-1, 40]]),
                   dict(valid, type='polygon'), dict(valid, points=[[320, 40]])]
        for edit in invalid:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                loaded['annotations']['terrain_edits'] = [edit]
                self.store.save('chapter_01', loaded['annotations'], loaded['revision'])
        self.assertFalse((self.package / 'annotations.json').exists())

    def test_categories_and_elevator_connections_roundtrip(self):
        loaded = self.document()
        document = loaded['annotations']
        document['objects'] = [
            {'id': 'normal', 'type': 'point', 'category': 'normal_collectible',
             'difficulty': 'normal', 'points': [[20, 30]], 'label': '普通收集品'},
            {'id': 'hard', 'type': 'point', 'category': 'hard_collectible',
             'difficulty': 'hard', 'points': [[20, 30]], 'label': '困难收集品'},
            {'id': 'switch', 'type': 'polygon', 'category': 'ground_mechanism',
             'points': [[40, 40], [60, 40], [60, 60]]},
            {'id': 'a', 'type': 'point', 'category': 'ground_elevator', 'points': [[70, 80]]},
            {'id': 'b', 'type': 'point', 'category': 'ground_elevator', 'points': [[90, 80]]},
        ]
        document['connections'][0]['category'] = 'elevator_connection'
        self.store.save('chapter_01', document, loaded['revision'])
        self.assertEqual(self.store.load('chapter_01')['annotations'], document)

    def test_invalid_categories_difficulty_and_elevator_endpoints_are_rejected(self):
        original = self.document()['annotations']
        invalid = []
        for category in ['unknown', 'elevator_connection', None, []]:
            document = copy.deepcopy(original)
            document['objects'][0]['category'] = category
            invalid.append(document)
        for difficulty in [None, 'hard']:
            document = copy.deepcopy(original)
            document['objects'][0].update(category='normal_collectible', difficulty=difficulty)
            invalid.append(document)
        for category in ['ground_mechanism', 'elevator_connection']:
            document = copy.deepcopy(original)
            document['connections'][0]['category'] = category
            document['objects'][0]['category'] = 'ground_elevator'
            invalid.append(document)
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(ValueError):
                validate_annotations(document, self.image_hash, [320, 200])

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

    def test_movement_endpoints_require_token_and_forward_target_without_scaling(self):
        catalog = json.loads(self.request('/api/maps'))
        self.assertEqual(json.loads(self.request('/api/movement'))['state'], 'idle')
        self.assertIn(b'createMovementController', self.request('/movement.js'))
        payload = {'id': 'chapter_01', 'target': [37.25, 85.5], 'difficulty': 'normal'}
        with patch('dev_tools.map_annotator.MovementJobs.start', return_value={'id': 'job'}) as start:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/movement/start', payload)
            self.assertEqual(error.exception.code, 403)
            start.assert_not_called()
            self.assertEqual(json.loads(self.request('/api/movement/start', payload, catalog['token'])), {'id': 'job'})
            start.assert_called_once_with(payload)
        with patch('dev_tools.map_annotator.MovementJobs.stop', return_value={'state': 'stopping'}) as stop:
            self.request('/api/movement/stop', {'job': 'job'}, catalog['token'])
            stop.assert_called_once_with('job')

    def test_scan_api_token_and_device_exclusion(self):
        token = json.loads(self.request('/api/maps'))['token']
        payload = {'chapter': 40, 'process_3d': True, 'stroke_px': 120}
        self.assertEqual(json.loads(self.request('/api/scan'))['state'], 'idle')
        self.assertIn(b'createScanController', self.request('/scan.js'))
        with patch('dev_tools.map_annotator.ScanJobs.start', return_value={'id': 'scan'}) as start:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/scan/start', payload)
            self.assertEqual(error.exception.code, 403)
            start.assert_not_called()
            with patch('dev_tools.map_annotator.MovementJobs.status', return_value={'running': True}):
                with self.assertRaises(HTTPError) as error:
                    self.request('/api/scan/start', payload, token)
                self.assertEqual(error.exception.code, 400)
                start.assert_not_called()
            self.assertEqual(json.loads(self.request('/api/scan/start', payload, token)), {'id': 'scan'})
            start.assert_called_once_with(payload)
        with patch('dev_tools.map_annotator.ScanJobs.status', return_value={'running': True}), patch(
                'dev_tools.map_annotator.MovementJobs.start') as move:
            with self.assertRaises(HTTPError) as error:
                self.request('/api/movement/start', {'id': 'chapter_01', 'target': [20, 30]}, token)
            self.assertEqual(error.exception.code, 400)
            move.assert_not_called()
        with patch('dev_tools.map_annotator.ScanJobs.stop', return_value={'state': 'stopping'}) as stop:
            self.request('/api/scan/stop', {'job': 'scan'}, token)
            stop.assert_called_once_with('scan')


if __name__ == '__main__':
    unittest.main()
