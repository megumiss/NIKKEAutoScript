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
    (p/'annotations.json').write_text(json.dumps({'schema_version':1,'image':'map.png','image_sha256':sha,
        'coordinates':coordinates,'objects':[],'connections':[]}))
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
  const tool = name => page.locator(`[data-tool="${name}"]`).click();
  const count = expected => waitUntil(async () => await page.locator('#object-list button').count() === expected);
  const save = async () => { await page.locator('#save').click(); await waitUntil(async () => await page.locator('#message').getAttribute('class') === 'success'); };

  await tool('point'); await page.locator('#new-label').fill('起点'); await click([180, 180]);
  await page.locator('#new-label').fill('终点'); await click([350, 180]); await count(2);
  await tool('polyline'); await click([220, 360]); await click([450, 360]); await click([580, 450]);
  await page.keyboard.press('Enter'); await count(3);
  await tool('polygon'); await click([700, 300]); await click([950, 320]); await click([900, 530], 2); await count(4);
  await tool('rectangle'); await drag([700, 650], [900, 780]); await count(5);
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

  await page.locator('#layer').selectOption('reference.png');
  await waitUntil(async () => (await page.locator('#base-image').getAttribute('href')).includes('reference.png'));
  await page.locator('#zoom-in').click(); await page.keyboard.down('Space'); await drag([500, 300], [540, 340]); await page.keyboard.up('Space');
  assert.equal(await page.locator('#save-state').innerText(), '已与磁盘同步');
  await page.locator('#show-labels').uncheck(); await page.locator('#show-labels').check();
  await page.locator('#layer').selectOption('map.png'); await page.locator('#fit').click();
  await page.locator('#object-list button').filter({ hasText: '终点' }).click(); await page.locator('#delete').click(); await count(4);
  await page.locator('#undo').click(); await count(6);
  await tool('polyline'); await click([100, 600]); await click([200, 620]); await page.keyboard.press('Backspace');
  assert.equal(await page.locator('#draft circle').count(), 1);
  await page.keyboard.press('Escape'); await count(6);
  await page.reload(); await count(6);
  assert.deepEqual(annotations(), saved);

  await tool('point'); await click([90, 700]); await count(7);
  page.once('dialog', dialog => dialog.dismiss()); await page.locator('#map-select').selectOption('chapter_02');
  assert.equal(await page.locator('#map-select').inputValue(), 'chapter_01');
  page.once('dialog', dialog => dialog.accept()); await page.locator('#map-select').selectOption('chapter_02');
  await waitUntil(async () => (await page.locator('#dimensions').textContent()).includes('600 × 500'));
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
  await second.locator('summary').click();
  const downloaded = second.waitForEvent('download'); await second.locator('#download').click();
  const downloadPath = await (await downloaded).path();
  assert.equal(JSON.parse(fs.readFileSync(downloadPath, 'utf8')).objects[0].note, '旧窗口不能覆盖');
  assert.ok(fs.readdirSync(path.join(temporary, 'chapter_01/.annotation_backups')).length >= 2);

  await page.locator('#fit').click();
  fs.mkdirSync(path.dirname(path.resolve(screenshot)), { recursive: true });
  await page.screenshot({ path: screenshot });
  await page.setViewportSize({ width: 760, height: 900 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ status: 'PASS', fixture: temporary, screenshot, checks: [
    'all geometry types', 'directed connections', 'vertex drag and numeric edit', 'undo/redo and delete',
    'layer/zoom/pan preserve coordinates', 'reload and map switching', 'save backup', 'concurrent-save rejection',
    'download current copy', 'responsive width', 'no browser errors'] }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close(); server.kill();
});
