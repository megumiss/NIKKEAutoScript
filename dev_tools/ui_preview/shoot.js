// Screenshot the static UI preview. Run from repo root:
//   node dev_tools/ui_preview/shoot.js
// Reuses playwright from webapp/node_modules.
const path = require('path');
const { createRequire } = require('module');
const webappRequire = createRequire(path.resolve(__dirname, '../../webapp/package.json'));
const { chromium } = webappRequire('playwright');

(async () => {
  const root = path.resolve(__dirname, '../..');
  const file = 'file:///' + path.join(root, 'dev_tools/ui_preview/preview.html').split(path.sep).join('/');
  const out = (name) => path.join(root, 'dev_tools/ui_preview/shots', name);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 860 } });
  await page.goto(file);
  await page.waitForTimeout(400);
  await page.screenshot({ path: out('1-dashboard.png') });

  // 实例调度总览（含任务栏）
  await page.click('#side-instances .side-item >> nth=0');
  await page.waitForTimeout(2200);
  await page.screenshot({ path: out('2-instance.png') });

  // 普通任务配置页（咨询）
  await page.click('.rail-item[data-task-name="咨询"]');
  await page.waitForTimeout(300);
  await page.screenshot({ path: out('3-task.png') });

  // 工具任务页（自动爬塔）+ 启动一次（工具组默认折叠，先筛选展开）
  await page.fill('#rail-search', '自动爬塔');
  await page.click('.rail-item[data-task-name="自动爬塔"]');
  await page.fill('#rail-search', '');
  await page.waitForTimeout(200);
  await page.click('#tool-run-btn');
  await page.waitForTimeout(200);
  await page.screenshot({ path: out('4-tool.png') });

  // 任务栏筛选
  await page.fill('#rail-search', '竞技');
  await page.waitForTimeout(300);
  await page.screenshot({ path: out('5-rail-filter.png') });
  await page.fill('#rail-search', '');

  // 实例管理
  await page.click('.side-item[data-view="manage"]');
  await page.waitForTimeout(200);
  await page.screenshot({ path: out('6-manage.png') });

  // 浅色主题
  await page.click('.side-item[data-view="dashboard"]');
  await page.click('#theme-toggle');
  await page.waitForTimeout(200);
  await page.screenshot({ path: out('7-dashboard-light.png') });
  await page.click('#theme-toggle');

  // 单实例入口自适应（点击演示按钮 → 自动跳转调度总览）
  await page.click('#demo-single');
  await page.waitForTimeout(300);
  await page.screenshot({ path: out('8-single-instance.png') });

  await browser.close();
  console.log('done');
})().catch((e) => { console.error(e); process.exit(1); });
