'use strict';

function createConnectivityController(editor) {
  const byId = id => document.getElementById(id), ns = 'http://www.w3.org/2000/svg';
  let key = '', serial = 0, timer, data = null, drawnData = null, drawnLayer = '', scale = 1;
  const geometryKey = () => JSON.stringify([editor.map()?.id, editor.doc()?.terrain_edits || [],
    editor.doc()?.connectivity_edits || []]);
  const color = id => `hsl(${(id * 137.508) % 360} 80% 65%)`;
  function svg(name, attributes, text) {
    const node = document.createElementNS(ns, name);
    for (const [k, v] of Object.entries(attributes)) node.setAttribute(k, v);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function draw(z) {
    scale = z;
    const group = byId('connectivity-regions'), corrections = byId('connectivity-corrections');
    group.style.display = byId('show-connectivity').checked ? '' : 'none';
    corrections.style.display = group.style.display;
    if (!editor.map()) return;
    const layer = byId('connectivity-layer').value;
    if (data !== drawnData || layer !== drawnLayer) {
      drawnData = data; drawnLayer = layer; group.replaceChildren();
      const regions = data?.[layer];
      if (regions) {
        const paths = new Map();
        regions.rows.forEach((runs, y) => runs.forEach(([start, end, id]) => {
          paths.set(id, (paths.get(id) || '') + `M${start * 8 - 4},${y * 8 - 4}h${(end - start) * 8}v8h-${(end - start) * 8}z`);
        }));
        for (const [id, d] of paths) group.append(svg('path', { d, fill: color(id), 'fill-opacity': .45 }));
        for (const region of regions.regions) group.append(svg('text', {
          x: region.center[0], y: region.center[1], fill: '#fff', stroke: '#101317',
          'paint-order': 'stroke', 'text-anchor': 'middle', 'dominant-baseline': 'middle',
        }, `R${region.id}`));
      }
    }
    for (const label of group.querySelectorAll('text')) {
      label.setAttribute('font-size', 13 / z); label.setAttribute('stroke-width', 3 / z);
    }
    corrections.replaceChildren();
    for (const edit of editor.doc()?.connectivity_edits || []) {
      corrections.append(svg('polyline', { points: edit.points.map(p => p.join(',')).join(' '), fill: 'none',
        stroke: '#ff7b91', 'stroke-width': edit.width, 'stroke-opacity': .7 }));
      for (const [x, y] of [edit.points[0], edit.points.at(-1)]) corrections.append(svg('circle', {
        cx: x, cy: y, r: 4 / z, fill: '#ff7b91', stroke: '#101317', 'stroke-width': 1 / z,
      }));
    }
  }
  function details(pending = false) {
    byId('connectivity-status').textContent = pending ? '正在计算连通预览…' : data
      ? `初判 ${data.automatic.regions.length} 个区域 · 修订后 ${data.effective.regions.length} 个区域`
      : '打开地图后显示道路连通初判。';
    const list = byId('connectivity-edits'); list.replaceChildren();
    (editor.doc()?.connectivity_edits || []).forEach((edit, index) => {
      const row = document.createElement('div'); row.className = 'connectivity-edit';
      const text = document.createElement('span');
      text.textContent = `${index + 1}. 分隔线 · ${edit.points[0].join(',')} → ${edit.points.at(-1).join(',')}`;
      const button = document.createElement('button'); button.textContent = '删除';
      button.setAttribute('aria-label', `删除第 ${index + 1} 条连通修订`); button.disabled = editor.busy();
      button.onclick = () => editor.change(() => {
        editor.doc().connectivity_edits = editor.doc().connectivity_edits.filter(v => v.id !== edit.id);
      });
      row.append(text, button); list.append(row);
    });
  }
  function sync() {
    const next = geometryKey();
    if (!editor.map()) return;
    if (next === key) {
      for (const button of byId('connectivity-edits').querySelectorAll('button')) button.disabled = editor.busy();
      return;
    }
    const oldMap = key ? JSON.parse(key)[0] : null;
    key = next; clearTimeout(timer); const requestSerial = ++serial;
    if (oldMap !== editor.map().id) {
      data = editor.map().connectivity; details(); draw(scale); return;
    }
    details(true); draw(scale);
    const map = editor.map(), document = JSON.parse(JSON.stringify(editor.doc()));
    timer = setTimeout(async () => {
      try {
        const result = await editor.api('/api/connectivity/preview', { method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': editor.token() },
          body: JSON.stringify({ id: map.id, revision: map.revision, annotations: document }) });
        if (requestSerial !== serial) return;
        data = result; details(); draw(scale);
      } catch (error) {
        if (requestSerial === serial) byId('connectivity-status').textContent = `连通预览失败：${error.message}`;
      }
    }, 250);
  }
  for (const name of ['show-connectivity', 'connectivity-layer']) byId(name).onchange = () => draw(scale);
  return { sync, draw,
    opened(value) { ++serial; clearTimeout(timer); key = geometryKey(); data = value; details(); draw(scale); },
    saved(value) { ++serial; clearTimeout(timer); data = value; details(); draw(scale); },
  };
}
