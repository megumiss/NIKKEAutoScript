'use strict';

const $ = id => document.getElementById(id);
const state = {rows: [], events: {}, selected: null, revision: '', dirty: false, busy: false, nextId: 0};
let browseOffset = 0;
let browseRequest = 0;
const imageUrl = path => `/api/image?path=${encodeURIComponent(path)}`;
const filename = path => path ? path.split(/[/\\]/).pop() : '待补截图';
const current = () => state.rows.find(row => row.id === state.selected);
const eventInfo = row => state.events[row.event_id] || {name: row.event_id, phases: {}};
const phaseInfo = row => eventInfo(row).phases[row.phase] || {label: '未选择阶段', scenes: {}, templates: {}};
const sceneLabel = row => phaseInfo(row).scenes[row.scene]?.label || row.scene;
const kind = row => !row.screenshot_file ? 'missing' : !row.templates.length ? 'unassigned' : 'linked';
const labels = {missing: '缺截图', unassigned: '待补模板', linked: '已关联'};

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function options(select, items, value) {
  select.replaceChildren(...items.map(([key, label]) => new Option(label, key)));
  if (items.some(([key]) => key === value)) select.value = value;
}

function showError(message, target = 'error') {
  $(target).textContent = message || '';
  $(target).hidden = !message;
}

async function api(url, body) {
  const response = await fetch(url, body ? {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  } : {cache: 'no-store'});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}

function documentData() {
  const data = {screenshots: [], missing: []};
  for (const row of state.rows) {
    const {screenshot_file, event_id, phase, scene, templates} = row;
    data[screenshot_file ? 'screenshots' : 'missing'].push({screenshot_file, event_id, phase, scene, templates: [...templates]});
  }
  return data;
}

function replaceRows(data, preferred) {
  state.rows = [...data.screenshots, ...data.missing].map(row => ({...row, id: state.nextId++}));
  const match = preferred && state.rows.find(row => row.screenshot_file === preferred.screenshot_file
    && row.event_id === preferred.event_id && row.phase === preferred.phase && row.scene === preferred.scene);
  state.selected = match?.id ?? state.rows[0]?.id ?? null;
}

function refreshStatus() {
  const missing = state.rows.filter(row => kind(row) === 'missing').length;
  const unassigned = state.rows.filter(row => kind(row) === 'unassigned').length;
  const shots = new Set(state.rows.map(row => row.screenshot_file).filter(Boolean)).size;
  $('summary').textContent = `${shots} 张原图 · ${state.rows.length} 条关联 · ${missing} 组缺图 · ${unassigned} 条待补模板`;
  $('save').disabled = !state.dirty || state.busy;
  $('save').textContent = state.busy ? '正在保存…' : '保存修改';
  $('reload').disabled = !state.revision || state.busy;
  $('download').disabled = !state.revision;
  $('add').disabled = !state.revision || state.busy;
  if (state.dirty) $('status').textContent = '有未保存的修改 · 保存后写回原 JSON';
}

function changed() {
  state.dirty = true;
  showError('');
  refreshStatus();
  renderList();
  const row = current();
  if (row) $('rowState').textContent = labels[kind(row)];
}

function filterOptions() {
  const used = [...new Set(state.rows.map(row => row.event_id))];
  options($('eventFilter'), [['', '所有活动'], ...used.map(id => [id, `${id} · ${state.events[id]?.name || id}`])], $('eventFilter').value);
  const phases = new Map();
  for (const row of state.rows) {
    if (!$('eventFilter').value || row.event_id === $('eventFilter').value) phases.set(row.phase, phaseInfo(row).label);
  }
  options($('phaseFilter'), [['', '所有阶段'], ...phases.entries()], $('phaseFilter').value);
  const scenes = new Map();
  for (const row of state.rows) {
    if ((!$('eventFilter').value || row.event_id === $('eventFilter').value)
      && (!$('phaseFilter').value || row.phase === $('phaseFilter').value)) scenes.set(row.scene, sceneLabel(row));
  }
  options($('sceneFilter'), [['', '所有场景'], ...scenes.entries()], $('sceneFilter').value);
}

function filteredRows() {
  const search = $('search').value.trim().toLowerCase();
  return state.rows.filter(row => (!$('eventFilter').value || row.event_id === $('eventFilter').value)
    && (!$('phaseFilter').value || row.phase === $('phaseFilter').value)
    && (!$('sceneFilter').value || row.scene === $('sceneFilter').value)
    && (!$('kindFilter').value || kind(row) === $('kindFilter').value)
    && (!search || [row.screenshot_file, row.event_id, row.scene, sceneLabel(row), ...row.templates].join(' ').toLowerCase().includes(search)));
}

function renderList() {
  const rows = filteredRows();
  $('count').textContent = `${rows.length} / ${state.rows.length}`;
  $('rows').replaceChildren();
  if (!rows.length) $('rows').append(element('p', 'empty', '没有符合筛选条件的条目。'));
  for (const row of rows) {
    const button = element('button', `shot-item${row.id === state.selected ? ' selected' : ''}`);
    button.setAttribute('aria-pressed', String(row.id === state.selected));
    button.dataset.rowId = row.id;
    let thumb;
    if (row.screenshot_file) {
      thumb = element('img', 'shot-thumb');
      thumb.alt = '';
      thumb.loading = 'lazy';
      thumb.src = imageUrl(row.screenshot_file);
      thumb.addEventListener('error', () => thumb.replaceWith(element('span', 'shot-thumb', '无图')), {once: true});
    } else thumb = element('span', 'shot-thumb', '缺图');
    const copy = element('span', 'shot-copy');
    copy.append(element('strong', '', sceneLabel(row)), element('span', 'shot-file', filename(row.screenshot_file)),
      element('span', 'shot-state', `${row.event_id} · ${phaseInfo(row).label} · ${row.templates.length} 个模板`),
      element('span', `badge${kind(row) === 'linked' ? '' : ' warning'}`, labels[kind(row)]));
    button.append(thumb, copy);
    button.addEventListener('click', () => selectRow(row.id));
    $('rows').append(button);
  }
  const position = rows.findIndex(row => row.id === state.selected);
  $('position').textContent = position < 0 ? '当前条目不在筛选内' : `${position + 1} / ${rows.length}`;
  $('previous').disabled = position <= 0;
  $('next').disabled = position < 0 || position >= rows.length - 1;
}

function selectRow(id) {
  state.selected = id;
  $('allTemplates').checked = false;
  renderList();
  renderEditor();
}

function preview(row) {
  $('screenshot').onload = null;
  $('screenshot').onerror = null;
  $('screenshot').hidden = true;
  $('enlarge').disabled = true;
  $('previewEmpty').hidden = false;
  $('filename').textContent = row?.screenshot_file || '尚未提供截图';
  $('previewEmpty').textContent = row?.screenshot_file ? '加载原图…' : '此条缺少截图，请在右侧选图或填写路径。';
  if (!row?.screenshot_file) {
    $('screenshot').removeAttribute('src');
    return;
  }
  $('screenshot').onload = () => {
    $('screenshot').hidden = false;
    $('previewEmpty').hidden = true;
    $('enlarge').disabled = false;
  };
  $('screenshot').onerror = () => {
    $('screenshot').hidden = true;
    $('previewEmpty').hidden = false;
    $('previewEmpty').textContent = '无法读取图片，请检查路径或重新选图。';
    $('enlarge').disabled = true;
  };
  $('screenshot').src = imageUrl(row.screenshot_file);
}

function renderEditor() {
  const row = current();
  $('editor').hidden = !row;
  $('editorEmpty').hidden = !!row;
  $('remove').disabled = !row || state.busy;
  preview(row);
  if (!row) return;
  $('rowState').textContent = labels[kind(row)];
  $('path').value = row.screenshot_file || '';
  options($('event'), Object.entries(state.events).map(([id, info]) => [id, `${id} · ${info.name}`]), row.event_id);
  renderPhases();
  renderScenes();
  renderTemplates();
}

function renderPhases() {
  const row = current();
  options($('phase'), Object.entries(eventInfo(row).phases).map(([id, phase]) => [id, phase.label]), row.phase);
}

function renderScenes() {
  const row = current();
  const info = phaseInfo(row);
  const scenes = {...info.scenes};
  const entries = Object.entries(scenes).map(([id, scene]) => [id, `${scene.label} · ${id}`]);
  if (!scenes[row.scene]) entries.unshift(['', '请选择 YAML 中的场景']);
  options($('scene'), entries, scenes[row.scene] ? row.scene : '');
  $('eventInfo').textContent = `${eventInfo(row).name} · ${info.label}`;
  $('sceneHint').textContent = scenes[row.scene]?.state_hint || '';
}

function renderTemplates() {
  const row = current();
  if (!row) return;
  const templates = phaseInfo(row).templates;
  const names = Object.keys(templates).filter(name =>
    $('allTemplates').checked || row.templates.includes(name) || templates[name].scenes.includes(row.scene));
  $('templateCount').textContent = `已选 ${row.templates.length}`;
  $('templates').replaceChildren();
  for (const name of names) {
    const info = templates[name];
    const card = element('label', 'history-template');
    const heading = element('span');
    const checkbox = element('input');
    checkbox.type = 'checkbox';
    checkbox.value = name;
    checkbox.checked = row.templates.includes(name);
    checkbox.setAttribute('aria-label', name);
    checkbox.addEventListener('change', () => {
      row.templates = checkbox.checked ? [...row.templates, name] : row.templates.filter(item => item !== name);
      changed();
      $('templateCount').textContent = `已选 ${row.templates.length}`;
    });
    heading.append(checkbox, element('code', '', name));
    const frame = element('span', 'template-image');
    if (info?.file) {
      const img = element('img');
      img.alt = `${name} 历史裁剪`;
      img.loading = 'lazy';
      img.src = `/api/history/template?event_id=${encodeURIComponent(row.event_id)}&name=${encodeURIComponent(name)}`;
      img.addEventListener('error', () => img.replaceWith(element('span', '', '无法读取模板图片')), {once: true});
      frame.append(img);
    } else frame.append(element('span', '', '暂无历史模板图片'));
    card.append(heading, frame);
    if (info?.desc) card.append(element('span', 'hint', info.desc));
    $('templates').append(card);
  }
  if (!names.length) $('templates').append(element('p', 'empty', '此场景暂无候选，可显示当前阶段的全部 YAML 模板。'));
}

async function reload() {
  if (state.dirty && !confirm('重新读取会丢弃未保存的修改，是否继续？')) return;
  $('reload').disabled = true;
  try {
    const result = await api('/api/history');
    state.events = result.events;
    state.revision = result.revision;
    replaceRows(result.data, current());
    state.dirty = false;
    $('file').textContent = result.file;
    $('status').textContent = '已读取索引 · 修改后点击“保存修改”';
    showError('');
    filterOptions();
    refreshStatus();
    selectRow(state.selected);
  } catch (error) {
    showError(error.message);
    $('status').textContent = '读取失败';
    $('reload').disabled = false;
  }
}

async function save() {
  if (!state.dirty || state.busy) return;
  state.busy = true;
  refreshStatus();
  // 保存期间固定这次提交的快照，避免随后输入的修改被返回结果覆盖。
  $('editor').inert = true;
  $('rows').inert = true;
  $('remove').disabled = true;
  try {
    const result = await api('/api/history', {data: documentData(), revision: state.revision});
    state.revision = result.revision;
    replaceRows(result.data, current());
    state.dirty = false;
    $('status').textContent = '已保存，原文件已备份';
    $('status').title = result.backup;
    showError('');
    filterOptions();
    selectRow(state.selected);
  } catch (error) {
    showError(error.message);
    $('status').textContent = '保存失败，当前修改仍保留在页面中';
  } finally {
    state.busy = false;
    $('editor').inert = false;
    $('rows').inert = false;
    $('remove').disabled = !current();
    refreshStatus();
  }
}

function addRow() {
  const event_id = $('eventFilter').value || current()?.event_id || Object.keys(state.events)[0];
  if (!event_id) return;
  const phases = state.events[event_id].phases;
  const phase = (phases[$('phaseFilter').value] && $('phaseFilter').value)
    || (current()?.event_id === event_id && current().phase) || Object.keys(phases)[0];
  const scenes = phases[phase].scenes;
  const scene = $('sceneFilter').value || (current()?.event_id === event_id ? current().scene : '')
    || Object.keys(scenes)[0];
  const row = {id: state.nextId++, screenshot_file: null, event_id, phase,
    scene: scenes[scene] ? scene : Object.keys(scenes)[0], templates: []};
  state.rows.push(row);
  $('kindFilter').value = '';
  $('search').value = '';
  filterOptions();
  changed();
  selectRow(row.id);
  $('path').focus();
}

async function loadBrowse(offset = 0) {
  const request = ++browseRequest;
  browseOffset = offset;
  showError('', 'browseError');
  $('browseFiles').replaceChildren(element('p', 'empty', '正在读取截图…'));
  $('browsePrev').disabled = $('browseNext').disabled = true;
  const params = new URLSearchParams({dir: $('directory').value, date_from: $('dateFrom').value,
    date_to: $('dateTo').value, offset, limit: 30});
  try {
    const result = await api(`/api/browse?${params}`);
    if (request !== browseRequest) return;
    $('browseFiles').replaceChildren();
    for (const file of result.files) {
      const button = element('button', 'browse-item');
      const img = element('img');
      img.src = imageUrl(file.file);
      img.alt = file.name;
      img.loading = 'lazy';
      button.append(img, element('span', '', file.name));
      button.addEventListener('click', () => {
        current().screenshot_file = file.file;
        changed();
        renderEditor();
        $('browseDialog').close();
      });
      $('browseFiles').append(button);
    }
    if (!result.files.length) $('browseFiles').append(element('p', 'empty', '没有符合条件的截图。'));
    $('browsePage').textContent = `${Math.floor(offset / 30) + 1} / ${Math.max(1, Math.ceil(result.total / 30))} 页 · ${result.total} 张`;
    $('browsePrev').disabled = offset === 0;
    $('browseNext').disabled = offset + 30 >= result.total;
  } catch (error) {
    if (request !== browseRequest) return;
    $('browseFiles').replaceChildren();
    $('browsePage').textContent = '';
    showError(error.message, 'browseError');
  }
}

$('reload').addEventListener('click', reload);
$('save').addEventListener('click', save);
$('add').addEventListener('click', addRow);
for (const id of ['search', 'eventFilter', 'phaseFilter', 'sceneFilter', 'kindFilter']) {
  $(id).addEventListener(id === 'search' ? 'input' : 'change', () => {
    if (id === 'eventFilter' || id === 'phaseFilter') filterOptions();
    const rows = filteredRows();
    if (rows.length && !rows.some(row => row.id === state.selected)) selectRow(rows[0].id);
    else renderList();
  });
}
$('path').addEventListener('input', () => {
  current().screenshot_file = $('path').value.trim() || null;
  changed();
});
$('path').addEventListener('change', () => preview(current()));
$('event').addEventListener('change', () => {
  current().event_id = $('event').value;
  const phases = eventInfo(current()).phases;
  if (!phases[current().phase]) current().phase = Object.keys(phases)[0];
  updatePhase();
});
function updatePhase() {
  const info = phaseInfo(current());
  current().templates = current().templates.filter(name => name in info.templates);
  if (!info.scenes[current().scene]) current().scene = Object.keys(info.scenes)[0] || '';
  $('allTemplates').checked = false;
  renderPhases();
  renderScenes();
  renderTemplates();
  filterOptions();
  changed();
}
$('phase').addEventListener('change', () => {
  current().phase = $('phase').value;
  updatePhase();
});
$('scene').addEventListener('change', () => {
  current().scene = $('scene').value;
  renderScenes();
  renderTemplates();
  changed();
});
$('allTemplates').addEventListener('change', renderTemplates);
$('remove').addEventListener('click', () => {
  if (!confirm('从索引移除此条关联？截图文件会保留，点击保存后生效。')) return;
  const index = state.rows.findIndex(row => row.id === state.selected);
  state.rows.splice(index, 1);
  filterOptions();
  changed();
  selectRow(state.rows[Math.min(index, state.rows.length - 1)]?.id ?? null);
});
for (const [id, step] of [['previous', -1], ['next', 1]]) {
  $(id).addEventListener('click', () => {
    const rows = filteredRows();
    const index = rows.findIndex(row => row.id === state.selected);
    if (rows[index + step]) selectRow(rows[index + step].id);
  });
}
$('enlarge').addEventListener('click', () => {
  $('largeImage').src = imageUrl(current().screenshot_file);
  $('imageDialog').showModal();
});
for (const button of document.querySelectorAll('[data-close]')) {
  button.addEventListener('click', () => $(button.dataset.close).close());
}
$('browse').addEventListener('click', () => {
  if (current().screenshot_file) $('directory').value = current().screenshot_file.replace(/[/\\][^/\\]+$/, '');
  $('browseDialog').showModal();
  loadBrowse();
});
$('browseLoad').addEventListener('click', () => loadBrowse());
$('browsePrev').addEventListener('click', () => loadBrowse(Math.max(0, browseOffset - 30)));
$('browseNext').addEventListener('click', () => loadBrowse(browseOffset + 30));
$('download').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(documentData(), null, 2) + '\n'], {type: 'application/json'}));
  const link = element('a');
  link.href = url;
  link.download = 'history_matches.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('beforeunload', event => {
  if (state.dirty) { event.preventDefault(); event.returnValue = ''; }
});
document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
    event.preventDefault();
    if (!$('browseDialog').open && !$('imageDialog').open) save();
  }
});
reload();
