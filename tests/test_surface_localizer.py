"""用未入库的平移视角验证表面定位与正式验收门槛。"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from module.campaign_prototype.surface_localizer import SurfaceLocalizer


class SurfaceLocalizerTests(unittest.TestCase):
    def test_unseen_camera_view_localizes_but_unverified_geometry_stays_review(self):
        rng = np.random.default_rng(7)
        texture = rng.integers(0, 101, (300, 300, 1), dtype=np.uint8)
        texture = texture + np.uint8([145, 85, 25])
        cameras = [[0, 0], [8, 4], [16, 8]]
        with tempfile.TemporaryDirectory() as folder:
            package = Path(folder)
            source = package / 'source'
            source.mkdir()
            hashes, frames = {}, []
            for index, (x, y) in enumerate(cameras):
                name = f'frame_{index:05}.png'
                cv2.imwrite(str(source / name), texture[y:y + 200, x:x + 200])
                hashes[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
                frames.append({'file': name})
            (source / 'scan.json').write_text(json.dumps({'frames': frames}))
            cv2.imwrite(str(package / 'map.png'), texture)
            np.savez_compressed(source / 'surface_map.npz', masks=np.ones((1, 300, 300), bool),
                                roi_surface_id=np.zeros((3, 200, 200), np.int16))
            metadata = {'coordinate_model': 'orthographic_surfaces', 'size': [300, 300],
                        'image_sha256': hashlib.sha256((package / 'map.png').read_bytes()).hexdigest(),
                        'geometry_data': 'source/surface_map.npz',
                        'geometry_sha256': hashlib.sha256((source / 'surface_map.npz').read_bytes()).hexdigest(),
                        'projection': np.eye(3).tolist(), 'vertical_origin': [0., 0.], 'camera': cameras,
                        'world_to_map': np.eye(3).tolist(), 'surfaces': [{'id': 'ground', 'height_plane': [0, 0, 0]}],
                        'texture_frames': [0, 1, 2], 'source_sha256': hashes,
                        'validation': {'independent_geometry_verified': False, 'surface_identity_verified': False}}
            (package / 'map.json').write_text(json.dumps(metadata))
            localizer = SurfaceLocalizer(package)
            image = texture[13:213, 25:225].copy()
            result = localizer.locate(image, [100., 100.])
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(result['surface_id'], 'ground')
            np.testing.assert_allclose(result['position'], [125., 113.], atol=.25)
            localizer.metadata['validation']['independent_geometry_verified'] = True
            self.assertEqual(localizer.locate(image, [100., 100.])['status'], 'needs_review')
            localizer.metadata['validation']['surface_identity_verified'] = True
            self.assertEqual(localizer.locate(image, [100., 100.])['status'], 'accepted')
            with self.assertRaisesRegex(ValueError, 'features'):
                localizer.locate(np.zeros_like(image), [100., 100.])


if __name__ == '__main__':
    unittest.main()
