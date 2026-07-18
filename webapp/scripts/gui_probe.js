// 一次性脚本：截取当前问题区域，用完即删
const { chromium } = require('playwright');
const path = require('path');

(async () => {
    const outDir = path.resolve(__dirname, 'shots');
    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

    async function dismissNotice(tries = 8) {
        for (let i = 0; i < tries; i++) {
            const btn = page.locator('button:has-text("我知道了")').first();
            if (await btn.count() && await btn.isVisible().catch(() => false)) {
                await btn.click().catch(() => {});
                await page.waitForTimeout(600);
            } else {
                return;
            }
        }
    }

    await page.goto('http://127.0.0.1:12299');
    await page.waitForTimeout(5000);
    await dismissNotice();

    // 进入 nkas 实例
    const inst = page.locator('button:has-text("nkas")').first();
    if (await inst.count()) {
        await inst.click();
        await page.waitForTimeout(3000);
    }

    // 1. 总览页：启动/自动滚动 按钮
    await page.screenshot({ path: path.join(outDir, 'p1-overview.png') });
    // 按钮区域特写
    const bar = page.locator('#pywebio-scope-scheduler-bar, #pywebio-scope-log-bar').first();
    if (await bar.count()) {
        const btns = page.locator('#pywebio-scope-schedulers');
        if (await btns.count()) await btns.screenshot({ path: path.join(outDir, 'p1-buttons.png') });
    }

    // 2. 左侧菜单（含折叠箭头）
    const menu = page.locator('#pywebio-scope-menu').first();
    if (await menu.count()) {
        await menu.screenshot({ path: path.join(outDir, 'p2-menu.png') });
    }
    // 展开第一个折叠组
    const collapse = page.locator('.collapse-toggle, [data-toggle="collapse"], .card-header').first();
    if (await collapse.count()) {
        await collapse.click().catch(() => {});
        await page.waitForTimeout(800);
        await page.screenshot({ path: path.join(outDir, 'p2-menu-open.png') });
    }

    // 3. NKAS 设置页（游戏路径输入框、下拉框）
    const taskBtn = page.locator('button:has-text("NKAS")').first();
    if (await taskBtn.count()) {
        await taskBtn.click().catch(() => {});
        await page.waitForTimeout(3000);
        await page.screenshot({ path: path.join(outDir, 'p3-settings.png'), fullPage: false });
        // 滚动到游戏路径
        const gp = page.locator('text=游戏路径').first();
        if (await gp.count()) {
            await gp.scrollIntoViewIfNeeded().catch(() => {});
            await page.waitForTimeout(500);
            await page.screenshot({ path: path.join(outDir, 'p3-gamepath.png') });
        }
        // 打开一个下拉框看选项样式
        const sel = page.locator('select').first();
        if (await sel.count()) {
            await sel.scrollIntoViewIfNeeded().catch(() => {});
            await sel.click().catch(() => {});
            await page.waitForTimeout(500);
            await page.screenshot({ path: path.join(outDir, 'p4-select-open.png') });
            await page.keyboard.press('Escape').catch(() => {});
        }
    }

    await browser.close();
    console.log('done');
})();
