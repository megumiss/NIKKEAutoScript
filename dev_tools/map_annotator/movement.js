'use strict';

function createMovementController(editor) {
  const byId = id => document.getElementById(id);
  let target = null, job = null, pending = false, picking = false;
  const active = () => pending || Boolean(job?.running);
  const post = (path, body) => editor.api(path, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': editor.token() }, body: JSON.stringify(body) });

  function sync() {
    const map = editor.map(), item = editor.selected(), running = active();
    byId('move-pick').disabled = !map || running || editor.busy();
    byId('move-pick').setAttribute('aria-pressed', String(picking));
    byId('move-use-selected').disabled = !map || running || editor.busy() || item?.type !== 'point';
    byId('move-start').disabled = !map || !target || running || editor.busy();
    byId('move-stop').disabled = !job?.id || !job?.running;
    byId('move-difficulty').disabled = running;
    byId('move-target').textContent = target
      ? `目标 X ${target[0].toFixed(1)} · Y ${target[1].toFixed(1)}` : '尚未选择目标';
  }

  function select(point, mode) {
    if (active()) return;
    target = [...point]; picking = false;
    if (mode === 'normal' || mode === 'hard') byId('move-difficulty').value = mode;
    editor.selectTool('select'); editor.redraw(); sync();
    byId('move-status').textContent = '目标已选择，点击“移动到目标”开始自动定位与移动。';
  }

  function showJob(value) {
    job = value;
    if (job.running && job.map_id === editor.map()?.id) {
      target = [...job.target]; byId('move-difficulty').value = job.difficulty;
    }
    if (job.id || (!target && !picking)) byId('move-status').textContent = job.message || '正在读取状态…';
    byId('move-status').classList.toggle('error', job.state === 'failed');
    byId('move-metrics').textContent = job.id
      ? `第 ${job.chapter} 章 · ${job.movement_clicks ?? 0} 次移动${Number.isFinite(job.distance) ? ` · 距目标 ${job.distance.toFixed(1)} 地图像素` : ''}` : '';
    byId('move-evidence').hidden = !job.log_path;
    byId('move-log').textContent = job.log_path || '';
    byId('move-preview').hidden = !job.preview;
    if (job.preview) byId('move-preview').src = `/api/movement/preview?${new URLSearchParams({ job: job.id, v: job.updated_at || '' })}`;
    editor.sync(); editor.redraw(); sync();
  }

  async function poll() {
    try { const value = await editor.api('/api/movement'); if (!pending) showJob(value); }
    catch (error) {
      byId('move-status').textContent = `读取移动状态失败：${error.message}。恢复连接后会继续查询。`;
    } finally { setTimeout(poll, active() ? 1000 : 3000); }
  }

  byId('move-pick').addEventListener('click', () => {
    if (active()) return;
    picking = !picking;
    editor.selectTool(picking ? 'move-target' : 'select'); sync();
    byId('move-status').textContent = picking ? '在道路底图上单击目标位置，不会创建或修改标注。' : '已取消选点。';
  });
  byId('move-use-selected').addEventListener('click', () => {
    const item = editor.selected();
    if (item?.type === 'point') select(item.points[0], item.difficulty || item.source?.difficulty);
  });
  byId('move-start').addEventListener('click', async () => {
    const map = editor.map();
    if (!map || !target || active()) return;
    pending = true; picking = false; editor.sync(); sync();
    byId('move-status').textContent = '正在提交移动测试…';
    try {
      const value = await post('/api/movement/start', { id: map.id, revision: map.revision,
        image_sha256: map.annotations.image_sha256, target, difficulty: byId('move-difficulty').value });
      showJob({ ...value, running: true });
    } catch (error) { byId('move-status').textContent = `无法开始移动：${error.message}`; }
    finally { pending = false; editor.sync(); sync(); }
  });
  byId('move-stop').addEventListener('click', async () => {
    if (!job?.running) return;
    byId('move-stop').disabled = true;
    try { const result = await post('/api/movement/stop', { job: job.id });
      byId('move-status').textContent = result.message;
    } catch (error) { byId('move-status').textContent = `停止请求失败：${error.message}`; }
  });

  setTimeout(poll, 0);
  return {
    get running() { return active(); }, sync, select,
    reset() { if (!active()) { target = null; picking = false; } sync(); },
    toolChanged(tool) { picking = tool === 'move-target'; sync(); },
    draw(scale) {
      const group = byId('movement-markers'); group.replaceChildren();
      const points = [[target, '#ffb454', '目标']];
      if (job?.position && job.map_id === editor.map()?.id) points.push([job.position, '#70dfa0', '小队']);
      for (const [point, color, label] of points) {
        if (!point) continue;
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        for (const [key, value] of Object.entries({ cx: point[0], cy: point[1], r: 10 / scale,
          fill: 'none', stroke: color, 'stroke-width': 2 / scale })) circle.setAttribute(key, value);
        const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        for (const [key, value] of Object.entries({ x: point[0] + 14 / scale, y: point[1] - 12 / scale,
          fill: color, 'font-size': 13 / scale, stroke: '#090909', 'stroke-width': 3 / scale,
          'paint-order': 'stroke' })) text.setAttribute(key, value);
        text.textContent = label; group.append(circle, text);
      }
    },
  };
}
