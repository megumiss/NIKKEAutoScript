'use strict';

const $ = id => document.getElementById(id);
const svg = $('canvas');
const NS = 'http://www.w3.org/2000/svg';
const typeNames = { point: '点', polyline: '折线', polygon: '区域', connection: '连接' };
const categories = {
  normal_collectible: { name: '普通收集品', color: '#ffd166', difficulty: 'normal', help: '单击标记普通收集品；与困难收集品分开显示。' },
  hard_collectible: { name: '困难收集品', color: '#c792ea', difficulty: 'hard', help: '单击标记困难收集品；与普通收集品分开显示。' },
  ground_mechanism: { name: '地面机关', color: '#70dfa0', help: '标记地面机关的位置，也可使用区域绘制范围。' },
  ground_elevator: { name: '地面电梯', color: '#6ccfff', help: '先标记两部电梯，再选择“电梯传送关系”连接。' },
  elevator_connection: { name: '电梯传送关系', color: '#ffb454', help: '依次点击起点电梯、目标电梯；默认双向，可勾选单向。' },
};
let current = null, doc = null, token = '', saved = '', selected = null, tool = 'select';
let newCategory = 'normal_collectible', difficulty = 'normal';
let history = [], future = [], draft = [], connectFrom = null, gesture = null, cursor = null;
let space = false, busy = false, saving = false, view = { x: 0, y: 0, scale: 1 };
const movement = createMovementController({ api, map: () => current, token: () => token,
  selected: selectedItem, busy: () => busy || saving || scan.running || dirty() || Boolean(draft.length || gesture),
  selectTool: setTool, redraw: renderGeometry, sync: syncState });
const scan = createScanController({ api, token: () => token, moving: () => movement.running,
  busy: () => busy || saving || Boolean(draft.length || gesture), sync: syncState,
  refresh: refreshMaps, open: openMap });
const wiki = createWikiController({ api, token: () => token, map: () => current, save, dirty,
  busy: () => busy || saving || movement.running || Boolean(draft.length || gesture),
  sync: syncState, reload: () => openMap(current.id) });
const connectivity = createConnectivityController({ api, token: () => token, map: () => current, doc: () => doc,
  change, busy: () => busy || saving || movement.running });

function element(name, attributes = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
function message(text, kind = '') { $('message').textContent = text; $('message').className = kind; }
function snapshot() { return JSON.stringify(doc); }
function roadTool() { return tool.startsWith('road-'); }
function roadOperation() { return tool === 'road-erase' ? 'erase' : tool === 'road-polygon' ? $('road-operation').value : 'add'; }
function roadColor(operation) { return current?.terrain_colors?.[operation] || (operation === 'add' ? '#3b8bba' : '#1c232c'); }
function dirty() { return doc && snapshot() !== saved; }
function itemById(id) { return doc && [...doc.objects, ...doc.connections].find(item => item.id === id); }
function selectedItem() { return itemById(selected); }
function kindOf(item) { return item.type || 'connection'; }
function categoryOf(item) {
  if (!item) return null;
  if (item.category) return item.category;
  if (!item.points) return null;
  // Wiki 旧数据没有 category；保留原文件，用已有难度或明确标签识别。
  const mode = item.difficulty ?? item.source?.difficulty;
  if (mode === 'normal' || mode === 'hard') return `${mode}_collectible`;
  if (/^普通收集[品物](?:\s|$)/u.test(item.label)) return 'normal_collectible';
  if (/^困难收集[品物](?:\s|$)/u.test(item.label)) return 'hard_collectible';
  if (/^地面机关(?:\s|$)/u.test(item.label)) return 'ground_mechanism';
  if (/^地面电梯(?:\s|$)/u.test(item.label)) return 'ground_elevator';
  return null;
}
function categoryName(item) { return categories[categoryOf(item)]?.name || '旧标注（未分类）'; }
function colorOf(item) {
  return item.color || categories[categoryOf(item)]?.color
    || { point: '#ffb454', polyline: '#70dfa0', polygon: '#ff7b91' }[item.type] || '#d6dce5';
}
function visible(item) {
  if (!item) return false;
  if (!item.points) return visible(itemById(item.from)) && visible(itemById(item.to));
  const mode = categories[categoryOf(item)]?.difficulty;
  return !mode || difficulty === 'all' || mode === difficulty;
}
function applyCategory(item, category) {
  const previous = categoryOf(item), settings = categories[category];
  if (!item.label || item.label === categories[previous]?.name) item.label = settings.name;
  else if (/^(?:普通|困难)收集[品物](?:\s|$)/u.test(item.label))
    item.label = item.label.replace(/^(?:普通|困难)收集[品物]/u, settings.name);
  item.category = category; item.color = settings.color;
  if (settings.difficulty) item.difficulty = settings.difficulty;
  else delete item.difficulty;
}
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
  if (saving || busy || movement.running) return;
  if ((dirty() || draft.length) && !confirm('当前有未保存的标注。放弃这些修改并打开地图？')) {
    $('map-select').value = current.id; return;
  }
  busy = true; syncState(); message('正在加载地图…');
  try {
    const data = await api(`/api/map?${new URLSearchParams({ map: id })}`);
    await loadImage(imageURL(id, 'map.png'));
    current = data; doc = data.annotations; saved = snapshot();
    connectivity.opened(data.connectivity);
    history = []; future = []; draft = []; selected = null; connectFrom = null; gesture = null;
    movement.reset();
    scan.mapOpened(data);
    $('map-select').value = id; $('layer').value = 'map.png';
    $('layer').querySelector('[value="reference.png"]').disabled = !data.reference;
    const image = $('base-image'); image.setAttribute('href', imageURL(id, 'map.png'));
    image.setAttribute('width', data.size[0]); image.setAttribute('height', data.size[1]);
    $('terrain-bounds').setAttribute('width', data.size[0]); $('terrain-bounds').setAttribute('height', data.size[1]);
    $('terrain-export-link').hidden = true;
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
  $('save').disabled = !loaded || saving || busy || movement.running || Boolean(gesture);
  $('export-terrain').disabled = !loaded || saving || busy || movement.running || Boolean(gesture)
    || (!(doc?.terrain_edits?.length) && !(tool === 'road-polygon' && draft.length >= 3));
  $('road-count').textContent = `${doc?.terrain_edits?.length || 0} 笔道路修订`;
  $('map-select').disabled = busy || saving || movement.running;
  $('refresh-maps').disabled = busy || saving || movement.running;
  $('reload').disabled = !loaded || busy || saving || movement.running;
  $('properties').inert = movement.running;
  $('download').disabled = !loaded;
  $('undo').disabled = !history.length || busy || movement.running;
  $('redo').disabled = !future.length || busy || movement.running;
  const minimum = ['polygon', 'road-polygon'].includes(tool) ? 3 : 2;
  $('finish').disabled = !['polyline', 'polygon', 'road-polygon'].includes(tool) || draft.length < minimum;
  if (tool.startsWith('connectivity-')) $('finish').disabled = draft.length < 2;
  $('cancel').disabled = !draft.length && !connectFrom && !gesture;
  const hint = $('drawing-hint');
  hint.hidden = !draft.length && !connectFrom;
  hint.textContent = connectFrom ? `已选 ${itemById(connectFrom)?.label || connectFrom}，点击目标电梯完成传送关系 · Esc 取消`
    : `${draft.length} 个顶点 · 继续单击添加 · 双击 / Enter 完成 · Esc 取消`;
  movement.sync();
  scan.sync();
  wiki.sync();
  connectivity.sync();
  const tasks = [scan.running && '扫描中', movement.running && '小队移动中', wiki.running && 'Wiki 匹配中'].filter(Boolean);
  $('task-summary').textContent = tasks.join(' · ') || '当前无运行任务';
}

function remember(previous) {
  if (snapshot() === previous) return;
  history.push(previous); if (history.length > 100) history.shift(); future = [];
}
function change(action) { if (!doc || busy || movement.running) return; const previous = snapshot(); action(); remember(previous); render(); }
function undo() {
  if (gesture) { cancelDraft(); return; }
  if (draft.length) { draft.pop(); renderGeometry(); syncState(); return; }
  if (!history.length || busy || movement.running) return;
  future.push(snapshot()); doc = JSON.parse(history.pop()); render();
}
function redo() {
  if (!future.length || busy || gesture || movement.running) return;
  history.push(snapshot()); doc = JSON.parse(future.pop()); render();
}

function cancelDraft() {
  if (gesture?.previous) doc = JSON.parse(gesture.previous);
  gesture = null; draft = []; connectFrom = null; render();
}
function setTool(next) {
  if (gesture) cancelDraft();
  if (draft.length && next !== tool) { message('请先完成当前绘制，或按 Esc 取消。'); return; }
  if (next === 'connect' && newCategory !== 'elevator_connection') { setCategory('elevator_connection'); return; }
  if (['point', 'polyline', 'polygon', 'rectangle'].includes(next) && newCategory === 'elevator_connection') {
    message('当前类型是电梯传送关系。请先切换到收集品、地面机关或地面电梯。'); return;
  }
  tool = next; connectFrom = null;
  if (roadTool()) { $('show-terrain').checked = true; selected = null; $('road-tools').open = true; }
  if (next.startsWith('connectivity-')) {
    $('show-connectivity').checked = true; $('connectivity-tools').open = true; selected = null;
  }
  if (!['select', 'pan'].includes(next)) $('show-annotations').checked = true;
  for (const button of document.querySelectorAll('[data-tool]')) button.setAttribute('aria-pressed', String(button.dataset.tool === tool));
  svg.dataset.tool = tool; movement.toolChanged(tool); renderGeometry(); syncState();
}
function syncCategory() {
  const settings = categories[newCategory];
  $('new-category').value = newCategory;
  $('category-help').textContent = settings.help;
  $('new-direction').hidden = newCategory !== 'elevator_connection';
  for (const button of document.querySelectorAll('[data-tool]')) {
    button.disabled = newCategory === 'elevator_connection' && ['point', 'polyline', 'polygon', 'rectangle'].includes(button.dataset.tool);
  }
  for (const button of document.querySelectorAll('[data-difficulty]')) {
    button.setAttribute('aria-pressed', String(button.dataset.difficulty === difficulty));
  }
}
function setCategory(next) {
  if (draft.length || gesture) {
    $('new-category').value = newCategory;
    message('请先完成当前绘制，或按 Esc 取消，再切换类型。'); return;
  }
  newCategory = next;
  const settings = categories[next];
  $('new-label').value = settings.name; $('new-color').value = settings.color;
  if (settings.difficulty && difficulty !== 'all') difficulty = settings.difficulty;
  syncCategory(); setTool(next === 'elevator_connection' ? 'connect' : 'point'); render();
}
function setDifficulty(next) {
  if (draft.length || gesture) { message('请先完成当前绘制，或按 Esc 取消，再切换难度。'); return; }
  difficulty = next; connectFrom = null;
  if (next !== 'all' && categories[newCategory].difficulty && categories[newCategory].difficulty !== next) {
    newCategory = `${next}_collectible`;
    $('new-label').value = categories[newCategory].name; $('new-color').value = categories[newCategory].color;
  }
  syncCategory(); render();
}

function addObject(type, points) {
  const item = { id: uniqueId('obj'), type, category: newCategory,
    ...(categories[newCategory].difficulty ? { difficulty: categories[newCategory].difficulty } : {}),
    label: $('new-label').value.trim() || categories[newCategory].name,
    points: points.map(bounded), color: $('new-color').value, note: '' };
  change(() => { doc.objects.push(item); selected = item.id; draft = []; });
  message(`已添加${typeNames[type]}「${item.label}」。Ctrl+S 保存。`);
}
function finishDraft() {
  const minimum = ['polygon', 'road-polygon'].includes(tool) ? 3 : 2;
  if (draft.length < minimum) { message(`至少需要 ${minimum} 个不同的顶点。`, 'error'); return false; }
  if (tool.startsWith('connectivity-')) {
    const width = Number($('connectivity-width').value);
    if (!Number.isInteger(width) || width < 8 || width > 160) {
      message('分隔线宽度必须为 8～160 地图像素的整数。', 'error'); return false;
    }
    change(() => {
      (doc.connectivity_edits ??= []).push({ id: uniqueId('cut'), operation: 'cut', points: draft.map(bounded), width });
      draft = [];
    });
    message('连通修订已加入预览，Ctrl+S 保存；可按 Ctrl+Z 撤销。'); return true;
  }
  if (tool === 'road-polygon') {
    change(() => { (doc.terrain_edits ??= []).push({ type: 'polygon', operation: roadOperation(), points: draft.map(bounded) }); draft = []; });
    message('已填充道路区域。Ctrl+Z 撤销，Ctrl+S 保存。'); return true;
  }
  addObject(tool, draft); return true;
}
function connectTo(id) {
  if (!id || categoryOf(itemById(id)) !== 'ground_elevator') { message('传送关系只能连接两部地面电梯，请点击已标注的电梯。', 'error'); return; }
  if (!connectFrom) { connectFrom = id; renderGeometry(); syncState(); return; }
  if (connectFrom === id) { message('请选择另一个对象。'); return; }
  const directed = $('new-directed').checked;
  if (doc.connections.some(item => item.directed === directed && ((item.from === connectFrom && item.to === id)
    || (!directed && item.to === connectFrom && item.from === id)))) {
    message('这两个对象之间已有相同方向的连接。', 'error'); return;
  }
  change(() => {
    const item = { id: uniqueId('edge'), category: 'elevator_connection', from: connectFrom, to: id, directed,
      label: $('new-label').value.trim() || categories.elevator_connection.name, color: $('new-color').value, note: '' };
    itemById(connectFrom).category = 'ground_elevator'; itemById(id).category = 'ground_elevator';
    doc.connections.push(item); selected = item.id; connectFrom = null;
  });
  message(directed ? '已创建单向电梯传送，箭头指向目标电梯。' : '已创建双向电梯传送。');
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
function renderTerrain() {
  $('terrain').replaceChildren(); $('terrain-preview').replaceChildren();
  if (!doc || !$('show-terrain').checked) return;
  $('terrain').setAttribute('opacity', $('layer').value === 'reference.png' ? '.55' : '1');
  for (const edit of doc.terrain_edits || []) {
    const color = roadColor(edit.operation), points = edit.points.map(p => p.join(',')).join(' ');
    if (edit.type === 'polygon') $('terrain').append(element('polygon', { points, fill: color }));
    else if (edit.points.length === 1) $('terrain').append(element('circle', {
      cx: edit.points[0][0], cy: edit.points[0][1], r: edit.width / 2, fill: color }));
    else $('terrain').append(element('polyline', { points, fill: 'none', stroke: color,
      'stroke-width': edit.width, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  }
  if (tool === 'road-polygon' && draft.length) {
    const points = [...draft, ...(cursor && inside(cursor) ? [bounded(cursor)] : [])];
    $('terrain-preview').append(element('polygon', { points: points.map(p => p.join(',')).join(' '),
      fill: roadColor(roadOperation()), 'fill-opacity': .5, stroke: '#ffffff', 'stroke-width': 1 / view.scale }));
    for (const p of draft) $('terrain-preview').append(element('circle', { cx: p[0], cy: p[1], r: 3 / view.scale, fill: '#fff' }));
  } else if (['road-brush', 'road-erase'].includes(tool) && cursor && inside(cursor)) {
    $('terrain-preview').append(element('circle', { cx: cursor[0], cy: cursor[1], r: Number($('road-width').value) / 2,
      fill: 'none', stroke: '#ffffff', 'stroke-width': 1 / view.scale }));
  }
}
function renderGeometry() {
  $('scene').setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.scale})`);
  $('zoom').textContent = `${Math.round(view.scale * 100)}%`;
  for (const id of ['objects', 'connections', 'handles', 'draft']) $(id).replaceChildren();
  renderTerrain();
  connectivity.draw(view.scale);
  movement.draw(view.scale);
  if (!doc || !$('show-annotations').checked) return;
  const z = view.scale;
  for (const item of doc.connections) {
    if (!visible(item)) continue;
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
    if (!visible(item)) continue;
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
  if (preview.length && !roadTool()) {
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
  $('object-count').textContent = `${items.filter(visible).length} / ${items.length}`;
  const normal = doc.objects.filter(item => categoryOf(item) === 'normal_collectible').length;
  const hard = doc.objects.filter(item => categoryOf(item) === 'hard_collectible').length;
  $('difficulty-summary').textContent = `普通 ${normal} · 困难 ${hard} · 机关与电梯共用`;
  const options = [...new Set([...Object.values(categories).map(item => item.name), ...items.map(item => item.label).filter(Boolean)])];
  $('label-options').replaceChildren(...options.map(value => new Option(value, value)));
  for (const item of items) {
    if (!visible(item) || (query && !`${item.label} ${item.id} ${categoryName(item)} ${typeNames[kindOf(item)]}`.toLowerCase().includes(query))) continue;
    const button = document.createElement('button'); button.type = 'button'; button.dataset.selectId = item.id;
    button.classList.toggle('selected', selected === item.id);
    button.setAttribute('aria-pressed', String(selected === item.id));
    const swatch = document.createElement('span'); swatch.className = 'swatch'; swatch.style.background = colorOf(item);
    const title = document.createElement('span'); title.className = 'item-name'; title.textContent = item.label || '未命名';
    const id = document.createElement('small'); id.textContent = item.id; title.append(id);
    const type = document.createElement('span'); type.className = 'item-type'; type.textContent = categories[categoryOf(item)]?.name || typeNames[kindOf(item)];
    button.append(swatch, title, type);
    button.addEventListener('click', () => { selected = item.id; render(); }); list.append(button);
  }
  if (!list.children.length) { const empty = document.createElement('p'); empty.className = 'muted empty-list';
    empty.textContent = query ? '没有匹配的标注。' : '当前难度暂无标注。可切换难度，或选择左侧类型开始标注。'; list.append(empty); }
}
function renderProperties() {
  const item = selectedItem();
  $('properties').hidden = !item; $('no-selection').hidden = Boolean(item); $('locate').disabled = !item;
  if (!item) return;
  $('selected-id').textContent = item.id; $('selected-type').textContent = `${categoryName(item)} · ${typeNames[kindOf(item)]}`;
  const category = categoryOf(item), picker = $('edit-category'); picker.replaceChildren();
  if (!category) { const legacy = new Option('旧标注（未分类）', ''); legacy.disabled = true; picker.add(legacy); }
  for (const [key, settings] of Object.entries(categories)) {
    if (Boolean(item.points) !== (key !== 'elevator_connection')) continue;
    picker.add(new Option(settings.name, key));
  }
  picker.value = category || '';
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
  if (!visible(selectedItem())) selected = null;
  if (connectFrom && (!visible(itemById(connectFrom)) || categoryOf(itemById(connectFrom)) !== 'ground_elevator')) connectFrom = null;
  renderGeometry(); renderList(); renderProperties(); syncState();
}

svg.addEventListener('pointerdown', event => {
  if (!doc || busy || movement.running || event.button > 1) return;
  svg.focus({ preventScroll: true });
  const p = localPoint(event);
  if (event.button === 1 || space || tool === 'pan') {
    event.preventDefault(); gesture = { kind: 'pan', start: [event.clientX, event.clientY], x: view.x, y: view.y };
  } else if (['road-brush', 'road-erase'].includes(tool) && inside(p)) {
    $('show-terrain').checked = true;
    const edit = { type: 'brush', operation: roadOperation(), width: Number($('road-width').value), points: [bounded(p)] };
    gesture = { kind: 'road', previous: snapshot(), edit };
    (doc.terrain_edits ??= []).push(edit); renderGeometry(); syncState();
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
  } else if (gesture?.kind === 'road') {
    const point = bounded(p), previous = gesture.edit.points.at(-1);
    if (Math.hypot(point[0] - previous[0], point[1] - previous[1]) >= .5) gesture.edit.points.push(point);
  } else if (gesture?.kind === 'vertex') {
    selectedItem().points[gesture.index] = bounded(p);
  } else if (gesture?.kind === 'object') {
    let delta = p.map((v, i) => v - gesture.start[i]);
    delta = delta.map((v, axis) => Math.max(-Math.min(...gesture.points.map(q => q[axis])),
      Math.min(current.size[axis] - 1 - Math.max(...gesture.points.map(q => q[axis])), v)));
    selectedItem().points = gesture.points.map(q => bounded([q[0] + delta[0], q[1] + delta[1]]));
  }
  if (gesture || draft.length || roadTool()) renderGeometry();
});
svg.addEventListener('pointerup', event => {
  if (!gesture) return;
  const ending = gesture; gesture = null;
  if (ending.kind === 'road') ending.edit.points.push(bounded(localPoint(event)));
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
  if (!doc || busy || movement.running || space || event.button !== 0 || event.detail > 1) return;
  const p = localPoint(event);
  if (tool === 'move-target') { if (inside(p)) movement.select(bounded(p)); return; }
  if (tool.startsWith('connectivity-') && inside(p)) {
    const point = bounded(p);
    if (!draft.some(q => q[0] === point[0] && q[1] === point[1])) draft.push(point);
    renderGeometry(); syncState();
    return;
  }
  if (tool === 'road-polygon' && inside(p)) {
    const point = bounded(p);
    if (!draft.some(q => q[0] === point[0] && q[1] === point[1])) draft.push(point);
    renderGeometry(); syncState(); return;
  }
  if (!$('show-annotations').checked) return;
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
  if (tool === 'connectivity-cut' && draft.length) { event.preventDefault(); finishDraft(); return; }
  if (['polyline', 'polygon', 'road-polygon'].includes(tool) && draft.length) { event.preventDefault(); finishDraft(); }
});
svg.addEventListener('wheel', event => {
  event.preventDefault(); const rect = svg.getBoundingClientRect();
  zoomTo(view.scale * Math.exp(-Math.max(-500, Math.min(500, event.deltaY)) * .0015), [event.clientX - rect.left, event.clientY - rect.top]);
}, { passive: false });
svg.addEventListener('contextmenu', event => { event.preventDefault(); cancelDraft(); });

async function save() {
  if (!doc || busy || saving || movement.running || gesture) return false;
  document.activeElement?.blur();
  if (draft.length && !finishDraft()) return;
  const content = snapshot(); saving = true; syncState();
  try {
    const result = await api('/api/save', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': token },
      body: JSON.stringify({ id: current.id, annotations: JSON.parse(content), revision: current.revision }) });
    current.revision = result.revision; saved = content;
    current.connectivity = result.connectivity; connectivity.saved(result.connectivity);
    const stored = JSON.parse(content);
    message(`已保存 ${stored.objects.length} 个对象、${stored.connections.length} 条连接、${stored.terrain_edits?.length || 0} 笔道路修订。${result.backup ? '原文件已备份。' : ''}`, 'success');
    return true;
  } catch (error) { message(`保存失败：${error.message}`, 'error'); return false; }
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
for (const button of document.querySelectorAll('[data-difficulty]')) button.addEventListener('click', () => setDifficulty(button.dataset.difficulty));
$('new-category').addEventListener('change', () => setCategory($('new-category').value));
$('edit-category').addEventListener('change', () => {
  const item = selectedItem(), category = $('edit-category').value; if (!item || !categories[category]) return;
  if (draft.length || gesture) {
    message('请先完成当前绘制，或按 Esc 取消，再修改类型。'); renderProperties(); return;
  }
  const related = doc.connections.some(edge => edge.category === 'elevator_connection' && (edge.from === item.id || edge.to === item.id));
  if (related && category !== 'ground_elevator') {
    message('这部电梯已有传送关系，请先删除相关传送关系再修改类型。', 'error'); renderProperties(); return;
  }
  if (category === 'elevator_connection' && [item.from, item.to].some(id => categoryOf(itemById(id)) !== 'ground_elevator')) {
    message('传送关系只能连接两部地面电梯，请先修改端点类型。', 'error'); renderProperties(); return;
  }
  change(() => {
    applyCategory(item, category);
    if (category === 'elevator_connection') {
      itemById(item.from).category = 'ground_elevator'; itemById(item.to).category = 'ground_elevator';
    }
  });
  if (categories[category].difficulty && difficulty !== 'all') { selected = item.id; setDifficulty(categories[category].difficulty); }
});
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
$('road-width').addEventListener('input', () => { $('road-width-value').textContent = `${$('road-width').value} px`; renderGeometry(); });
$('road-operation').addEventListener('change', renderGeometry);
$('show-terrain').addEventListener('change', renderGeometry);
$('export-terrain').addEventListener('click', async () => {
  if (!(await save()) || dirty()) return;
  busy = true; syncState();
  try {
    const result = await api('/api/terrain/export', { method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': token },
      body: JSON.stringify({ id: current.id, revision: current.revision }) });
    const link = $('terrain-export-link');
    link.href = `/api/export-image?${new URLSearchParams({ map: current.id, export: result.export })}`;
    link.hidden = false;
    message(`修订图已导出：${result.path}`, 'success');
  } catch (error) { message(`导出失败：${error.message}`, 'error'); }
  finally { busy = false; syncState(); }
});
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
    if (current.id === id && $('layer').value === filename) { $('base-image').setAttribute('href', imageURL(id, filename)); renderGeometry(); }
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
  if (movement.running) return;
  const editing = event.target.closest('input, textarea, select, [contenteditable]');
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); save(); return; }
  if (editing) return;
  if (event.code === 'Space') { space = true; event.preventDefault(); }
  if (event.key === 'Escape') { event.preventDefault(); cancelDraft(); if (tool === 'move-target') setTool('select'); }
  if (event.key === 'Enter' && draft.length) { event.preventDefault(); finishDraft(); }
  if (event.key === 'Backspace' && draft.length) { event.preventDefault(); draft.pop(); renderGeometry(); syncState(); }
  if (event.key === 'Delete') { event.preventDefault(); deleteSelected(); }
  if (event.ctrlKey || event.metaKey) {
    if (event.key.toLowerCase() === 'z') { event.preventDefault(); event.shiftKey ? redo() : undo(); }
    if (event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); }
    return;
  }
  const keys = { v: 'select', h: 'pan', p: 'point', l: 'polyline', g: 'polygon', r: 'rectangle', c: 'connect',
    b: 'road-brush', e: 'road-erase', f: 'road-polygon' };
  if (keys[event.key.toLowerCase()]) setTool(keys[event.key.toLowerCase()]);
});
document.addEventListener('keyup', event => { if (event.code === 'Space') space = false; });
window.addEventListener('blur', () => { space = false; if (gesture) cancelDraft(); });
window.addEventListener('beforeunload', event => { if (dirty() || draft.length) { event.preventDefault(); event.returnValue = ''; } });
new ResizeObserver(() => { if (current) renderGeometry(); }).observe($('stage'));
for (const [key, settings] of Object.entries(categories)) $('new-category').add(new Option(settings.name, key));
syncCategory(); setTool('select'); refreshMaps(true);
