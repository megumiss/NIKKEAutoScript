"""Download normal/hard Wiki collectibles and register them on shared chapter maps in one resumable batch."""

import argparse
import copy
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import cv2
import numpy as np
import requests
from bs4 import BeautifulSoup

if __package__:
    from .map_annotator import AnnotationStore
    from .wiki_collectible_match import MATCH_VERSION, MapMatcher
else:
    from map_annotator import AnnotationStore
    from wiki_collectible_match import MATCH_VERSION, MapMatcher


BASE = 'https://www.gamekee.com'
TREE_URL = BASE + '/v1/entry/getEntryTreeById?id=183583'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def clean(text):
    return re.sub(r'\s+', ' ', text.replace('\ufeff', '').replace('\\r', ' ').replace('\\n', ' ')).strip()


def public_url(url):
    result = urljoin(BASE + '/nikke/', url)
    parts = urlsplit(result)
    if parts.scheme != 'https' or not (parts.hostname or '').endswith('.gamekee.com'):
        raise ValueError(f'Unexpected Wiki resource URL: {url}')
    return result


def chapter_number(title):
    found = re.search(r'第([一二三四五六七八九十百零〇\d]+)章', title)
    if not found:
        raise ValueError(f'Unrecognized chapter title: {title}')
    number = found[1]
    if number.isdecimal():
        return int(number)
    digits = {c: i for i, c in enumerate('零一二三四五六七八九')}
    if '十' in number:
        tens, ones = number.split('十')
        return (digits[tens] if tens else 1) * 10 + (digits[ones] if ones else 0)
    return digits[number]


def walk(nodes):
    for node in nodes or []:
        yield node
        yield from walk(node.get('children') or node.get('child'))


def parse_catalog(data):
    articles = []
    for category in walk(data):
        name = category.get('name', '')
        difficulty = {'地图收集（主线）': 'normal', '地图收集（困难）': 'hard'}.get(name)
        if difficulty is None:
            continue
        for node in walk(category.get('child')):
            if not node.get('content_id'):
                continue
            articles.append({'chapter': chapter_number(node['name']), 'difficulty': difficulty,
                             'article_id': node['content_id'], 'title': node['name'],
                             'url': f"{BASE}/nikke/{node['content_id']}.html"})
    keys = [(r['chapter'], r['difficulty']) for r in articles]
    if not articles or len(keys) != len(set(keys)):
        raise ValueError('Wiki chapter catalog is empty or has duplicate chapter/difficulty entries.')
    return sorted(articles, key=lambda r: (r['chapter'], r['difficulty']))


def parse_article(document):
    content = document.get('content', '')
    rows = []
    assets = []
    if document.get('editor_type') == 1:
        nodes = json.loads(content) if isinstance(content, str) else content
        for node in walk(nodes):
            if node.get('type') == 'image' and node.get('src'):
                assets.append(public_url(node['src']))
            if node.get('type') != 'table-row':
                continue
            cells = []
            for cell in node.get('children', []):
                descendants = list(walk([cell]))
                cells.append({'text': clean(' '.join(n.get('text', '') for n in descendants)),
                              'images': [public_url(n['src']) for n in descendants
                                         if n.get('type') == 'image' and n.get('src')]})
            rows.append(cells)
    else:
        soup = BeautifulSoup(content, 'html.parser')

        def images(element):
            return [public_url(img.get('data-real') or img.get('src')) for img in element.select('img')
                    if img.get('data-real') or img.get('src')]

        assets = images(soup)
        for row in soup.select('tr'):
            rows.append([{'text': clean(cell.get_text(' ', strip=True)), 'images': images(cell)}
                         for cell in row.find_all(['td', 'th'], recursive=False)])
        if not rows:
            # Early articles use successive text headings and pairs of scene/map images.
            heading = ''
            groups = []
            for node in soup.descendants:
                if getattr(node, 'name', None) == 'img':
                    url = node.get('data-real') or node.get('src')
                    if url:
                        if not groups or groups[-1]['name'] != heading:
                            groups.append({'name': heading, 'images': []})
                        groups[-1]['images'].append(public_url(url))
                elif isinstance(node, str) and clean(node):
                    heading = clean(node)
            items = [{'number': i + 1, 'name': g['name'],
                      'images': [{'url': u, 'role': 'unclassified'} for u in g['images']]}
                     for i, g in enumerate(groups) if g['name']]
            return {'items': items, 'assets': list(dict.fromkeys(assets)), 'layout': 'heading_images'}
    items = []
    headers = []
    for cells in rows:
        texts = [c['text'] for c in cells]
        if any('遗失物' in t or '收集物' in t for t in texts) and any('图像' in t for t in texts):
            headers = texts
            continue
        if (not headers and len(cells) == 3 and texts[0] and not cells[0]['images']
                and cells[1]['images'] and cells[2]['images']):
            headers = ['遗失物', '区域图像', '地图图像']
        if not headers or not any(c['images'] for c in cells):
            continue
        name_index = next((i for i, t in enumerate(headers) if '遗失物' in t or '收集物' in t), None)
        if name_index is None or name_index >= len(cells) or not cells[name_index]['text']:
            continue
        number_index = next((i for i, t in enumerate(headers) if '序号' in t), None)
        number = len(items) + 1
        if number_index is not None:
            if number_index >= len(texts) or not texts[number_index].isdecimal():
                raise ValueError(f'Invalid item number in Wiki row: {texts}')
            number = int(texts[number_index])
        item_images = []
        for i, cell in enumerate(cells):
            header = headers[i] if i < len(headers) else ''
            role = 'area' if '区域' in header else 'map' if '地图' in header else 'other'
            item_images.extend({'url': url, 'role': role} for url in cell['images'])
        items.append({'number': number, 'name': cells[name_index]['text'], 'images': item_images})
    if len({i['number'] for i in items}) != len(items):
        raise ValueError('Duplicate collectible numbers in article; manual review required.')
    return {'items': items, 'assets': list(dict.fromkeys(assets)), 'layout': 'table'}


class WikiClient:
    def __init__(self, cache, offline=False, refresh=False, retries=2):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.offline, self.refresh, self.retries = offline, refresh, retries
        self.session = requests.Session()
        self.session.headers.update({'game-alias': 'nikke', 'Referer': BASE + '/nikke/',
                                     'User-Agent': 'Mozilla/5.0'})
        self.browser_hosts = set()
        self.playwright = self.browser = self.page = None

    def browser_get(self, url):
        if self.page is None:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(channel='msedge', headless=True)
            self.page = self.browser.new_page()
        article = re.search(r'/content/(\d+)\.json', url)
        if article:
            with self.page.expect_response(lambda r: f'/content/{article[1]}.json' in r.url, timeout=45000) as pending:
                self.page.goto(f'{BASE}/nikke/{article[1]}.html', wait_until='domcontentloaded', timeout=45000)
            response = pending.value
        else:
            if not self.page.url.startswith(BASE + '/nikke/'):
                self.page.goto(BASE + '/nikke/', wait_until='domcontentloaded', timeout=45000)
            with self.page.expect_response(lambda r: r.url == url, timeout=45000) as pending:
                self.page.evaluate('''url => {
                    window.wikiDownload = new Image();
                    window.wikiDownload.src = url;
                }''', url)
            response = pending.value
        if response is None or not response.ok:
            raise RuntimeError(f'Browser download failed: {url}, status {response.status if response else None}')
        return response.body()

    def get(self, url, path, image=False):
        path = Path(path)
        if path.exists() and (self.offline or not self.refresh):
            body = path.read_bytes()
            try:
                self.validate(body, image)
                return body
            except ValueError:
                if self.offline:
                    raise
        if self.offline:
            raise FileNotFoundError(f'Offline cache missing: {path}')
        error = None
        for attempt in range(self.retries + 1):
            try:
                host = urlsplit(url).hostname
                if host in self.browser_hosts:
                    body = self.browser_get(url)
                else:
                    response = self.session.get(url, timeout=(10, 40))
                    if response.status_code in (403, 567):
                        self.browser_hosts.add(host)
                        body = self.browser_get(url)
                    else:
                        response.raise_for_status()
                        body = response.content
                self.validate(body, image)
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.part')
                temporary.write_bytes(body)
                temporary.replace(path)
                return body
            except Exception as exc:
                error = exc
                if attempt < self.retries:
                    time.sleep(min(2 ** attempt, 4))
        raise RuntimeError(f'Download failed after {self.retries + 1} attempts: {url}: {error}') from error

    @staticmethod
    def validate(body, image):
        if image:
            if cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR) is None:
                raise ValueError('Response is not a decodable image.')
        else:
            document = json.loads(body)
            if isinstance(document, dict) and 'code' in document and document['code'] != 0:
                raise ValueError(f"Wiki API failed: {document.get('msg')}")

    def api(self, url, path):
        payload = json.loads(self.get(url, path))
        if payload.get('code') != 0:
            raise ValueError(f'Wiki API failed: {payload.get("msg")}')
        return payload['data']

    def close(self):
        try:
            if self.browser is not None:
                self.browser.close()
        finally:
            if self.playwright is not None:
                self.playwright.stop()
            self.session.close()


def find_package(roots, chapter):
    for root in roots:
        matches = []
        for path in sorted(root.rglob('map.json')):
            if 'attempts' in path.parts:
                continue
            try:
                meta = json.loads(path.read_text(encoding='utf-8'))
                if meta.get('chapter') == chapter:
                    matches.append(path.parent)
            except (OSError, ValueError):
                continue
        if len(matches) > 1:
            raise ValueError(f'Multiple maps for chapter {chapter} under {root}; select a single collection root.')
        if matches:
            return matches[0]
    return None


def save_annotations(package, article, matches, update=False):
    store = AnnotationStore(package.parent)
    loaded = store.load(package.name)
    document = copy.deepcopy(loaded['annotations'])
    by_id = {obj['id']: obj for obj in document['objects']}
    added = updated = preserved = 0
    for item in matches:
        identifier = f"wiki_{article['article_id']}_{item['number']:02d}"
        difficulty = article['difficulty']
        if identifier in by_id and by_id[identifier].get('source', {}).get('url') == article['url']:
            old = by_id[identifier]
            old.setdefault('difficulty', difficulty)
            old['source'].setdefault('difficulty', difficulty)
            if old.get('label') == f"收集物 {item['number']:02d}":
                old['label'] = f"{'普通' if difficulty == 'normal' else '困难'}收集物 {item['number']:02d}"
        if item.get('status') != 'accepted':
            preserved += int(identifier in by_id)
            continue
        if item['image_sha256'] != document['image_sha256']:
            raise ValueError('Map changed after matching; recompute positions.')
        source = {key: item[key] for key in ('number', 'road_iou', 'roi', 'roi_to_map', 'player_center')}
        source.update(url=article['url'], article_id=article['article_id'], difficulty=difficulty,
                      author=article.get('author', ''), article_updated=article.get('updated_at'),
                      image_url=item['image_url'], item_name=item['name'], importer='wiki_collectibles',
                      position_basis='squad_marker_center', normalized_size=item['normalized_size'])
        obj = {'id': identifier, 'type': 'point', 'difficulty': difficulty,
               'label': f"{'普通' if difficulty == 'normal' else '困难'}收集物 {item['number']:02d}",
               'points': [[round(v, 1) for v in item['position']]],
               'color': '#ffd166' if difficulty == 'normal' else '#c792ea',
               'note': f"{item['name']}\n按 Wiki 小队圆环中心配准。\n{article['url']}", 'source': source}
        if identifier in by_id:
            old = by_id[identifier]
            if old.get('source', {}).get('url') != article['url']:
                raise ValueError(f'Annotation ID collision with another source: {identifier}')
            if update:
                old.clear()
                old.update(obj)
                updated += 1
            else:
                preserved += 1
        else:
            document['objects'].append(obj)
            by_id[identifier] = obj
            added += 1
    receipt = {'added': added, 'updated': updated, 'preserved': preserved}
    if document != loaded['annotations']:
        receipt.update(store.save(package.name, document, loaded['revision']))
    return receipt


def process_article(client, article, roots, options, matchers):
    folder = client.cache / f"chapter_{article['chapter']:02d}" / article['difficulty']
    folder.mkdir(parents=True, exist_ok=True)
    detail = client.api(f"{BASE}/v1/content/detail/{article['article_id']}", folder / 'detail.json')
    content_url = detail.get('content_cdn')
    if content_url:
        document = json.loads(client.get(public_url(content_url), folder / 'article.json'))
    else:
        document = {'content': detail.get('content_json') or detail.get('content', ''),
                    'editor_type': detail.get('editor_type', 0)}
        write_json(folder / 'article.json', document)
    parsed = parse_article(document)
    article = {**article, 'author': (detail.get('user') or {}).get('username', ''),
               'updated_at': detail.get('updated_at')}
    downloaded = {}
    failures = []
    for url in parsed['assets']:
        name = hashlib.sha256(url.encode()).hexdigest()[:24] + '.img'
        path = folder / 'images' / name
        try:
            client.get(url, path, image=True)
            downloaded[url] = path
        except Exception as exc:
            failures.append({'url': url, 'error': str(exc)})
    manifest = {**article, **parsed, 'downloaded': {u: str(p.relative_to(folder)) for u, p in downloaded.items()},
                'download_failures': failures}
    write_json(folder / 'items.json', manifest)
    record = {**article, 'items': len(parsed['items']), 'images': len(downloaded), 'download_failures': failures}
    if options.download_only:
        return {**record, 'status': 'cached' if parsed['items'] and not failures else 'needs_review'}
    package = find_package(roots, article['chapter'])
    if package is None:
        return {**record, 'status': 'missing_map'}
    if package not in matchers:
        matchers[package] = MapMatcher(package)
    matcher = matchers[package]
    matches = []
    for item in parsed['items']:
        result = {**item, 'status': 'needs_review', 'errors': []}
        accepted = []
        for screenshot in item['images']:
            url = screenshot['url']
            path = downloaded.get(url)
            if path is None:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            key = f'{digest}-{matcher.digest}-{matcher.cache_digest}-v{MATCH_VERSION}'
            saved = folder / 'matches' / (hashlib.sha256(key.encode()).hexdigest() + '.json')
            try:
                if saved.exists():
                    match = json.loads(saved.read_text(encoding='utf-8'))
                else:
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
                    match = matcher.match(image, review=saved.with_suffix('.jpg'))
                    write_json(saved, match)
                if match['status'] == 'accepted':
                    accepted.append({**match, 'image_url': url})
                else:
                    result['errors'].append({'image_url': url, **match})
            except Exception as exc:
                result['errors'].append({'image_url': url, 'error': f'{type(exc).__name__}: {exc}'})
        if accepted:
            best = max(accepted, key=lambda r: r['road_iou'])
            spread = max(np.linalg.norm(np.asarray(r['position']) - best['position']) for r in accepted)
            result.update(best)
            if spread > 20:
                result.update(status='needs_review', reason='Screenshots disagree on the item position.')
        matches.append(result)
        print(json.dumps({'chapter': article['chapter'], 'difficulty': article['difficulty'],
                          'item': item['number'], 'status': result['status']}), flush=True)
    write_json(folder / 'matches.json', {'package': str(package), 'image_sha256': matcher.digest, 'matches': matches})
    receipt = {} if options.dry_run else save_annotations(package, article, matches, options.update_existing)
    accepted_count = sum(m['status'] == 'accepted' for m in matches)
    return {**record, 'package': str(package), 'accepted': accepted_count, 'receipt': receipt,
            'status': 'complete' if matches and accepted_count == len(matches) and not failures else 'needs_review'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--maps-root', type=Path, action='append', help='Collection roots in priority order.')
    parser.add_argument('--cache', type=Path, default=Path('data/wiki_collectibles'))
    parser.add_argument('--chapters', nargs='+', type=int, help='Omit to process all published chapters.')
    parser.add_argument('--difficulty', choices=('normal', 'hard', 'both'), default='both')
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--refresh', action='store_true',
                        help='Refresh downloaded resources, keeping failed cache intact.')
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='Download and match without writing annotations.')
    parser.add_argument('--update-existing', action='store_true',
                        help='Replace same-source imports, including manual edits.')
    parser.add_argument('--retries', type=int, default=2)
    options = parser.parse_args(argv)
    if options.retries < 0 or (options.chapters and min(options.chapters) < 1):
        parser.error('Retries must be nonnegative; chapter numbers must be positive.')
    roots = [p.resolve() for p in (options.maps_root or [Path('data/chapter_maps')])]
    cv2.setNumThreads(2)
    client = WikiClient(options.cache.resolve(), options.offline, options.refresh, options.retries)
    results = []
    try:
        articles = parse_catalog(client.api(TREE_URL, client.cache / 'tree.json'))
        write_json(client.cache / 'catalog.json', articles)
        selected = [a for a in articles if (not options.chapters or a['chapter'] in options.chapters)
                    and (options.difficulty == 'both' or a['difficulty'] == options.difficulty)]
        expected = {(c, d) for c in (options.chapters or sorted({a['chapter'] for a in articles}))
                    for d in (('normal', 'hard') if options.difficulty == 'both' else (options.difficulty,))}
        available = {(a['chapter'], a['difficulty']) for a in selected}
        results.extend({'chapter': c, 'difficulty': d, 'status': 'missing_article'}
                       for c, d in sorted(expected - available))
        matchers = {}
        for article in selected:
            if (client.cache / 'STOP').exists():
                raise KeyboardInterrupt('Stopped by STOP file.')
            try:
                record = process_article(client, article, roots, options, matchers)
            except Exception as exc:
                record = {**article, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
            results.append(record)
            write_json(client.cache / 'progress.json', results)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        write_json(client.cache / 'progress.json', results)
        return 0 if results and all(r['status'] in ('cached', 'complete') for r in results) else 1
    except KeyboardInterrupt as exc:
        results.append({'status': 'stopped', 'error': str(exc)})
        write_json(client.cache / 'progress.json', results)
        return 130
    except Exception as exc:
        results.append({'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
        write_json(client.cache / 'progress.json', results)
        print(str(exc), flush=True)
        return 2
    finally:
        client.close()


if __name__ == '__main__':
    raise SystemExit(main())
