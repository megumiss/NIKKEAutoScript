// Run: node tests/map_annotator_browser.cjs <python executable> [screenshot path]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require('../webui/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const python = process.argv[2] || 'python';
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'nikke-map-annotator-'));
const screenshot = process.argv[3] || path.join(temporary, 'editor.png');
const fixture = `
import sys,json,hashlib
from pathlib import Path
from PIL import Image,ImageDraw
root=Path(sys.argv[1])
for chapter,size in [(1,(1200,900)),(2,(600,500))]:
    p=root/f'chapter_{chapter:02d}'
    p.mkdir()
    im=Image.new('RGB',size,'#1e2c38')
    draw=ImageDraw.Draw(im)
    draw.line([(180,180),(350,180),(450,360),(580,450),(800,650)],fill='#2d8dc7',width=60)
    im.save(p/'map.png')
    for x in range(0,size[0],60): draw.line([(x,0),(x,size[1])],fill='#375464')
    im.save(p/'reference.png')
    sha=hashlib.sha256((p/'map.png').read_bytes()).hexdigest()
    coordinates={'unit':'pixel','origin':'top_left','x':'right','y':'down'}
    (p/'map.json').write_text(json.dumps({'chapter':chapter,'size':size,'image_sha256':sha,'coordinates':coordinates}))
    objects=[]
    connections=[]
    if chapter==2:
        objects=[
            {'id':'wiki_normal','type':'point','difficulty':'normal','label':'Wiki A','points':[[100,100]],
             'source':{'difficulty':'normal','url':'https://example.com/wiki'}},
            {'id':'wiki_hard','type':'point','label':'Wiki B','points':[[100,100]],'source':{'difficulty':'hard'}},
            {'id':'label_normal','type':'point','label':'普通收集物 01','points':[[200,100]]},
            {'id':'label_hard','type':'point','label':'困难收集品 01','points':[[200,100]]},
            {'id':'legacy','type':'point','label':'旧路口','points':[[300,100]]},
        ]
        connections=[{'id':'legacy_edge','from':'wiki_normal','to':'legacy','directed':False}]
    (p/'annotations.json').write_text(json.dumps({'schema_version':1,'image':'map.png','image_sha256':sha,
        'coordinates':coordinates,'objects':objects,'connections':connections}))
`;
const prepared = spawnSync(python, ['-c', fixture, temporary], { cwd: root, encoding: 'utf8' });
assert.equal(prepared.status, 0, prepared.stderr);
const server = spawn(python, ['-X', 'utf8', '-u', 'dev_tools/map_annotator.py', '--root', temporary,
  '--map', path.join(temporary, 'chapter_01'), '--port', '0', '--no-open'], { cwd: root, windowsHide: true });
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
let browser;
const errors = [];

async function waitUntil(check, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await check(); if (result) return result; await new Promise(r => setTimeout(r, 50)); }
  throw new Error(`Timed out. Server: ${serverLog}`);
}
const annotations = () => JSON.parse(fs.readFileSync(path.join(temporary, 'chapter_01/annotations.json'), 'utf8'));
const approx = (actual, expected, tolerance = 1.5) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} != ${expected}`);

(async () => {
  const url = await waitUntil(() => serverLog.match(/http:\/\/127\.0\.0\.1:\d+\//)?.[0]);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(url);
  await waitUntil(async () => (await page.locator('#dimensions').textContent()).includes('1200 × 900'));
  async function screen(point) {
    return page.evaluate(([x, y]) => { const p = new DOMPoint(x, y).matrixTransform(document.getElementById('scene').getScreenCTM());
      return { x: p.x, y: p.y }; }, point);
  }
  async function click(point, count = 1) { const p = await screen(point); await page.mouse.click(p.x, p.y, { clickCount: count }); }
  async function drag(from, to) { const a = await screen(from), b = await screen(to);
    await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move(b.x, b.y, { steps: 5 }); await page.mouse.up(); }
  const tool = async name => {
    const section = name.startsWith('road-') ? '#road-tools' : '#annotation-tools';
    if (await page.locator(section).getAttribute('open') === null)
      await page.locator(`${section} > summary`).click();
    await page.locator(`[data-tool="${name}"]`).click();
  };
  const category = name => page.locator('#new-category').selectOption(name);
  const difficulty = name => page.locator(`[data-difficulty="${name}"]`).click();
  const count = expected => waitUntil(async () => await page.locator('#object-list button').count() === expected);
  const save = async () => { await page.locator('#save').click(); await waitUntil(async () => await page.locator('#message').getAttribute('class') === 'success'); };

  assert.deepEqual(await page.locator('#new-category option').allTextContents(),
    ['普通收集品', '困难收集品', '地面机关', '地面电梯', '电梯传送关系']);
  await category('ground_elevator'); await page.locator('#new-label').fill('起点'); await click([180, 180]);
  await page.locator('#new-label').fill('终点'); await click([350, 180]); await count(2);
  await category('ground_mechanism'); await tool('polyline'); await page.locator('#new-label').fill('道路');
  await click([220, 360]); await click([450, 360]); await click([580, 450]);
  await page.keyboard.press('Enter'); await count(3);
  await page.locator('#new-label').fill('障碍');
  await tool('polygon'); await click([700, 300]); await click([950, 320]); await click([900, 530], 2); await count(4);
  await tool('rectangle'); await page.locator('#new-label').fill('区域'); await drag([700, 650], [900, 780]); await count(5);
  await tool('connect'); await page.locator('#new-directed').check(); await click([180, 180]); await click([350, 180]); await count(6);
  await page.locator('#edit-note').fill('仅从起点前往终点'); await page.locator('#edit-note').press('Tab');

  await tool('select'); await click([180, 180]);
  await page.locator('#actual-size').click(); await drag([180, 180], [230, 220]);
  await page.getByRole('spinbutton', { name: '顶点 1 X', exact: true }).fill('231.5');
  await page.getByRole('spinbutton', { name: '顶点 1 X', exact: true }).press('Tab');
  await page.locator('#edit-label').fill('起点 A'); await page.locator('#edit-label').press('Tab');
  await page.locator('#object-list button').filter({ hasText: '道路' }).click();
  await drag([450, 360], [470, 390]);
  approx(Number(await page.getByRole('spinbutton', { name: '顶点 2 X', exact: true }).inputValue()), 470);
  await page.keyboard.press('Control+z');
  approx(Number(await page.getByRole('spinbutton', { name: '顶点 2 X', exact: true }).inputValue()), 450);
  await page.keyboard.press('Control+Shift+z');
  approx(Number(await page.getByRole('spinbutton', { name: '顶点 2 X', exact: true }).inputValue()), 470);
  await page.locator('#add-vertex').click();
  assert.equal(await page.locator('.vertex-row').count(), 4);
  await page.getByRole('button', { name: '删除顶点 3', exact: true }).click();
  assert.equal(await page.locator('.vertex-row').count(), 3);
  await save();
  const saved = annotations();
  assert.equal(saved.objects.length, 5); assert.equal(saved.connections.length, 1);
  assert.equal(saved.objects[0].points[0][0], 231.5);
  approx(saved.objects[0].points[0][1], 220);
  assert.equal(saved.objects[3].points.length, 3); assert.equal(saved.objects[4].points.length, 4);
  assert.equal(saved.connections[0].directed, true); assert.equal(saved.connections[0].note, '仅从起点前往终点');
  assert.equal(saved.connections[0].category, 'elevator_connection');
  assert.equal(saved.objects[0].category, 'ground_elevator');

  await page.getByText('图层与显示', { exact: true }).click();
  await page.locator('#layer').selectOption('reference.png');
  await waitUntil(async () => (await page.locator('#base-image').getAttribute('href')).includes('reference.png'));
  await page.locator('#zoom-in').click(); await page.keyboard.down('Space'); await drag([500, 300], [540, 340]); await page.keyboard.up('Space');
  assert.equal(await page.locator('#save-state').innerText(), '已与磁盘同步');
  await page.locator('#show-labels').uncheck(); await page.locator('#show-labels').check();
  await page.locator('#layer').selectOption('map.png'); await page.locator('#fit').click();
  await page.locator('#object-list button').filter({ hasText: '终点' }).click(); await page.locator('#delete').click(); await count(4);
  await page.locator('#undo').click(); await count(6);
  await category('ground_mechanism'); await tool('polyline'); await click([100, 600]); await click([200, 620]);
  await category('hard_collectible');
  assert.equal(await page.locator('#new-category').inputValue(), 'ground_mechanism');
  await difficulty('hard');
  assert.equal(await page.locator('[data-difficulty="normal"]').getAttribute('aria-pressed'), 'true');
  await page.locator('#canvas').focus(); await page.keyboard.press('Backspace');
  assert.equal(await page.locator('#draft circle').count(), 1);
  await page.keyboard.press('Escape'); await count(6);
  await page.reload(); await count(6);
  assert.deepEqual(annotations(), saved);

  await tool('point'); await click([90, 700]); await count(7);
  page.once('dialog', dialog => dialog.dismiss()); await page.locator('#map-select').selectOption('chapter_02');
  assert.equal(await page.locator('#map-select').inputValue(), 'chapter_01');
  page.once('dialog', dialog => dialog.accept()); await page.locator('#map-select').selectOption('chapter_02');
  await waitUntil(async () => (await page.locator('#dimensions').textContent()).includes('600 × 500'));
  await count(4);
  const legacyPath = path.join(temporary, 'chapter_02/annotations.json');
  const legacyBytes = fs.readFileSync(legacyPath, 'utf8');
  await difficulty('hard'); await count(3);
  assert.equal(await page.locator('#objects [data-object="wiki_normal"]').count(), 0);
  assert.equal(await page.locator('#objects [data-object="wiki_hard"]').count(), 1);
  assert.equal(await page.locator('#connections [data-connection="legacy_edge"]').count(), 0);
  await difficulty('all'); await count(6);
  assert.equal(await page.locator('#save-state').innerText(), '已与磁盘同步');
  assert.equal(fs.readFileSync(legacyPath, 'utf8'), legacyBytes);
  await page.locator('[data-select-id="wiki_normal"]').click();
  await page.locator('#edit-category').selectOption('hard_collectible');
  await page.locator('#save').click();
  await waitUntil(async () => await page.locator('#message').getAttribute('class') === 'success');
  const reclassified = JSON.parse(fs.readFileSync(legacyPath, 'utf8')).objects[0];
  assert.equal(reclassified.category, 'hard_collectible'); assert.equal(reclassified.difficulty, 'hard');
  assert.deepEqual(reclassified.source, { difficulty: 'normal', url: 'https://example.com/wiki' });
  assert.equal(reclassified.label, 'Wiki A');
  await difficulty('normal'); await count(2);
  await page.locator('#map-select').selectOption('chapter_01'); await count(6);

  const second = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  second.on('pageerror', error => errors.push(String(error)));
  await second.goto(url); await waitUntil(async () => await second.locator('#object-list button').count() === 6);
  await page.locator('#object-list button').filter({ hasText: '起点 A' }).click();
  await page.locator('#edit-note').fill('主窗口保存'); await page.locator('#edit-note').press('Tab'); await save();
  await second.locator('#object-list button').filter({ hasText: '起点 A' }).click();
  await second.locator('#edit-note').fill('旧窗口不能覆盖'); await second.locator('#edit-note').press('Tab');
  await second.locator('#save').click();
  await waitUntil(async () => (await second.locator('#message').textContent()).includes('已被其他窗口'));
  assert.equal(annotations().objects[0].note, '主窗口保存');
  await second.locator('.file-details > summary').click();
  const downloaded = second.waitForEvent('download'); await second.locator('#download').click();
  const downloadPath = await (await downloaded).path();
  assert.equal(JSON.parse(fs.readFileSync(downloadPath, 'utf8')).objects[0].note, '旧窗口不能覆盖');
  assert.ok(fs.readdirSync(path.join(temporary, 'chapter_01/.annotation_backups')).length >= 2);

  await category('normal_collectible'); await click([100, 600]); await count(7);
  await category('hard_collectible'); await click([100, 600]); await count(7);
  assert.equal(await page.locator('#object-count').innerText(), '7 / 8');
  assert.equal(await page.locator('#objects > g').count(), 6);
  await save();
  const withCollectibles = annotations();
  assert.equal(withCollectibles.objects.length, 7);
  const normal = withCollectibles.objects.find(item => item.category === 'normal_collectible');
  const hard = withCollectibles.objects.find(item => item.category === 'hard_collectible');
  assert.equal(normal.difficulty, 'normal'); assert.equal(hard.difficulty, 'hard');
  assert.deepEqual(normal.points, hard.points);
  await tool('select'); await click([100, 600]);
  assert.equal(await page.locator('#selected-id').innerText(), hard.id);
  await difficulty('normal');
  assert.equal(await page.locator('#new-category').inputValue(), 'normal_collectible');
  assert.equal(await page.locator('#properties').isVisible(), false);
  assert.equal(await page.locator('#handles circle').count(), 0);
  await click([100, 600]); assert.equal(await page.locator('#selected-id').innerText(), normal.id);
  assert.equal(await page.locator('#save-state').innerText(), '已与磁盘同步');
  await page.locator('#edit-category').selectOption('hard_collectible');
  assert.equal(await page.locator('[data-difficulty="hard"]').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.locator('#selected-id').innerText(), normal.id);
  await page.locator('#undo').click();
  await difficulty('all'); await count(8);
  await page.locator('#search').fill('普通收集品'); await count(1);
  await page.locator('#search').fill(''); await count(8);

  await category('elevator_connection'); await click([100, 600]);
  assert.match(await page.locator('#message').innerText(), /只能连接两部地面电梯/);
  assert.equal(await page.locator('#drawing-hint').isVisible(), false);
  await page.locator('#new-directed').check();
  await click([231.5, 220]); await click([231.5, 220]);
  assert.match(await page.locator('#message').innerText(), /另一个对象/);
  await click([350, 180]); await count(8);
  assert.match(await page.locator('#message').innerText(), /已有相同方向/);
  await page.locator('#new-directed').uncheck(); await click([350, 180]); await count(9);
  await page.locator('#object-list button').filter({ hasText: '起点 A' }).click();
  await page.locator('#edit-category').selectOption('ground_mechanism');
  assert.equal(await page.locator('#edit-category').inputValue(), 'ground_elevator');
  assert.match(await page.locator('#message').innerText(), /先删除相关传送关系/);
  await page.locator('#delete').click(); await count(6);
  await page.locator('#undo').click(); await count(9);
  await save();
  assert.equal(annotations().connections.length, 2);
  assert.equal(annotations().connections[1].directed, false);
  await page.reload(); await count(8);
  await difficulty('hard'); await count(8);
  await difficulty('all'); await count(9);

  // Browser movement actions use an explicit fake job; this regression never controls the game.
  const beforeMovement = fs.readFileSync(path.join(temporary, 'chapter_01/annotations.json'), 'utf8');
  let movementJob = { state: 'idle', message: '选择目标点后开始移动测试。' }, movementRequest;
  let calibrationInfo = { state: 'missing', message: '尚未标定' };
  await page.route(/\/api\/movement(?:[/?]|$)/, async route => {
    const endpoint = new URL(route.request().url()).pathname;
    if (endpoint === '/api/movement/calibration') {
      const info = new URL(route.request().url()).searchParams.get('difficulty') === 'hard'
        ? { state: 'missing', message: '困难尚未标定' } : calibrationInfo;
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(info) });
    }
    if (endpoint === '/api/movement/start') {
      movementRequest = route.request().postDataJSON();
      movementJob = { id: 'browser-test', map_id: movementRequest.id, target: movementRequest.target,
        action: movementRequest.action,
        chapter: 1, difficulty: movementRequest.difficulty, state: 'moving', running: true,
        message: '正在移动（测试桩）', movement_clicks: 1, position: [220, 300], distance: 24 };
    }
    if (endpoint === '/api/movement/stop') {
      movementJob = { ...movementJob, state: 'cancelled', running: false, message: '测试已停止' };
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(movementJob) });
  });
  await page.locator('#object-list button').filter({ hasText: '起点 A' }).click();
  await page.locator('#tab-movement').click();
  await page.locator('#move-use-selected').click();
  assert.match(await page.locator('#move-target').textContent(), /231\.5.*220\.0/);
  await page.locator('#fit').click(); await page.locator('#zoom-in').click();
  await page.locator('#move-pick').click(); await click([241.4, 317.2]);
  const pickedTarget = (await page.locator('#move-target').textContent()).match(/X ([\d.]+).*Y ([\d.]+)/).slice(1).map(Number);
  approx(pickedTarget[0], 241.4); approx(pickedTarget[1], 317.2);
  assert.equal(await page.locator('#movement-markers circle').count(), 1);
  await page.locator('#move-start').click();
  await waitUntil(async () => {
    if (errors.length) throw new Error(errors.join('\n'));
    const status = await page.locator('#move-status').textContent();
    if (status.includes('无法开始')) throw new Error(status);
    return movementRequest;
  });
  assert.deepEqual(movementRequest.target, pickedTarget);
  assert.equal(await page.locator('#move-start').isDisabled(), true);
  assert.equal(await page.locator('#map-select').isDisabled(), true);
  assert.equal(await page.locator('#scan-start').isDisabled(), true);
  await waitUntil(async () => (await page.locator('#move-metrics').textContent()).includes('24.0'));
  await page.locator('#move-stop').click();
  await waitUntil(async () => !(await page.locator('#map-select').isDisabled()));
  assert.equal(await page.locator('#move-start').isEnabled(), true);
  assert.equal(fs.readFileSync(path.join(temporary, 'chapter_01/annotations.json'), 'utf8'), beforeMovement);
  await page.locator('#map-select').selectOption('chapter_02');
  await waitUntil(async () => (await page.locator('#dimensions').textContent()).includes('600 × 500'));
  assert.equal(await page.locator('#move-target').textContent(), '尚未选择目标');
  assert.equal(await page.locator('#move-start').isDisabled(), true);
  await page.locator('#map-select').selectOption('chapter_01'); await difficulty('all'); await count(9);

  let scanJob = { state: 'idle', running: false }, scanRequest;
  await page.route(/\/api\/scan(?:[/?]|$)/, async route => {
    const endpoint = new URL(route.request().url()).pathname;
    if (endpoint === '/api/scan/start') {
      scanRequest = route.request().postDataJSON();
      scanJob = { ...scanRequest, id: 'scan-test', state: 'running', running: true,
        frames: 12, message: '正在扫描（测试桩）' };
    }
    if (endpoint === '/api/scan/stop') scanJob = { ...scanJob, state: 'cancelled', running: false };
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(scanJob) });
  });
  assert.equal(await page.locator('#scan-3d').isChecked(), false);
  await page.locator('#tab-scan').click();
  await page.locator('#scan-chapter').fill('40'); await page.locator('#scan-chapter').press('Tab');
  assert.equal(await page.locator('#scan-3d').isChecked(), true);
  await page.locator('#scan-start').click();
  await waitUntil(async () => scanRequest);
  assert.deepEqual(scanRequest, { chapter: 40, stroke_px: 120, process_3d: true });
  assert.equal(await page.locator('#move-start').isDisabled(), true);
  await waitUntil(async () => (await page.locator('#scan-progress').textContent()).includes('12'));
  await page.locator('#scan-stop').click();
  await waitUntil(async () => await page.locator('#scan-start').isEnabled());
  assert.equal(await page.locator('#scan-open').isDisabled(), true);
  await page.locator('#scan-chapter').fill('1'); await page.locator('#scan-chapter').press('Tab');
  assert.equal(await page.locator('#scan-3d').isChecked(), false);
  await page.locator('#scan-start').click();
  await waitUntil(async () => scanRequest.process_3d === false);
  scanJob = { ...scanJob, state: 'complete', running: false, map_id: 'chapter_02', message: '扫描完成' };
  await waitUntil(async () => await page.locator('#scan-open').isEnabled());
  assert.equal(await page.locator('#map-select').inputValue(), 'chapter_01');
  await page.locator('#scan-open').click();
  await waitUntil(async () => (await page.locator('#dimensions').textContent()).includes('600 × 500'));
  await page.locator('#map-select').selectOption('chapter_01'); await difficulty('all'); await count(9);

  await page.locator('#fit').click();
  await page.route(/\/api\/map\?map=chapter_01$/, async route => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...await response.json(), coordinate_model: 'local_parallax' } });
  });
  await page.locator('#map-select').selectOption('chapter_02');
  await waitUntil(async () => (await page.locator('#dimensions').textContent()).includes('600 × 500'));
  await page.locator('#map-select').selectOption('chapter_01'); await difficulty('all'); await count(9);
  await page.locator('#tab-movement').click();
  assert.equal(await page.locator('#move-calibration-panel').isVisible(), true);
  await page.locator('#move-pick').click(); await click([241, 317]);
  assert.equal(await page.locator('#move-start').isDisabled(), true);
  calibrationInfo = { state: 'ready', message: '已标定，可复用', training_samples: 6, validation_samples: 3, validation: 5.2 };
  await waitUntil(async () => (await page.locator('#move-calibration-status').textContent()).includes('已标定'));
  await page.locator('#move-pick').click(); await click([241, 317]);
  await page.locator('#move-start').click();
  await waitUntil(async () => movementRequest.action !== 'calibrate');
  assert.equal(movementRequest.auto_calibrate, false);
  await page.locator('#move-stop').click();
  await waitUntil(async () => await page.locator('#move-start').isEnabled());
  await page.locator('#move-difficulty').selectOption('hard');
  await waitUntil(async () => (await page.locator('#move-calibration-status').textContent()).includes('困难尚未标定'));
  assert.equal(await page.locator('#move-start').isDisabled(), true);
  // 道路修订沿用同一原图坐标，保存和导出都不能覆写基线。
  const originalMap = fs.readFileSync(path.join(temporary, 'chapter_01/map.png'));
  await tool('road-brush');
  await page.locator('#road-width').fill('20');
  await drag([200, 500], [400, 500]);
  assert.equal(await page.locator('#terrain > *').count(), 1);
  await tool('road-erase'); await click([300, 500]);
  assert.equal(await page.locator('#terrain > *').count(), 2);
  await tool('road-polygon');
  await click([500, 200]); await click([600, 200]); await click([600, 300]);
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#terrain > *').count(), 3);
  await page.locator('#undo').click(); assert.equal(await page.locator('#terrain > *').count(), 2);
  await page.locator('#redo').click(); assert.equal(await page.locator('#terrain > *').count(), 3);
  if (!(await page.locator('#show-annotations').isVisible())) await page.getByText('图层与显示', { exact: true }).click();
  await page.locator('#show-annotations').uncheck();
  assert.equal(await page.locator('#terrain > *').count(), 3);
  await page.locator('#show-terrain').uncheck(); assert.equal(await page.locator('#terrain > *').count(), 0);
  await page.locator('#show-terrain').check();
  await save(); assert.equal(annotations().terrain_edits.length, 3);
  await page.reload();
  await waitUntil(async () => await page.locator('#terrain > *').count() === 3);
  await page.locator('#road-tools > summary').click();
  await page.locator('#export-terrain').click();
  await waitUntil(async () => await page.locator('#terrain-export-link').isVisible());
  const exported = new URL(await page.locator('#terrain-export-link').getAttribute('href'), url).searchParams.get('export');
  const png = path.join(temporary, 'chapter_01/manual_exports', exported, 'map.png');
  const pixels = spawnSync(python, ['-c', 'from PIL import Image; import sys,json; im=Image.open(sys.argv[1]); print(json.dumps([im.getpixel(p) for p in [(240,500),(300,500),(580,220)]]))', png], { encoding: 'utf8' });
  assert.equal(pixels.status, 0, pixels.stderr);
  assert.deepEqual(JSON.parse(pixels.stdout), [[45,141,199],[15,20,28],[45,141,199]]);
  assert.deepEqual(fs.readFileSync(path.join(temporary, 'chapter_01/map.png')), originalMap);
  const exportedResponse = await page.request.get(new URL(await page.locator('#terrain-export-link').getAttribute('href'), url).href);
  assert.equal(exportedResponse.status(), 200);
  await tool('road-brush');
  fs.mkdirSync(path.dirname(path.resolve(screenshot)), { recursive: true });
  await page.screenshot({ path: screenshot });
  await page.setViewportSize({ width: 760, height: 900 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'PASS', fixture: temporary, screenshot, checks: [
    'five annotation categories', 'normal/hard isolation and shared mechanisms', 'legacy Wiki compatibility',
    'elevator-only directed and bidirectional connections', 'category edit and undo', 'all geometry types',
    'vertex drag and numeric edit', 'undo/redo and delete',
    'layer/zoom/pan preserve coordinates', 'reload and map switching', 'save backup', 'concurrent-save rejection',
    'download current copy', 'movement target coordinates, start, stop and map reset',
    'scan mode, step, progress, stop, explicit result opening and movement exclusion',
    'shared planar movement, pending layered movement, existing calibration and difficulty binding',
    'road brush/erase/polygon, undo/redo, visibility, save/reload, PNG export and baseline preservation',
    'responsive width', 'no browser errors'] }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close(); server.kill();
});
