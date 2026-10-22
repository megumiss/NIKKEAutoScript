'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const BROWSE_PAGE_SIZE = 30;
const baseName = file => (file || '').split(/[\\/]/).pop();
const sceneKey = item => item?.scene ? `${item.phase || state?.session?.phase}:${item.scene}` : '';
let state = null, shotIndex = -1, templateName = '', view = 'source', selectedFiles = new Set();
let browseFiles = [], browseOffset = 0, browseTotal = 0, toastTimer, infoSequence = 0;
let annotationDirty = false, reviewDirty = false;
let confirmInfo = null, historyEvents = [], sessionDetails = [], pendingDelete = null, deletingSession = false;

function newEventId() {
  const value = $('eventId').value.trim();
  return value ? `event_${value}` : '';
}

function notify(message, error = false) {
  $('status').textContent = message;
  $('toast').textContent = message; $('toast').classList.toggle('error', error); $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4500);
}
async function api(path, body) {
  const headers = {};
  if (state?.current) headers['X-Event-ID'] = state.current;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (body !== undefined && state?.session) headers['X-Session-Revision'] = String(state.session.revision || 0);
  const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST', headers, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function bind(id, callback, errorId) {
  $(id).addEventListener('click', async () => {
    const button = $(id); if (button.disabled) return;
    button.disabled = true; button.setAttribute('aria-busy', 'true');
    if (errorId) $(errorId).hidden = true;
    try { await callback(); } catch (error) {
      if (errorId) { $(errorId).textContent = error.message; $(errorId).hidden = false; }
      notify(error.message, true);
    } finally { button.disabled = false; button.removeAttribute('aria-busy'); updateButtons(); }
  });
}
function sceneOptions(id, initial, filter = false) {
  const select = $(id), old = select.value;
  select.innerHTML = `<option value="">${initial}</option>` + (filter ? '<option value="unassigned">未关联场景</option>' : '')
    + Object.entries(state?.scenes || {}).map(([key, scene]) => `<option value="${esc(key)}">${esc(scene.label)}</option>`).join('');
  if ([...select.options].some(o => o.value === old)) select.value = old;
}
function applyState(next) {
  const changed = next.current !== state?.current;
  const previousShot = state?.session?.screenshots[shotIndex];
  if (next.session?.revision !== state?.session?.revision || changed) confirmInfo = null;
  state = next;
  if (changed) {
    shotIndex = -1; templateName = ''; annotationDirty = reviewDirty = false; infoSequence++;
    $('templateSearch').value = ''; $('templateStatus').value = '';
    for (const id of ['shotFilter', 'shotScene', 'templateScene']) $(id).value = '';
    selectedFiles.clear(); updateImportCount();
  }
  if (previousShot && !changed) shotIndex = state.session?.screenshots.findIndex(s => s.file === previousShot.file && s.phase === previousShot.phase) ?? -1;
  if (shotIndex < 0 && state.session?.screenshots.length) shotIndex = 0;
  if (templateName && !state.session?.templates.some(t => t.name === templateName)) templateName = '';
  const session = state.session;
  $('sessionTitle').textContent = session ? `${session.event_name || session.event_id}` : '尚未打开活动';
  $('sessionMeta').textContent = session ? `${state.types[session.event_type].label}活动 · ${state.types[session.event_type].phases[session.phase]} · ${session.screenshots.length} 张截图` : '导入截图，手动截取或交给 agent 裁剪，再校对导出';
  $('dataPath').textContent = state.session_file || '';
  $('dataPath').title = state.session_file || '';
  for (const [id, initial, filter] of [['shotFilter','所有场景',true], ['shotScene','未归类'], ['templateScene','所有场景']]) sceneOptions(id, initial, filter);
  const required = session?.templates.filter(t => !t.optional) || [];
  const confirmed = required.filter(t => t.status === 'confirmed').length;
  $('progress').max = Math.max(1, required.length); $('progress').value = confirmed;
  $('missingInfo').textContent = session ? `必需模板 ${confirmed} / ${required.length} 已确认` : '等待打开会话';
  $('tplCount').textContent = session ? `${session.templates.filter(t => t.status === 'confirmed').length} / ${session.templates.length}` : '0 / 0';
  $('shotCount').textContent = session?.screenshots.length || 0;
  const completed = session?.screenshots.filter(s => s.scene).length || 0;
  $('mappingCount').textContent = `${completed} 张已关联`;
  $('prepareHint').textContent = session ? `已关联 ${completed} / ${session.screenshots.length} 张截图。${completed < session.screenshots.length ? '请为截图选择场景。' : '可以生成任务；缺少的场景会列为待补充项。'}` : '先导入截图并关联场景。';
  renderShots(); renderShotEditor(); renderCoverage(); renderTemplates(); updateButtons();
  if (templateName) loadTemplateInfo();
  else {
    $('reviewDetails').hidden = true; $('tplInfo').innerHTML = '<div class="empty">选择模板，查看裁剪结果与历史参考。</div>';
    $('templateBadge').textContent = '未选择'; $('templateBadge').className = 'badge';
    $('reviewNote').value = '';
    if (!session) switchView('source');
  }
}
function updateButtons() {
  const session = state?.session, shot = session?.screenshots[shotIndex];
  const template = session?.templates.find(t => t.name === templateName);
  for (const id of ['btnRefresh','btnImportDialog']) $(id).disabled = !session;
  for (const id of ['btnPrepare','btnPrepareMain']) $(id).disabled = !session || session.screenshots.some(s => !s.scene) || !session.templates.some(t => t.status !== 'confirmed');
  for (const id of ['btnRemoveShot','btnSaveShot','shotScene']) $(id).disabled = !shot;
  const cropDirty = window.eventCrop?.dirty() || false;
  $('btnConfirm').disabled = cropDirty || !template || template.status === 'confirmed' || confirmInfo?.name !== template.name || !confirmInfo.valid;
  $('btnReject').disabled = cropDirty || !template || !template.box;
  $('btnClear').disabled = cropDirty || !template?.box;
  $('btnManualStart').disabled = !session?.screenshots.length || !session?.templates.length;
  $('btnManualCrop').disabled = !template || !session?.screenshots.length;
  $('btnManualCrop').textContent = '定位到裁剪画布';
  $('btnNext').disabled = !session?.templates.some(t => t.status !== 'confirmed');
  $('btnSaveReview').disabled = !template;
  $('btnExport').disabled = cropDirty || !session || !session.templates.length || Boolean(state.missing?.length);
  if (cropDirty) for (const id of ['btnPrepare','btnPrepareMain','btnAdvance','btnRemoveShot']) $(id).disabled = true;
  $('btnPrevPage').disabled = browseOffset === 0;
  $('btnNextPage').disabled = browseOffset + BROWSE_PAGE_SIZE >= browseTotal;
  const eventId = newEventId();
  $('eventIdHint').textContent = eventId ? `活动 ID：${eventId}` : '自动添加 event_ 前缀，不限制日期格式或位数。';
  $('btnNew').disabled = !eventId || !$('eventName').value.trim() || !historyEvents.some(e => e.event_id === $('baseEventSel').value && e.source_dir);
  const selected = sessionDetails.find(s => s.event_id === $('sessionSel').value);
  for (const id of ['btnOpen', 'btnDeleteSession']) $(id).disabled = !selected || $(id).hasAttribute('aria-busy');
  $('btnDeleteSession').textContent = selected && selected.event_id === state?.current ? '删除当前会话' : '删除会话';
  $('btnConfirmDeleteSession').disabled = !pendingDelete || deletingSession;
  $('btnCancelDeleteSession').disabled = deletingSession;
  $('btnConfirmDeleteSession').textContent = deletingSession ? '正在删除…' : '确认删除';
}
function renderShots() {
  const shots = state?.session?.screenshots || [], filter = $('shotFilter').value;
  $('shotList').innerHTML = shots.map((s, i) => {
    if (filter === 'unassigned' && s.scene || filter && filter !== 'unassigned' && sceneKey(s) !== filter) return '';
    return `<button class="shot-item ${i === shotIndex ? 'selected' : ''}" data-index="${i}" aria-pressed="${i === shotIndex}"><img class="shot-thumb" src="/api/image?path=${encodeURIComponent(s.file)}" loading="lazy" alt=""><span class="shot-copy"><strong>${esc(state.scenes[sceneKey(s)]?.label || '未关联场景')}</strong><span class="shot-file">${esc(baseName(s.file))}</span></span></button>`;
  }).join('') || '<div class="empty">没有符合条件的截图。</div>';
}
function renderShotEditor() {
  const shot = state?.session?.screenshots[shotIndex];
  $('shotName').textContent = shot ? baseName(shot.file) : '未选择截图';
  if (!annotationDirty) {
    $('shotScene').value = sceneKey(shot);
  }
  $('sceneHint').textContent = state?.scenes?.[$('shotScene').value]?.state_hint || '同一场景可以提供多张截图，agent 按你关联的场景裁剪。';
  window.eventCrop?.sync();
}
function renderCoverage() {
  const coverage = state?.coverage || [];
  const open = new Set([...document.querySelectorAll('.scene-card[open]')].map(e => e.dataset.scene));
  $('coverageSummary').textContent = coverage.length ? `${coverage.filter(c => c.screenshots).length} / ${coverage.length} 个场景有截图` : '';
  $('coverageList').innerHTML = coverage.map(c => `<details class="scene-card" data-scene="${esc(c.key)}" ${open.has(c.key) || c.key === $('shotScene').value ? 'open' : ''}><summary><strong>${esc(c.label)}</strong><span class="badge ${c.needs_screenshot ? 'warning' : ''}">${c.screenshots ? `${c.screenshots} 张截图` : c.needs_screenshot ? '需要截图' : '已完成 / 可选'}</span></summary><p class="hint state-hint">${esc(c.state_hint)}</p><ul class="scene-requirements">${c.templates.map(t => `<li>${esc(t.desc)} ${t.optional ? '<span class="badge">可选</span>' : ''}<code>${esc(t.name)}</code>${t.requires ? `<span class="hint">${esc(t.requires)}</span>` : ''}</li>`).join('')}</ul></details>`).join('') || '<div class="empty">打开活动后，显示对应阶段的场景要求。</div>';
}
function renderTemplates() {
  const query = $('templateSearch').value.trim().toLowerCase(), scene = $('templateScene').value, status = $('templateStatus').value;
  $('tplList').innerHTML = (state?.session?.templates || []).filter(t => {
    const rule = state.rules[t.name] || {};
    return (!query || `${t.name} ${rule.desc || ''}`.toLowerCase().includes(query)) && (!scene || sceneKey(t) === scene) && (!status || (status === 'todo' ? t.status !== 'confirmed' : t.status === status));
  }).map(t => `<button class="template-item ${t.name === templateName ? 'selected' : ''}" data-name="${esc(t.name)}" data-status="${esc(t.status)}" aria-pressed="${t.name === templateName}"><span class="dot"></span><span class="template-copy"><strong>${esc(t.name)}</strong><span>${esc(state.rules[t.name]?.desc || t.name)} · ${esc(state.status_label[t.status])}${t.optional ? ' · 可选' : ''}</span></span></button>`).join('') || '<div class="empty">没有符合条件的模板。</div>';
}
async function saveAnnotation() {
  if (!annotationDirty || shotIndex < 0) return;
  const selected = state.scenes[$('shotScene').value];
  const next = await api('/api/shots/scene', {index:shotIndex, scene:selected?.scene || '', phase:selected?.phase || state.session.screenshots[shotIndex].phase});
  annotationDirty = false; applyState(next);
}
async function saveReview(reject = false) {
  if (!templateName) return;
  const next = await api('/api/review_note', {name:templateName, note:$('reviewNote').value.trim(), reject});
  reviewDirty = false; applyState(next);
}
async function selectShot(index) {
  if (index !== shotIndex && !window.eventCrop?.allowDiscard()) return false;
  await saveAnnotation(); shotIndex = index; annotationDirty = false; renderShots(); renderShotEditor(); updateButtons(); return true;
}
async function selectTemplate(name) {
  if (name !== templateName && !window.eventCrop?.allowDiscard()) return;
  if (reviewDirty) await saveReview();
  templateName = name; reviewDirty = false;
  const template = state.session.templates.find(t => t.name === name);
  if (template?.source) {
    const index = state.session.screenshots.findIndex(s => s.file === template.source && s.phase === template.phase);
    if (index >= 0 && index !== shotIndex) {
      if (await selectShot(index) === false) return;
    }
  }
  renderTemplates(); window.eventCrop?.sync(); updateButtons(); await loadTemplateInfo();
}
async function loadTemplateInfo() {
  const seq = ++infoSequence, name = templateName;
  if (!name) return;
  try {
    const info = await api(`/api/template_info?name=${encodeURIComponent(name)}`);
    if (seq !== infoSequence || name !== templateName) return;
    const rule = state.rules[name] || {}, template = state.session.templates.find(t => t.name === name);
    $('templateBadge').textContent = state.status_label[info.status]; $('templateBadge').className = `badge ${info.status}`;
    $('tplInfo').innerHTML = `<p class="template-name">${esc(name)}</p><p class="template-desc">${esc(rule.desc || '')}</p><div class="template-flags"><span class="badge">${esc(state.scenes[sceneKey(template)]?.label || '未关联场景')}</span>${template.optional ? '<span class="badge">可选</span>' : ''}</div>${rule.requires ? `<p class="hint">${esc(rule.requires)}</p>` : ''}`;
    $('reviewDetails').hidden = false;
    const box = info.box;
    $('cropSize').textContent = box ? `${box[2]-box[0]} × ${box[3]-box[1]}` : '';
    $('sourceInfo').textContent = info.source ? `${baseName(info.source)} · ${info.proposer === 'agent' ? 'agent 提案' : info.proposer === 'user' ? '手动截取' : info.proposer || '提案'}` : '该模板还没有裁剪提案，可手动截取或交给 agent。';
    $('agentNote').textContent = info.agent_note || '';
    if (!reviewDirty) $('reviewNote').value = info.review_note || '';
    $('refPath').textContent = info.reference || '';
    $('tplMeta').textContent = `${info.mode === 'button' ? '整屏黑底，保留原始坐标' : '直接裁剪小图'}${box ? ` · [${box.join(', ')}]` : ''}`;
    const qs = `name=${encodeURIComponent(name)}&event_id=${encodeURIComponent(state.current)}&v=${state.session.revision || Date.now()}`;
    setPreview('previewImg','previewEmpty', info.source && box ? `/api/preview?${qs}` : '');
    setPreview('refImg','refEmpty', info.reference ? `/api/reference?${qs}` : '');
    confirmInfo = {name,valid:Boolean(info.bbox)};
    updateButtons();

  } catch (error) { notify(error.message, true); }
}
function setPreview(imageId, emptyId, url) {
  const img = $(imageId), empty = $(emptyId); img.hidden = true; empty.hidden = false;
  img.onload = () => { img.hidden = false; empty.hidden = true; };
  img.onerror = () => { img.hidden = true; empty.hidden = false; };
  if (url) img.src = url; else img.removeAttribute('src');
}
function switchView(mode) {
  view = mode; document.querySelector('.workspace').dataset.mode = mode;
  document.querySelector('.source-workspace').hidden = mode !== 'source'; document.querySelector('.review-workspace').hidden = mode !== 'review';
  $('btnSource').classList.toggle('active', mode === 'source'); $('btnReview').classList.toggle('active', mode === 'review');
  $('btnSource').setAttribute('aria-pressed', String(mode === 'source')); $('btnReview').setAttribute('aria-pressed', String(mode === 'review'));
  if (mode === 'review' && !templateName && state?.session?.templates.length) selectTemplate(state.session.templates.find(t => t.status === 'draft')?.name || state.session.templates[0].name).catch(e => notify(e.message, true));
  window.eventCrop?.sync();
}
async function refresh() {
  applyState(await api('/api/state'));
  notify(annotationDirty || reviewDirty || window.eventCrop?.dirty() ? '已读取最新结果，当前未保存的选区、标注或意见仍保留，请保存后继续' : '已读取最新会话与 agent 结果');
}
async function prepare() {
  await saveAnnotation(); if (reviewDirty) await saveReview();
  const result = await api('/api/agent/request', {});
  $('agentSummary').textContent = `已准备 ${result.targets} 个待处理模板。复制任务说明交给 agent。`;
  $('agentPrompt').value = result.prompt;
  $('sessionPath').textContent = state.session_file; $('requestPath').textContent = result.request_file; $('resultPath').textContent = result.result_file;
  $('agentWarnings').textContent = result.warnings.length ? `以下场景缺少截图，agent 将记录待补充项：\n${result.warnings.join('\n')}` : '';
  $('agentWarnings').hidden = !result.warnings.length; $('agentError').hidden = true; $('agentDialog').showModal();
}
function phaseOptions() {
  const base = historyEvents.find(e => e.event_id === $('baseEventSel').value);
  const type = state?.types[base?.event_type];
  $('typeLabel').value = type?.label || '';
  $('baseEventInfo').textContent = base?.source_dir ? `复制此活动的整套中文剧情资源，模板清单按${type?.label || ''}活动生成。` : '此活动缺少可复制的资源，请选择其它历史活动。';
  const phases = type?.phases || {};
  $('phaseSel').innerHTML = Object.entries(phases).map(([key,label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join('');
}
function renderSessionSelection() {
  const selected = sessionDetails.find(s => s.event_id === $('sessionSel').value);
  const type = state?.types[selected?.event_type];
  $('selectedSessionInfo').textContent = selected
    ? `${selected.event_id === state?.current ? '当前会话 · ' : ''}${type?.label || ''}活动 · ${type?.phases[selected.phase] || selected.phase} · ${selected.screenshot_count} 张截图 · 模板 ${selected.confirmed_count} / ${selected.template_count} 已确认`
    : '暂无会话，可以在下方新建活动会话。';
  updateButtons();
}
function renderSessionList(preferred = state?.current) {
  $('sessionSel').innerHTML = sessionDetails.map(s => `<option value="${esc(s.event_id)}">${s.event_id === state?.current ? '【当前】' : ''}${esc(s.event_name || s.event_id)}${s.event_name ? ` · ${esc(s.event_id)}` : ''}</option>`).join('') || '<option value="">暂无会话</option>';
  $('sessionSel').value = sessionDetails.some(s => s.event_id === preferred) ? preferred : sessionDetails[0]?.event_id || '';
  $('sessionSel').disabled = !sessionDetails.length;
  $('phaseBlock').hidden = !state?.session;
  $('phaseSessionInfo').textContent = state?.session ? `当前会话：${state.session.event_name || state.current} · ${state.current}` : '';
  if (state?.session) {
    $('advancePhase').innerHTML = Object.entries(state.types[state.session.event_type].phases).map(([key,label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join('');
    $('advancePhase').value = state.session.phase;
  }
  renderSessionSelection();
}
async function loadSessionList(preferred = state?.current) {
  const data = await api('/api/sessions');
  sessionDetails = data.session_details;
  historyEvents = data.events;
  const baseEventId = $('baseEventSel').value;
  $('baseEventSel').innerHTML = historyEvents.map(e => `<option value="${esc(e.event_id)}" ${e.source_dir ? '' : 'disabled'}>${esc(e.event_name)} · ${esc(e.event_id)} · ${esc(state.types[e.event_type]?.label || e.event_type)}${e.source_dir ? '' : '（资源缺失）'}</option>`).join('');
  $('baseEventSel').value = historyEvents.find(e => e.event_id === baseEventId && e.source_dir)?.event_id || historyEvents.find(e => e.source_dir)?.event_id || '';
  const phase = $('phaseSel').value;
  phaseOptions();
  if ([...$('phaseSel').options].some(o => o.value === phase)) $('phaseSel').value = phase;
  if (state?.current && !data.sessions.includes(state.current)) applyState(await api('/api/state'));
  renderSessionList(preferred);
}
async function sessionsDialog() {
  if (!window.eventCrop?.allowDiscard()) return;
  await loadSessionList();
  await saveAnnotation(); if (reviewDirty) await saveReview();
  if (state?.session) {
    const selected = sessionDetails.find(s => s.event_id === state.current);
    if (selected && state.session.revision > selected.revision) selected.revision = state.session.revision;
  }
  $('sessionError').hidden = true; $('sessionDialog').showModal();
}
function confirmSessionDeletion() {
  pendingDelete = sessionDetails.find(s => s.event_id === $('sessionSel').value);
  if (!pendingDelete) return;
  $('deleteSessionName').textContent = `${pendingDelete.event_name || pendingDelete.event_id}（${pendingDelete.event_id}）`;
  $('deleteCurrentHint').hidden = pendingDelete.event_id !== state?.current;
  $('deleteSessionError').hidden = true; updateButtons(); $('deleteSessionDialog').showModal();
}
async function deleteSession() {
  if (!pendingDelete || deletingSession) return;
  deletingSession = true; updateButtons();
  try {
    const next = await api('/api/session/delete', {event_id:pendingDelete.event_id, revision:pendingDelete.revision});
    sessionDetails = sessionDetails.filter(s => s.event_id !== next.deleted_event_id);
    applyState(next); renderSessionList();
    $('deleteSessionDialog').close();
    notify(`已删除会话 ${next.deleted_event_id}，原图和活动资源保留`);
  } finally { deletingSession = false; updateButtons(); }
}
function updateImportCount() { $('importCount').textContent = `已选 ${selectedFiles.size} 张`; $('btnImport').disabled = !selectedFiles.size; }
async function browse(offset = 0) {
  const query = new URLSearchParams({dir:$('browseDir').value.trim(), date_from:$('dateFrom').value, date_to:$('dateTo').value, offset:String(offset), limit:String(BROWSE_PAGE_SIZE)});
  const data = await api(`/api/browse?${query}`); browseFiles = data.files; browseOffset = offset; browseTotal = data.total;
  $('browseCount').textContent = `共 ${data.total} 张截图`;
  $('browseList').innerHTML = data.files.map(f => `<label class="browse-item"><input type="checkbox" value="${esc(f.file)}" ${selectedFiles.has(f.file) ? 'checked' : ''} aria-label="选择 ${esc(f.name)}"><img src="/api/image?path=${encodeURIComponent(f.file)}" loading="lazy" alt="${esc(f.name)}"><span>${esc(f.name)}</span><span class="hint">${f.width} × ${f.height}</span></label>`).join('') || '<div class="empty">没有符合条件的截图。</div>';
  $('pageInfo').textContent = data.total ? `${offset+1}–${Math.min(offset+BROWSE_PAGE_SIZE,data.total)} / ${data.total}` : '0 / 0';
  $('btnPrevPage').disabled = offset === 0; $('btnNextPage').disabled = offset + BROWSE_PAGE_SIZE >= data.total; updateImportCount();
}
for (const id of ['shotScene']) $(id).addEventListener('input', () => { annotationDirty = true; $('sceneHint').textContent = state?.scenes?.[$('shotScene').value]?.state_hint || '同一场景可以提供多张截图，agent 按你关联的场景裁剪。'; });
$('reviewNote').addEventListener('input', () => reviewDirty = true);
$('shotFilter').addEventListener('change', renderShots);
for (const id of ['templateSearch','templateScene','templateStatus']) $(id).addEventListener('input', renderTemplates);
$('shotList').addEventListener('click', e => { const row=e.target.closest('[data-index]'); if (row) selectShot(Number(row.dataset.index)).catch(e=>notify(e.message,true)); });
$('tplList').addEventListener('click', e => { const row=e.target.closest('[data-name]'); if (row) selectTemplate(row.dataset.name).catch(e=>notify(e.message,true)); });
$('baseEventSel').addEventListener('change', () => { phaseOptions(); updateButtons(); });
$('sessionSel').addEventListener('change', renderSessionSelection);
$('deleteSessionDialog').addEventListener('cancel', e => { if (deletingSession) e.preventDefault(); });
$('deleteSessionDialog').addEventListener('close', () => { pendingDelete = null; updateButtons(); });
for (const id of ['eventId','eventName']) $(id).addEventListener('input', updateButtons);
for (const el of document.querySelectorAll('[data-close]')) el.addEventListener('click',()=>$(el.dataset.close).close());
$('browseList').addEventListener('change', e => { if (e.target.type !== 'checkbox') return; if(e.target.checked) selectedFiles.add(e.target.value); else selectedFiles.delete(e.target.value); updateImportCount(); });
window.addEventListener('beforeunload', e => { if(annotationDirty || reviewDirty) { e.preventDefault(); e.returnValue=''; } });

bind('btnSessions',sessionsDialog); bind('btnRefresh',refresh);
bind('btnReloadSessions',()=>loadSessionList($('sessionSel').value),'sessionError');
bind('btnDeleteSession',confirmSessionDeletion,'sessionError');
bind('btnConfirmDeleteSession',deleteSession,'deleteSessionError');
bind('btnSource',async()=>{ await saveAnnotation(); if(reviewDirty) await saveReview(); switchView('source'); });
bind('btnReview',async()=>{ await saveAnnotation(); switchView('review'); });
bind('btnPrepare',prepare); bind('btnPrepareMain',prepare);
bind('btnSaveShot',async()=>{ annotationDirty=true; await saveAnnotation(); notify('截图场景已保存'); });
bind('btnRemoveShot',async()=>{ const next=await api('/api/shots/remove',{index:shotIndex}); annotationDirty=false; applyState(next); notify('已移除截图记录，原图保留'); });
bind('btnSaveReview',async()=>{ await saveReview(); notify('校对意见已保存'); });
bind('btnNew',async()=>{ const data=await api('/api/session/new',{event_id:newEventId(),event_name:$('eventName').value.trim(),base_event_id:$('baseEventSel').value,position:Number($('eventPosition').value),phase:$('phaseSel').value}); applyState(data); switchView('source'); $('sessionDialog').close(); notify(`已复制资源并创建活动，插入 EVENTS 第 ${data.session.event_position} 位`); },'sessionError');
bind('btnOpen',async()=>{ applyState(await api('/api/session/open',{event_id:$('sessionSel').value})); $('sessionDialog').close(); notify('已打开会话'); },'sessionError');
bind('btnAdvance',async()=>{ applyState(await api('/api/session/phase',{phase:$('advancePhase').value})); $('sessionDialog').close(); notify('已补入阶段模板，已有结果保留'); },'sessionError');
bind('btnImportDialog',async()=>{ await saveAnnotation(); $('importError').hidden=true; $('importDialog').showModal(); if(!browseFiles.length) await browse(); },'importError');
bind('btnBrowse',()=>browse(0),'importError'); bind('btnPrevPage',()=>browse(Math.max(0,browseOffset-BROWSE_PAGE_SIZE)),'importError'); bind('btnNextPage',()=>browse(browseOffset+BROWSE_PAGE_SIZE),'importError');
bind('btnSelectPage',async()=>{ const all=browseFiles.every(f=>selectedFiles.has(f.file)); for(const f of browseFiles) { if(all) selectedFiles.delete(f.file); else selectedFiles.add(f.file); } await browse(browseOffset); },'importError');
bind('btnImport',async()=>{ applyState(await api('/api/shots/import',{files:[...selectedFiles]})); selectedFiles.clear(); updateImportCount(); $('importDialog').close(); notify('已导入截图，请关联到对应场景'); },'importError');
bind('btnCopyPrompt',async()=>{ await navigator.clipboard.writeText($('agentPrompt').value); notify('任务说明已复制，发给 agent 即可'); },'agentError');
bind('btnReadResult',async()=>{ const next=await api('/api/agent/result',{}); applyState(next); $('agentDialog').close(); switchView('review'); notify(`已读入 ${next.result.proposals} 个草稿、${next.result.unresolved} 个待补充项`); },'agentError');
bind('btnAgentRefresh',async()=>{ await refresh(); $('agentDialog').close(); switchView('review'); },'agentError');
async function nextTemplate() {
  const items=state?.session?.templates || [], index=items.findIndex(t=>t.name===templateName);
  const ordered=[...items.slice(index+1),...items.slice(0,index+1)];
  const next=ordered.find(t=>t.status==='draft') || ordered.find(t=>t.status!=='confirmed');
  if(next) { $('templateSearch').value=''; $('templateScene').value=''; $('templateStatus').value=''; await selectTemplate(next.name); }
}
bind('btnNext',nextTemplate);
bind('btnConfirm',async()=>{ if(reviewDirty) await saveReview(); applyState(await api('/api/status',{name:templateName,value:'confirmed'})); notify('模板已确认'); if($('autoNext').checked) await nextTemplate(); });
bind('btnReject',async()=>{ await saveReview(true); notify('已打回，重新生成 agent 任务即可带上校对意见'); });
bind('btnClear',async()=>{ applyState(await api('/api/clear',{name:templateName})); notify('提案已清除，可手动截取或交给 agent 重新生成'); });
bind('btnExport',async()=>{ if(reviewDirty) await saveReview(); const result=await api('/api/export',{}); $('exportResult').textContent=`已导出 ${result.exported.length} 个模板。`; $('exportPath').textContent=result.out_dir; $('exportDialog').showModal(); notify('模板导出完成'); });
document.addEventListener('keydown',e=>{ if(view!=='review' || document.querySelector('dialog[open]') || e.target.closest('input,textarea,select,button,canvas')) return; if(e.key==='Enter' && !$('btnConfirm').disabled) { e.preventDefault(); $('btnConfirm').click(); } if(e.key==='Delete' && !$('btnReject').disabled) { e.preventDefault(); $('btnReject').click(); } });
api('/api/state').then(data=>{applyState(data); $('status').textContent=data.session ? '已读取会话，标注会保存到本地' : '新建或打开活动会话开始';}).catch(error=>notify(error.message,true));
