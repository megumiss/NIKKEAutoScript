import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen

from PIL import Image

from dev_tools.map_annotator import AnnotationStore, COORDINATES, make_server
from dev_tools.map_runtime import export_runtime
from dev_tools.map_scan import ScanJobs


class RuntimeMapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'capture' / 'chapter_01'
        (self.source / 'source').mkdir(parents=True)
        Image.new('RGB', (20, 20), '#3b8bba').save(self.source / 'map.png')
        self.metadata = dict(schema_version=1, chapter=1, image='map.png', coordinates=COORDINATES,
                             size=[20, 20], image_sha256=self.sha(self.source / 'map.png'))
        self.write_metadata()
        loaded = AnnotationStore(self.source.parent).load(self.source.name)
        (self.source / 'annotations.json').write_text(json.dumps(loaded['annotations']))
        (self.source / 'source/map_data.npz').write_bytes(b'flat cache')
        self.destination = self.root / 'runtime' / 'chapter_01'

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_metadata(self):
        (self.source / 'map.json').write_text(json.dumps(self.metadata))

    def layered(self):
        frame = self.source / 'source/frame_00000.png'
        frame.write_bytes((self.source / 'map.png').read_bytes())
        (self.source / 'depth').mkdir()
        (self.source / 'depth/frame_00000.npz').write_bytes(b'depth labels')
        (self.source / 'source/scan.json').write_text(json.dumps({'frames': [{'file': frame.name}]}))
        self.metadata.update(coordinate_model='local_parallax', source_sha256={frame.name: self.sha(frame)})
        self.write_metadata()

    def test_flat_export_preserves_bytes_and_excludes_capture_evidence(self):
        (self.source / 'reference.png').write_bytes((self.source / 'map.png').read_bytes())
        (self.source / 'source/frame_00000.png').write_bytes(b'raw capture')
        (self.source / 'report.json').write_text('{}')
        export_runtime(self.source, self.destination)
        names = {p.relative_to(self.destination).as_posix() for p in self.destination.rglob('*') if p.is_file()}
        self.assertEqual(names, {'map.png', 'map.json', 'annotations.json', 'source/map_data.npz'})
        for name in names:
            self.assertEqual(self.sha(self.destination / name), self.sha(self.source / name))
        self.assertTrue((self.source / 'report.json').is_file())
        self.assertFalse(AnnotationStore(self.destination.parent).load(self.destination.name)['reference'])

    def test_existing_destination_is_never_overwritten(self):
        self.destination.mkdir(parents=True)
        sentinel = self.destination / 'annotations.json'
        sentinel.write_bytes(b'local edits')
        with self.assertRaises(FileExistsError):
            export_runtime(self.source, self.destination)
        self.assertEqual(sentinel.read_bytes(), b'local edits')

    def test_new_capture_gets_empty_annotations_only_in_runtime_copy(self):
        (self.source / 'annotations.json').unlink()
        export_runtime(self.source, self.destination)
        self.assertFalse((self.source / 'annotations.json').exists())
        loaded = AnnotationStore(self.destination.parent).load(self.destination.name)
        self.assertEqual(loaded['annotations']['objects'], [])

    def test_layered_export_requires_complete_bound_reference_data(self):
        self.layered()
        export_runtime(self.source, self.destination)
        self.assertTrue((self.destination / 'source/frame_00000.png').is_file())
        self.assertTrue((self.destination / 'depth/frame_00000.npz').is_file())
        self.assertFalse((self.destination / 'source/map_data.npz').exists())
        (self.source / 'source/frame_00000.png').write_bytes(b'changed')
        other = self.destination.parent / 'bad'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            export_runtime(self.source, other)
        self.assertFalse(other.exists())

    def test_missing_depth_prevents_publication(self):
        self.layered()
        (self.source / 'depth/frame_00000.npz').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            export_runtime(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_partial_local_calibration_is_not_published(self):
        self.layered()
        calibration = self.source / 'movement_calibration/normal'
        calibration.mkdir(parents=True)
        (calibration / 'calibration.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Missing'):
            export_runtime(self.source, self.destination)
        self.assertFalse(self.destination.exists())

    def test_runtime_annotation_backups_are_written_to_local_directory(self):
        export_runtime(self.source, self.destination)
        with patch('dev_tools.map_paths.DEFAULT_MAPS_ROOT', self.destination.parent), patch(
                'dev_tools.map_paths.LOCAL_MAPS_ROOT', self.root / 'local'):
            store = AnnotationStore(self.destination.parent)
            loaded = store.load(self.destination.name)
            result = store.save(self.destination.name, loaded['annotations'], loaded['revision'])
        self.assertTrue(Path(result['backup']).is_relative_to(self.root / 'local'))
        self.assertTrue(Path(result['backup']).is_file())
        self.assertFalse((self.destination / '.annotation_backups').exists())

    def test_editor_scan_keeps_capture_local_and_exports_runtime_on_completion(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        store = AnnotationStore(runtime)
        process = Mock()
        process.poll.return_value = None
        with patch('dev_tools.map_scan.DEFAULT_MAPS_ROOT', runtime), patch(
                'dev_tools.map_scan.DEFAULT_CAPTURE_ROOT', self.root / 'local/captures'), patch(
                'dev_tools.map_scan.subprocess.Popen', return_value=process):
            jobs = ScanJobs(store)
            job = jobs.start(dict(chapter=1, process_3d=False))
            self.assertTrue(Path(job['output']).is_relative_to(self.root / 'local'))
            capture = Path(job['output']) / 'chapter_01'
            shutil.copytree(self.source, capture)
            process.poll.return_value = 0
            self.assertEqual(jobs.status()['state'], 'complete')
            self.assertEqual(jobs.status()['state'], 'complete')
        self.assertTrue((runtime / job['map_id'] / 'annotations.json').is_file())
        self.assertTrue((capture / 'source/map_data.npz').is_file())

    def test_runtime_terrain_preview_serves_the_local_export(self):
        export_runtime(self.source, self.destination)
        with patch('dev_tools.map_paths.DEFAULT_MAPS_ROOT', self.destination.parent), patch(
                'dev_tools.map_paths.LOCAL_MAPS_ROOT', self.root / 'local'):
            store = AnnotationStore(self.destination.parent)
            loaded = store.load(self.destination.name)
            loaded['annotations']['terrain_edits'] = [
                dict(type='brush', operation='add', width=1, points=[[4, 5]])]
            saved = store.save(self.destination.name, loaded['annotations'], loaded['revision'])
            exported = store.export_terrain(self.destination.name, saved['revision'])
            self.assertTrue(Path(exported['path']).is_relative_to(self.root / 'local'))
            server = make_server(store, 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = (f'http://127.0.0.1:{server.server_port}/api/export-image'
                       f'?map={self.destination.name}&export={exported["export"]}')
                with urlopen(url, timeout=3) as response:
                    self.assertEqual(response.read(), Path(exported['path']).read_bytes())
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()


if __name__ == '__main__':
    unittest.main()
