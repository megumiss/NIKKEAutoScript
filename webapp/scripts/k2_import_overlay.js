const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto('http://127.0.0.1:12299', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);

  // dismiss announcement popup if present
  for (let i = 0; i < 5; i++) {
    const btn = page.locator('.modal.show button:has-text("我知道了"), .modal.show .close');
    if (await btn.count()) {
      await btn.first().click();
      await page.waitForTimeout(600);
    } else break;
  }

  // open 多开 (manage) modal
  await page.locator('button:has-text("多开")').first().click();
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'webapp/scripts/shots/k2-manage.png' });

  // click 导入
  await page.locator('.modal.show button:has-text("导入")').first().click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: 'webapp/scripts/shots/k2-import-overlay.png' });

  // inspect geometry of the input container/cards
  const info = await page.evaluate(() => {
    const c = document.querySelector('#input-container');
    const k = document.querySelector('#input-cards');
    const card = document.querySelector('#input-cards .card');
    const cs = getComputedStyle(c);
    const ks = getComputedStyle(k);
    const r = k.getBoundingClientRect();
    return {
      containerPos: cs.position, containerBg: cs.backgroundColor, containerZ: cs.zIndex,
      cardsPos: ks.position, cardsZ: ks.zIndex, cardsW: ks.width,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      hasCard: !!card,
    };
  });
  console.log('GEOM', JSON.stringify(info));

  // click 取消 inside the import form
  await page.locator('#input-cards button:has-text("取消")').first().click();
  await page.waitForTimeout(1000);
  const after = await page.evaluate(() => {
    const k = document.querySelector('#input-cards');
    const c = document.querySelector('#input-container');
    const modal = document.querySelector('.modal.show');
    return {
      cardsEmpty: k && k.childElementCount === 0,
      containerPos: getComputedStyle(c).position,
      modalReopened: !!modal,
      modalTitle: modal ? (modal.querySelector('.modal-title') || {}).textContent : null,
    };
  });
  console.log('AFTER', JSON.stringify(after));
  await page.screenshot({ path: 'webapp/scripts/shots/k2-after-cancel.png' });

  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
