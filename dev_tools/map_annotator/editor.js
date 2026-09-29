'use strict';

const $ = id => document.getElementById(id);
const svg = $('canvas');
const NS = 'http://www.w3.org/2000/svg';
const typeNames = { point: '点', polyline: '折线', polygon: '区域', connection: '连接' };
const defaults = { point: ['路口', '#ffb454'], polyline: ['道路', '#70dfa0'], polygon: ['障碍', '#ff7b91'],
  rectangle: ['区域', '#bb9bff'], connect: ['通路', '#d6dce5'] };
const labels = ['路口', '道路', '障碍', '区域', '传送点', '关卡', 'EX', 'Boss', '收集物', '备注', '通路'];
let current = null, doc = null, token = '', saved = '', selected = null, tool = 'select';
let history = [], future = [], draft = [], connectFrom = null, gesture = null, cursor = null;
let space = false, busy = false, saving = false, view = { x: 0, y: 0, scale: 1 };

function element(name, attributes = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
function message(text, kind = '') { $('message').textContent = text; $('message').className = kind; }
function snapshot() { return JSON.stringify(doc); }
function dirty() { return doc && snapshot() !== saved; }
function itemById(id) { return doc && [...doc.objects, ...doc.connections].find(item => item.id === id); }
function selectedItem() { return itemById(selected); }
function kindOf(item) { return item.type || 'connection'; }
function colorOf(item) { return item.color || defaults[item.type === 'polygon' ? 'polygon' : item.type || 'connect'][1]; }
function center(item) {
  if (item.points) return item.points.reduce((a, p) => [a[0] + p[0] / item.points.length, a[1] + p[1] / item.points.length], [0, 0]);
  const a = center(itemById(item.from)), b = center(itemById(item.to));
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}
function localPoint(event) {
  const rect = svg.getBoundingClientRect();
  return [(event.clientX - rect.left - view.x) / view.scale, (event.clientY - rect.top - view.y) / view.scale];
}
function inside(point) { return current && point[0] >= 0 && point[1] >= 0 && point[0] < current.size[0] && point[1] < current.size[1]; }
function bounded(point) { return point.map((v, i) => Math.round(Math.max(0, Math.min(current.size[i] - 1, v)) * 10) / 10); }
function uniqueId(prefix) { return `${prefix}_${crypto.randomUUID().replaceAll('-', '').slice(0, 12)}`; }

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function imageURL(id, filename) { return `/api/image?${new URLSearchParams({ map: id, image: filename })}`; }
function loadImage(url) {
  return new Promise((resolve, reject) => { const image = new Image(); image.onload = resolve;
    image.onerror = () => reject(new Error('地图图片加载失败。')); image.src = url; });
}

async function refreshMaps(first = false) {
  try {
    const data = await api('/api/maps'); token = data.token;
    const picker = $('map-select'); picker.replaceChildren();
    for (const entry of data.maps) picker.add(new Option(entry.title, entry.id));
    picker.title = data.root;
    if (!data.maps.length) {
      picker.add(new Option('目录中没有地图包', ''));
      message(`未找到地图包：${data.root}。需要同目录中的 map.png 和 map.json。`, 'error'); return;
    }
    const id = current?.id ?? data.initial ?? data.maps[0].id;
    if (data.maps.some(entry => entry.id === id)) picker.value = id;
    if (first) await openMap(picker.value);
  } catch (error) { message(`读取目录失败：${error.message}`, 'error'); }
}

async function openMap(id) {
  if (saving || busy) return;
  if ((dirty() || draft.length) && !confirm('当前有未保存的标注。放弃这些修改并打开地图？')) {
    $('map-select').value = current.id; return;
  }
  busy = true; syncState(); message('正在加载地图…');
  try {
    const data = await api(`/api/map?${new URLSearchParams({ map: id })}`);
    await loadImage(imageURL(id, 'map.png'));
    current = data; doc = data.annotations; saved = snapshot();
    history = []; future = []; draft = []; selected = null; connectFrom = null; gesture = null;
    $('map-select').value = id; $('layer').value = 'map.png';
    $('layer').querySelector('[value="reference.png"]').disabled = !data.reference;
    const image = $('base-image'); image.setAttribute('href', imageURL(id, 'map.png'));
    image.setAttribute('width', data.size[0]); image.setAttribute('height', data.size[1]);
    $('empty-state').hidden = true;
    $('dimensions').textContent = `${data.size[0]} × ${data.size[1]} px · 原图坐标`;
    $('map-status').textContent = data.coverage_verified ? '采集覆盖已验证' : '采集覆盖尚未完全验证';
    $('package-path').textContent = data.path;
    fit(); render(); message('地图已打开。选择工具绘制，Ctrl+S 保存到地图目录。');
  } catch (error) {
    if (current) $('map-select').value = current.id;
    message(`打开失败：${error.message}`, 'error');
  } finally { busy = false; syncState(); }
}

function syncState() {
  const loaded = Boolean(current), changed = dirty();
  $('save-state').textContent = saving ? '正在保存…' : !loaded ? '未打开地图' : changed ? '有未保存的修改' : '已与磁盘同步';
  $('save-state').classList.toggle('dirty', Boolean(changed));
  $('save').disabled = !loaded || saving || busy;
  $('map-select').disabled = busy || saving;
  $('refresh-maps').disabled = busy || saving;
  $('reload').disabled = !loaded || busy || saving;
  $('download').disabled = !loaded;
  $('undo').disabled = !history.length || busy;
  $('redo').disabled = !future.length || busy;
  const minimum = tool === 'polygon' ? 3 : 2;
  $('finish').disabled = !['polyline', 'polygon'].includes(tool) || draft.length < minimum;
  $('cancel').disabled = !draft.length && !connectFrom && !gesture;
  const hint = $('drawing-hint');
  hint.hidden = !draft.length && !connectFrom;
  hint.textContent = connectFrom ? `已选 ${itemById(connectFrom)?.label || connectFrom}，点击目标对象完成连接 · Esc 取消`
    : `${draft.length} 个顶点 · 继续单击添加 · 双击 / Enter 完成 · Esc 取消`;
}

function remember(previous) {
  if (snapshot() === previous) return;
  history.push(previous); if (history.length > 100) history.shift(); future = [];
}
function change(action) { if (!doc || busy) return; const previous = snapshot(); action(); remember(previous); render(); }
function undo() {
  if (gesture) { cancelDraft(); return; }
  if (draft.length) { draft.pop(); renderGeometry(); syncState(); return; }
  if (!history.length || busy) return;
  future.push(snapshot()); doc = JSON.parse(history.pop()); render();
}
function redo() {
  if (!future.length || busy || gesture) return;
  history.push(snapshot()); doc = JSON.parse(future.pop()); render();
}

function cancelDraft() {
  if (gesture?.previous) doc = JSON.parse(gesture.previous);
  gesture = null; draft = []; connectFrom = null; render();
}
function setTool(next) {
  if (gesture) cancelDraft();
  if (draft.length && next !== tool) { message('请先完成当前绘制，或按 Esc 取消。'); return; }
  tool = next; connectFrom = null;
  if (defaults[next]) $('show-annotations').checked = true;
  if (defaults[next]) { $('new-label').value = defaults[next][0]; $('new-color').value = defaults[next][1]; }
  for (const button of document.querySelectorAll('[data-tool]')) button.setAttribute('aria-pressed', String(button.dataset.tool === tool));
  svg.dataset.tool = tool; renderGeometry(); syncState();
}

function addObject(type, points) {
  const item = { id: uniqueId('obj'), type, label: $('new-label').value.trim() || typeNames[type],
    points: points.map(bounded), color: $('new-color').value, note: '' };
  change(() => { doc.objects.push(item); selected = item.id; draft = []; });
  message(`已添加${typeNames[type]}「${item.label}」。Ctrl+S 保存。`);
}
function finishDraft() {
  const minimum = tool === 'polygon' ? 3 : 2;
  if (draft.length < minimum) { message(`至少需要 ${minimum} 个不同的顶点。`, 'error'); return false; }
  addObject(tool, draft); return true;
}
function connectTo(id) {
  if (!id || !doc.objects.some(item => item.id === id)) { message('请点击点、折线或区域对象作为连接端点。'); return; }
  if (!connectFrom) { connectFrom = id; renderGeometry(); syncState(); return; }
  if (connectFrom === id) { message('请选择另一个对象。'); return; }
  const directed = $('new-directed').checked;
  if (doc.connections.some(item => item.directed === directed && ((item.from === connectFrom && item.to === id)
    || (!directed && item.to === connectFrom && item.from === id)))) {
    message('这两个对象之间已有相同方向的连接。', 'error'); return;
  }
  change(() => {
    const item = { id: uniqueId('edge'), from: connectFrom, to: id, directed,
      label: $('new-label').value.trim() || '通路', color: $('new-color').value, note: '' };
    doc.connections.push(item); selected = item.id; connectFrom = null;
  });
  message(directed ? '已创建单向连接，箭头指向目标对象。' : '已创建双向连接。');
}

function fit() {
  if (!current) return;
  const rect = svg.getBoundingClientRect();
  view.scale = Math.max(.03, Math.min((rect.width - 48) / current.size[0], (rect.height - 48) / current.size[1]));
  view.x = (rect.width - current.size[0] * view.scale) / 2;
  view.y = (rect.height - current.size[1] * view.scale) / 2;
  renderGeometry();
}
function zoomTo(scale, anchor) {
  if (!current) return;
  const rect = svg.getBoundingClientRect();
  const a = anchor || [rect.width / 2, rect.height / 2];
  const next = Math.max(.03, Math.min(16, scale));
  view.x = a[0] - (a[0] - view.x) * next / view.scale;
  view.y = a[1] - (a[1] - view.y) * next / view.scale; view.scale = next;
  renderGeometry();
}
function drawLabel(parent, item, point) {
  if (!$('show-labels').checked) return;
  const label = element('text', { x: point[0] + 11 / view.scale, y: point[1] - 11 / view.scale,
    fill: '#f2f5f8', 'font-size': 12 / view.scale, stroke: '#101317', 'stroke-width': 3 / view.scale,
    'paint-order': 'stroke', 'pointer-events': 'none' }, item.label || item.id);
  parent.append(label);
}
function renderGeometry() {
  $('scene').setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.scale})`);
  $('zoom').textContent = `${Math.round(view.scale * 100)}%`;
  for (const id of ['objects', 'connections', 'handles', 'draft']) $(id).replaceChildren();
  if (!doc || !$('show-annotations').checked) return;
  const z = view.scale;
  for (const item of doc.connections) {
    const a = center(itemById(item.from)), b = center(itemById(item.to));
    const path = `M ${a.join(' ')} L ${b.join(' ')}`;
    const group = element('g', { 'data-connection': item.id });
    group.append(element('path', { d: path, fill: 'none', stroke: 'transparent', 'stroke-width': 14 / z }));
    group.append(element('path', { d: path, fill: 'none', stroke: item.id === selected ? '#ffffff' : colorOf(item),
      'stroke-width': (item.id === selected ? 3 : 2) / z, 'stroke-dasharray': `${7 / z} ${4 / z}`,
      ...(item.directed ? { 'marker-end': 'url(#arrow)' } : {}), 'pointer-events': 'none' }));
    drawLabel(group, item, center(item)); $('connections').append(group);
  }
  for (const item of doc.objects) {
    const color = colorOf(item), active = item.id === selected || item.id === connectFrom;
    const group = element('g', { 'data-object': item.id });
    if (item.type === 'point') {
      group.append(element('circle', { cx: item.points[0][0], cy: item.points[0][1], r: 12 / z, fill: 'transparent' }));
      group.append(element('circle', { cx: item.points[0][0], cy: item.points[0][1], r: 5 / z,
        fill: color, stroke: active ? '#ffffff' : '#101317', 'stroke-width': 2 / z }));
    } else {
      const points = item.points.map(p => p.join(',')).join(' ');
      if (item.type === 'polyline') group.append(element('polyline', { points, fill: 'none', stroke: 'transparent', 'stroke-width': 14 / z }));
      group.append(element(item.type, { points, fill: item.type === 'polygon' ? color : 'none', 'fill-opacity': .13,
        stroke: active ? '#ffffff' : color, 'stroke-width': (active ? 3 : 2) / z, 'stroke-linejoin': 'round' }));
    }
    drawLabel(group, item, item.type === 'point' ? item.points[0] : center(item)); $('objects').append(group);
  }
  const selectedObject = selectedItem();
  if (tool === 'select' && selectedObject?.points) selectedObject.points.forEach((point, index) => {
    $('handles').append(element('circle', { cx: point[0], cy: point[1], r: 6 / z, fill: '#ffffff',
      stroke: colorOf(selectedObject), 'stroke-width': 2 / z, 'data-vertex': index, 'data-object': selectedObject.id }));
  });
  let preview = draft;
  if (gesture?.kind === 'rectangle') {
    const a = gesture.start, b = bounded(cursor || a);
    preview = [a, [b[0], a[1]], b, [a[0], b[1]]];
  } else if (draft.length && cursor) preview = [...draft, bounded(cursor)];
  if (preview.length) {
    const polygon = tool === 'polygon' || tool === 'rectangle';
    $('draft').append(element(polygon ? 'polygon' : 'polyline', { points: preview.map(p => p.join(',')).join(' '),
      fill: polygon ? $('new-color').value : 'none', 'fill-opacity': .15, stroke: $('new-color').value,
      'stroke-width': 2 / z, 'stroke-dasharray': `${5 / z} ${4 / z}`, 'pointer-events': 'none' }));
    for (const p of draft) $('draft').append(element('circle', { cx: p[0], cy: p[1], r: 4 / z, fill: '#ffffff', 'pointer-events': 'none' }));
  }
}

function renderList() {
  const list = $('object-list'); list.replaceChildren();
  if (!doc) return;
  const items = [...doc.objects, ...doc.connections], query = $('search').value.trim().toLowerCase();
  $('object-count').textContent = `${doc.objects.length} / ${doc.connections.length} 连线`;
  const options = [...new Set([...labels, ...items.map(item => item.label).filter(Boolean)])];
  $('label-options').replaceChildren(...options.map(value => new Option(value, value)));
  for (const item of items) {
    if (query && !`${item.label} ${item.id} ${typeNames[kindOf(item)]}`.toLowerCase().includes(query)) continue;
    const button = document.createElement('button'); button.type = 'button'; button.dataset.selectId = item.id;
    button.classList.toggle('selected', selected === item.id);
    button.setAttribute('aria-pressed', String(selected === item.id));
    const swatch = document.createElement('span'); swatch.className = 'swatch'; swatch.style.background = colorOf(item);
    const title = document.createElement('span'); title.className = 'item-name'; title.textContent = item.label || '未命名';
    const id = document.createElement('small'); id.textContent = item.id; title.append(id);
    const type = document.createElement('span'); type.className = 'item-type'; type.textContent = typeNames[kindOf(item)];
    button.append(swatch, title, type);
    button.addEventListener('click', () => { selected = item.id; render(); }); list.append(button);
  }
  if (!list.children.length) { const empty = document.createElement('p'); empty.className = 'muted empty-list';
    empty.textContent = query ? '没有匹配的标注。' : '暂无标注。选择左侧工具在图上绘制。'; list.append(empty); }
}
function renderProperties() {
  const item = selectedItem();
  $('properties').hidden = !item; $('no-selection').hidden = Boolean(item); $('locate').disabled = !item;
  if (!item) return;
  $('selected-id').textContent = item.id; $('selected-type').textContent = typeNames[kindOf(item)];
  $('edit-label').value = item.label || ''; $('edit-note').value = item.note || ''; $('edit-color').value = colorOf(item);
  $('connection-properties').hidden = Boolean(item.points); $('vertex-properties').hidden = !item.points;
  if (!item.points) {
    $('edit-directed').checked = item.directed;
    $('connection-endpoints').textContent = `${itemById(item.from).label || item.from} ${item.directed ? '→' : '↔'} ${itemById(item.to).label || item.to}`;
    return;
  }
  $('add-vertex').hidden = item.type === 'point'; $('vertices').replaceChildren();
  const minimum = { point: 1, polyline: 2, polygon: 3 }[item.type];
  item.points.forEach((point, index) => {
    const row = document.createElement('div'); row.className = 'vertex-row';
    const number = document.createElement('span'); number.textContent = index + 1; row.append(number);
    point.forEach((value, axis) => {
      const input = document.createElement('input'); input.type = 'number'; input.step = '.1';
      input.min = 0; input.max = current.size[axis] - 1; input.value = value;
      input.setAttribute('aria-label', `顶点 ${index + 1} ${axis === 0 ? 'X' : 'Y'}`);
      input.addEventListener('change', () => {
        if (input.value === '' || !input.checkValidity()) { message('坐标必须在原图范围内。', 'error'); input.value = value; return; }
        change(() => { item.points[index][axis] = Math.round(Number(input.value) * 10) / 10; });
      }); row.append(input);
    });
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '×';
    remove.setAttribute('aria-label', `删除顶点 ${index + 1}`); remove.disabled = item.points.length <= minimum;
    remove.addEventListener('click', () => change(() => item.points.splice(index, 1))); row.append(remove);
    $('vertices').append(row);
  });
}
function render() {
  if (!selectedItem()) selected = null;
  renderGeometry(); renderList(); renderProperties(); syncState();
}

svg.addEventListener('pointerdown', event => {
  if (!doc || busy || event.button > 1) return;
  svg.focus({ preventScroll: true });
  const p = localPoint(event);
  if (event.button === 1 || space || tool === 'pan') {
    event.preventDefault(); gesture = { kind: 'pan', start: [event.clientX, event.clientY], x: view.x, y: view.y };
  } else if (tool === 'rectangle' && inside(p) && $('show-annotations').checked) {
    gesture = { kind: 'rectangle', start: bounded(p) }; cursor = bounded(p);
  } else if (tool === 'select' && $('show-annotations').checked) {
    const target = event.target.closest('[data-object], [data-connection]');
    selected = target?.dataset.object || target?.dataset.connection || null;
    const item = selectedItem();
    if (item?.points) gesture = { kind: event.target.hasAttribute('data-vertex') ? 'vertex' : 'object',
      index: Number(event.target.getAttribute('data-vertex')), start: p, previous: snapshot(), points: item.points.map(a => [...a]) };
    render();
  }
  if (gesture) { svg.setPointerCapture(event.pointerId); event.preventDefault(); }
});
svg.addEventListener('pointermove', event => {
  if (!current) return;
  const p = localPoint(event); cursor = p;
  $('coordinates').textContent = inside(p) ? `X ${p[0].toFixed(1)} · Y ${p[1].toFixed(1)}` : 'X — · Y —';
  if (gesture?.kind === 'pan') {
    view.x = gesture.x + event.clientX - gesture.start[0]; view.y = gesture.y + event.clientY - gesture.start[1];
  } else if (gesture?.kind === 'vertex') {
    selectedItem().points[gesture.index] = bounded(p);
  } else if (gesture?.kind === 'object') {
    let delta = p.map((v, i) => v - gesture.start[i]);
    delta = delta.map((v, axis) => Math.max(-Math.min(...gesture.points.map(q => q[axis])),
      Math.min(current.size[axis] - 1 - Math.max(...gesture.points.map(q => q[axis])), v)));
    selectedItem().points = gesture.points.map(q => bounded([q[0] + delta[0], q[1] + delta[1]]));
  }
  if (gesture || draft.length) renderGeometry();
});
svg.addEventListener('pointerup', event => {
  if (!gesture) return;
  const ending = gesture; gesture = null;
  if (svg.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
  if (ending.kind === 'rectangle') {
    const a = ending.start, b = bounded(localPoint(event));
    if (Math.abs(a[0] - b[0]) * view.scale >= 3 && Math.abs(a[1] - b[1]) * view.scale >= 3)
      addObject('polygon', [a, [b[0], a[1]], b, [a[0], b[1]]]);
    else { message('矩形太小，请拖出一个区域。'); renderGeometry(); }
  } else if (ending.previous) { remember(ending.previous); render(); }
  syncState();
});
svg.addEventListener('pointercancel', cancelDraft);
svg.addEventListener('click', event => {
  if (!doc || busy || space || event.button !== 0 || event.detail > 1 || !$('show-annotations').checked) return;
  const p = localPoint(event);
  if (tool === 'connect') { connectTo(event.target.closest('[data-object]')?.dataset.object); return; }
  if (!inside(p)) return;
  if (tool === 'point') addObject('point', [p]);
  if (tool === 'polyline' || tool === 'polygon') {
    const point = bounded(p);
    if (!draft.some(q => q[0] === point[0] && q[1] === point[1])) draft.push(point);
    renderGeometry(); syncState();
  }
});
svg.addEventListener('dblclick', event => {
  if (['polyline', 'polygon'].includes(tool) && draft.length) { event.preventDefault(); finishDraft(); }
});
svg.addEventListener('wheel', event => {
  event.preventDefault(); const rect = svg.getBoundingClientRect();
  zoomTo(view.scale * Math.exp(-Math.max(-500, Math.min(500, event.deltaY)) * .0015), [event.clientX - rect.left, event.clientY - rect.top]);
}, { passive: false });
svg.addEventListener('contextmenu', event => { event.preventDefault(); cancelDraft(); });

async function save() {
  if (!doc || busy || saving) return;
  document.activeElement?.blur();
  if (draft.length && !finishDraft()) return;
  const content = snapshot(); saving = true; syncState();
  try {
    const result = await api('/api/save', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': token },
      body: JSON.stringify({ id: current.id, annotations: JSON.parse(content), revision: current.revision }) });
    current.revision = result.revision; saved = content;
    message(`已保存 ${JSON.parse(content).objects.length} 个对象、${JSON.parse(content).connections.length} 条连接。${result.backup ? '原文件已备份。' : ''}`, 'success');
  } catch (error) { message(`保存失败：${error.message}`, 'error'); }
  finally { saving = false; syncState(); }
}
function deleteSelected() {
  if (!selectedItem()) return;
  const removed = doc.connections.filter(item => item.from === selected || item.to === selected).length;
  change(() => { doc.objects = doc.objects.filter(item => item.id !== selected);
    doc.connections = doc.connections.filter(item => item.id !== selected && item.from !== selected && item.to !== selected); selected = null; });
  message(`标注已删除${removed ? `，同时移除 ${removed} 条相关连接` : ''}。可按 Ctrl+Z 撤销。`);
}

for (const button of document.querySelectorAll('[data-tool]')) button.addEventListener('click', () => setTool(button.dataset.tool));
for (const [id, field] of [['edit-label', 'label'], ['edit-note', 'note'], ['edit-color', 'color'], ['edit-directed', 'directed']]) {
  $(id).addEventListener('change', () => { const item = selectedItem(); if (!item) return;
    change(() => { item[field] = field === 'directed' ? $(id).checked : $(id).value; }); });
}
$('properties').addEventListener('submit', event => event.preventDefault());
$('add-vertex').addEventListener('click', () => {
  const item = selectedItem(); if (!item?.points || item.type === 'point') return;
  change(() => { const index = item.type === 'polygon' ? item.points.length : item.points.length - 1;
    const a = item.points[index - 1], b = item.points[index % item.points.length];
    item.points.splice(index, 0, bounded([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])); });
});
$('map-select').addEventListener('change', () => openMap($('map-select').value));
$('refresh-maps').addEventListener('click', () => refreshMaps());
$('reload').addEventListener('click', () => current && openMap(current.id));
$('save').addEventListener('click', save);
$('undo').addEventListener('click', undo); $('redo').addEventListener('click', redo);
$('finish').addEventListener('click', finishDraft); $('cancel').addEventListener('click', cancelDraft);
$('delete').addEventListener('click', deleteSelected); $('fit').addEventListener('click', fit);
$('actual-size').addEventListener('click', () => zoomTo(1));
$('zoom-in').addEventListener('click', () => zoomTo(view.scale * 1.25));
$('zoom-out').addEventListener('click', () => zoomTo(view.scale / 1.25));
$('locate').addEventListener('click', () => { const item = selectedItem(); if (!item) return;
  const p = center(item), rect = svg.getBoundingClientRect();
  view.x = rect.width / 2 - p[0] * view.scale; view.y = rect.height / 2 - p[1] * view.scale; renderGeometry(); });
$('search').addEventListener('input', renderList);
$('layer').addEventListener('change', async () => {
  if (!current) return; const id = current.id, filename = $('layer').value;
  try { await loadImage(imageURL(id, filename));
    if (current.id === id && $('layer').value === filename) $('base-image').setAttribute('href', imageURL(id, filename));
  } catch (error) { message(error.message, 'error'); }
});
for (const id of ['show-labels', 'show-annotations', 'new-color']) $(id).addEventListener('input', renderGeometry);
$('download').addEventListener('click', () => {
  if (!doc) return; document.activeElement?.blur();
  if (draft.length && !finishDraft()) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2) + '\n'], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = `chapter_${current.chapter ?? 'map'}_annotations.json`;
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.addEventListener('keydown', event => {
  const editing = event.target.closest('input, textarea, select, [contenteditable]');
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save(); return; }
  if (editing) return;
  if (event.code === 'Space') { space = true; event.preventDefault(); }
  if (event.key === 'Escape') { event.preventDefault(); cancelDraft(); }
  if (event.key === 'Enter' && draft.length) { event.preventDefault(); finishDraft(); }
  if (event.key === 'Backspace' && draft.length) { event.preventDefault(); draft.pop(); renderGeometry(); syncState(); }
  if (event.key === 'Delete') { event.preventDefault(); deleteSelected(); }
  if (event.ctrlKey || event.metaKey) {
    if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo(); }
    if (event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); }
    return;
  }
  const keys = { v: 'select', h: 'pan', p: 'point', l: 'polyline', g: 'polygon', r: 'rectangle', c: 'connect' };
  if (keys[event.key.toLowerCase()]) setTool(keys[event.key.toLowerCase()]);
});
document.addEventListener('keyup', event => { if (event.code === 'Space') space = false; });
window.addEventListener('blur', () => { space = false; if (gesture) cancelDraft(); });
window.addEventListener('beforeunload', event => { if (dirty() || draft.length) { event.preventDefault(); event.returnValue = ''; } });
new ResizeObserver(() => { if (current) renderGeometry(); }).observe($('stage'));
setTool('select'); refreshMaps(true);
