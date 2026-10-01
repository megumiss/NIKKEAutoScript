'use strict';

function createScanController(editor) {
  const byId = id => document.getElementById(id);
  let job = null, pending = false;
  const active = () => pending || Boolean(job?.running);
  const post = (path, body) => editor.api(path, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': editor.token() }, body: JSON.stringify(body) });
  function sync() {
    const blocked = active() || editor.moving() || editor.busy();
    byId('scan-start').disabled = blocked;
    byId('scan-stop').disabled = !job?.running || pending;
    for (const id of ['scan-chapter', 'scan-3d', 'scan-stroke']) byId(id).disabled = blocked;
    byId('scan-open').disabled = job?.state !== 'complete' || blocked;
  }
  function show(value) {
    job = value;
    byId('scan-status').textContent = value.message || '正在读取扫描状态…';
    byId('scan-status').classList.toggle('error', value.state === 'failed');
    byId('scan-progress').textContent = value.id
      ? `第 ${value.chapter} 章 · ${value.process_3d ? '3D 处理' : '平面处理'} · 已采集 ${value.frames || 0} 帧` : '';
    byId('scan-log').textContent = value.log_path || '';
    editor.sync();
  }
  async function poll() {
    try { const value = await editor.api('/api/scan'); if (!pending) show(value); }
    catch (error) { byId('scan-status').textContent = `扫描状态读取失败：${error.message}`; }
    finally { setTimeout(poll, active() ? 1000 : 3000); }
  }
  byId('scan-start').addEventListener('click', async () => {
    if (active() || editor.moving() || editor.busy()) return;
    const chapter = Number(byId('scan-chapter').value), stroke = Number(byId('scan-stroke').value);
    if (!Number.isInteger(chapter) || chapter < 1 || chapter > 99 || !Number.isFinite(stroke) || stroke <= 0 || stroke > 240) {
      byId('scan-status').textContent = '请输入 1～99 的章节号和 1～240 的扫描步长。'; return;
    }
    pending = true; editor.sync();
    try { show(await post('/api/scan/start', { chapter, stroke_px: stroke, process_3d: byId('scan-3d').checked })); }
    catch (error) { byId('scan-status').textContent = `无法开始扫描：${error.message}`; }
    finally { pending = false; editor.sync(); }
  });
  byId('scan-stop').addEventListener('click', async () => {
    if (!job?.running || pending) return;
    pending = true; editor.sync();
    try { show(await post('/api/scan/stop', { job: job.id })); }
    catch (error) { byId('scan-status').textContent = `停止失败：${error.message}`; }
    finally { pending = false; editor.sync(); }
  });
  byId('scan-open').addEventListener('click', async () => {
    if (job?.state !== 'complete' || active()) return;
    try { await editor.refresh(); await editor.open(job.map_id); }
    catch (error) { byId('scan-status').textContent = `无法打开结果：${error.message}`; }
  });
  byId('scan-chapter').addEventListener('change', () => {
    byId('scan-3d').checked = Number(byId('scan-chapter').value) === 40;
  });
  setTimeout(poll, 0);
  return { get running() { return active(); }, sync,
    mapOpened(map) { if (active()) return; byId('scan-chapter').value = map.chapter || 40;
      byId('scan-3d').checked = map.chapter === 40; sync(); } };
}
