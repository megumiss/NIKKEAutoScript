import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from dev_tools.map_annotator import AnnotationStore, COORDINATES
from dev_tools.wiki_collectibles import (
    WikiClient, chapter_number, find_package, main, parse_article, parse_catalog, save_annotations, write_json,
)
from dev_tools.wiki_collectible_match import minimap_masks, player_center


def cell(text='', image=None):
    children = [{'text': text}]
    if image:
        children.append({'type': 'image', 'src': image})
    return {'type': 'table-cell', 'children': children}


class WikiParserTests(unittest.TestCase):
    def test_actual_catalog_categories_separate_difficulty_and_handle_null_children(self):
        data = [{'name': '地图收集（主线）', 'child': [
            {'name': '第三十八章', 'content_id': 657517, 'child': None}]},
            {'name': '地图收集（困难）', 'child': [
                {'name': '困难第三十八章', 'content_id': 671272}]},
            {'name': '活动收集', 'child': [{'name': '第一章', 'content_id': 999}]}]
        result = parse_catalog(data)
        self.assertEqual({(a['chapter'], a['difficulty'], a['article_id']) for a in result},
                         {(38, 'normal', 657517), (38, 'hard', 671272)})
        self.assertEqual(chapter_number('第十章'), 10)
        self.assertEqual(chapter_number('第四十八章'), 48)

    def test_structured_table_uses_explicit_numbers_and_images_not_thumbnails(self):
        rows = [[cell('序号'), cell('遗失物'), cell('区域图像')],
                [cell('2'), cell('芯尘\ufeff×20'), cell(image='//cdnimg-v2.gamekee.com/2.png')],
                [cell('7'), cell('Golden Tree'), cell(image='//cdnimg-v2.gamekee.com/7.png')]]
        content = [{'type': 'table', 'children': [{'type': 'table-row', 'children': row} for row in rows]}]
        result = parse_article({'editor_type': 1, 'content': json.dumps(content)})
        self.assertEqual([i['number'] for i in result['items']], [2, 7])
        self.assertEqual(result['items'][0]['name'], '芯尘×20')
        self.assertEqual(result['items'][0]['images'][0]['role'], 'area')

    def test_old_html_keeps_scene_and_map_images_in_same_item(self):
        content = '''<table><tr><td>遗失物</td><td>区域图像</td><td>地图图像</td></tr>
        <tr><td>宝石×50</td><td><img src="//cdnimg-v2.gamekee.com/a.png"></td>
        <td><img data-real="//cdnimg-v2.gamekee.com/b.png" src="placeholder"></td></tr></table>'''
        result = parse_article({'editor_type': 0, 'content': content})
        self.assertEqual(len(result['items']), 1)
        self.assertEqual([i['role'] for i in result['items'][0]['images']], ['area', 'map'])
        self.assertEqual(len(result['assets']), 2)

    def test_early_heading_article_does_not_count_two_images_as_two_items(self):
        content = '''<div>第一期招募大纲<div><img src="//cdnimg-v2.gamekee.com/a.png">
        <div><img src="//cdnimg-v2.gamekee.com/b.png"></div></div>
        <span>第二期招募大纲</span><img src="//cdnimg-v2.gamekee.com/c.png"></div>'''
        result = parse_article({'content': content, 'editor_type': 0})
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(len(result['items'][0]['images']), 2)

    def test_headerless_legacy_table_treats_numeric_title_as_item_name(self):
        content = '''<table><tr><td>01</td><td><img src="//cdnimg-v2.gamekee.com/a.png"></td>
        <td><img src="//cdnimg-v2.gamekee.com/b.png"></td></tr>
        <tr><td>The Calm Before the Storm</td><td><img src="//cdnimg-v2.gamekee.com/c.png"></td>
        <td><img src="//cdnimg-v2.gamekee.com/d.png"></td></tr></table>'''
        result = parse_article({'editor_type': 0, 'content': content})
        self.assertEqual([item['number'] for item in result['items']], [1, 2])
        self.assertEqual(result['items'][0]['name'], '01')

    def test_unknown_layout_never_claims_zero_items_as_complete(self):
        self.assertEqual(parse_article({'content': '<div>维护中</div>'})['items'], [])


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / 'chapter_38'
        self.package.mkdir()
        Image.new('RGB', (100, 80)).save(self.package / 'map.png')
        self.digest = hashlib.sha256((self.package / 'map.png').read_bytes()).hexdigest()
        write_json(self.package / 'map.json', {'chapter': 38, 'size': [100, 80],
                                             'coordinates': COORDINATES, 'image_sha256': self.digest})
        self.article = {'article_id': 657517, 'url': 'https://www.gamekee.com/nikke/657517.html',
                        'chapter': 38, 'difficulty': 'normal'}
        self.match = {'number': 1, 'name': 'Item', 'status': 'accepted', 'image_sha256': self.digest,
                      'road_iou': .92, 'roi': [1, 2, 30, 40], 'roi_to_map': [], 'player_center': [10, 10],
                      'normalized_size': [1920, 1080], 'position': [20, 30], 'image_url': 'https://example/image'}

    def test_shared_map_separate_difficulty_and_rerun_preserves_manual_edits(self):
        save_annotations(self.package, self.article, [self.match])
        store = AnnotationStore(self.root)
        loaded = store.load(self.package.name)
        document = loaded['annotations']
        document['objects'][0]['points'] = [[25, 35]]
        store.save(self.package.name, document, loaded['revision'])
        hard = {**self.article, 'difficulty': 'hard', 'article_id': 671272,
                'url': 'https://www.gamekee.com/nikke/671272.html'}
        save_annotations(self.package, hard, [{**self.match, 'position': [70, 60]}])
        before = (self.package / 'annotations.json').read_bytes()
        receipt = save_annotations(self.package, self.article, [self.match])
        self.assertEqual(receipt['preserved'], 1)
        self.assertEqual((self.package / 'annotations.json').read_bytes(), before)
        objects = store.load(self.package.name)['annotations']['objects']
        self.assertEqual([o['difficulty'] for o in objects], ['normal', 'hard'])
        self.assertEqual(objects[0]['points'], [[25, 35]])
        self.assertEqual(hashlib.sha256((self.package / 'map.png').read_bytes()).hexdigest(), self.digest)

    def test_hash_change_rejects_writes_and_uncertain_points_are_omitted(self):
        save_annotations(self.package, self.article, [{**self.match, 'status': 'needs_review'}])
        self.assertFalse((self.package / 'annotations.json').exists())
        with self.assertRaisesRegex(ValueError, 'changed after matching'):
            save_annotations(self.package, self.article, [{**self.match, 'image_sha256': 'stale'}])

    def test_surface_identity_is_preserved_with_wiki_point(self):
        save_annotations(self.package, self.article, [{**self.match, 'surface_id': 'surface_2'}])
        saved = AnnotationStore(self.root).load(self.package.name)['annotations']['objects'][0]
        self.assertEqual(saved['surface_id'], 'surface_2')
        self.assertEqual(saved['source']['surface_id'], 'surface_2')
        self.assertEqual(saved['points'], [[20, 30]])

    def test_ambiguous_chapter_packages_require_explicit_root(self):
        second = self.root / 'another' / 'chapter_38'
        second.mkdir(parents=True)
        write_json(second / 'map.json', {'chapter': 38})
        with self.assertRaisesRegex(ValueError, 'Multiple maps'):
            find_package([self.root], 38)
        self.assertEqual(find_package([self.package], 38), self.package)

    def test_failed_article_does_not_stop_next_difficulty(self):
        catalog = [{'name': '地图收集（主线）', 'child': [{'name': '第一章', 'content_id': 1}]},
                   {'name': '地图收集（困难）', 'child': [{'name': '困难第一章', 'content_id': 2}]}]
        with patch.object(WikiClient, 'api', return_value=catalog), patch(
                'dev_tools.wiki_collectibles.process_article', side_effect=[ValueError('bad article'),
                                                                          {'status': 'cached'}]) as process:
            code = main(['--cache', str(self.root / 'cache'), '--download-only'])
        self.assertEqual(code, 1)
        self.assertEqual(process.call_count, 2)
        results = json.loads((self.root / 'cache/progress.json').read_text(encoding='utf-8'))
        self.assertEqual([r['status'] for r in results], ['failed', 'cached'])

    def test_offline_cache_does_not_launch_browser_or_network(self):
        client = WikiClient(self.root, offline=True)
        self.addCleanup(client.close)
        write_json(self.root / 'cached.json', {'hello': 'world'})
        with patch.object(client.session, 'get') as request, patch.object(client, 'browser_get') as browser:
            self.assertIn(b'hello', client.get('https://www.gamekee.com/test', self.root / 'cached.json'))
            with self.assertRaises(FileNotFoundError):
                client.get('https://www.gamekee.com/test', self.root / 'missing.json')
            request.assert_not_called()
            browser.assert_not_called()


class ScreenshotTests(unittest.TestCase):
    def test_missing_ring_and_unsupported_layout_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Squad ring is absent'):
            player_center(np.zeros((211, 218, 3), np.uint8))
        with self.assertRaisesRegex(ValueError, 'Unsupported screenshot layout'):
            minimap_masks(np.zeros((512, 512, 3), np.uint8))

    def test_bright_grid_lines_do_not_become_roads(self):
        image = np.zeros((1080, 1920, 3), np.uint8)
        panel = image[96:307, 25:243]
        for y in range(5, 211, 12):
            cv2.line(panel, (0, y), (217, y), (190, 130, 50), 1)
        cv2.rectangle(panel, (145, 45), (195, 165), (190, 130, 50), -1)
        with patch('dev_tools.wiki_collectible_match.player_center', return_value=np.array([109., 102.])):
            _, road, _, _ = minimap_masks(image)
        self.assertEqual(np.count_nonzero(road[:, 45:70]), 0)
        self.assertGreater(np.count_nonzero(road[:, 150:190]), 3000)


if __name__ == '__main__':
    unittest.main()
