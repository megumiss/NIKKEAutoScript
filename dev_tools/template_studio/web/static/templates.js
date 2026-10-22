'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
let features = [], screenshots = [], library = null, selected = null, draft = null;
let shotIndex = -1, selection = null, selectionDirty = false, metadataDirty = false, loadedSource = '';
let busy = true, pendingAction = null, toastTimer;
const cropEditor = createCropEditor({onChange:value => {
  selection = value;
  selectionDirty = Boolean(value.box) && (value.changed || metadataDirty);
  refreshSelection();
}});

function notify(message, error = false) {
  $('status').textContent = message; $('toast').textContent = message;
  $('toast').classList.toggle('error', error); $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 5000);
}
async function api(path, body) {
  const response = await fetch(path, {method:body === undefined ? 'GET' : 'POST',
    headers:body === undefined ? {} : {'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 (${response.status})`);
  return data;
}
function bind(id, callback) {
  $(id).addEventListener('click', async () => {
    if ($(id).disabled || $(id).hasAttribute('aria-busy')) return;
    $(id).setAttribute('aria-busy', 'true'); $(id).disabled = true;
    try { await callback(); } catch (error) { notify(error.message, true); }
    finally { $(id).removeAttribute('aria-busy'); updateButtons(); }
  });
}
function setPreview(imageId, emptyId, url) {
  const image = $(imageId), empty = $(emptyId); image.hidden = true; empty.hidden = false;
  image.onload = () => { image.hidden = false; empty.hidden = true; };
  image.onerror = () => { image.hidden = true; empty.hidden = false; };
  if (url) image.src = url; else image.removeAttribute('src');
}
function outputName() {
  let name = $('outputName').value.trim().toUpperCase().replace(/\.PNG$/i, '');
  if (name && $('outputMode').value === 'template' && !name.startsWith('TEMPLATE_')) name = `TEMPLATE_${name}`;
  return name;
}
function imageUrl(asset = selected, full = false) {
  return asset && library ? `/api/templates/image?${new URLSearchParams({feature:library.feature,name:asset.name,v:library.revision,full:full ? '1' : '0'})}` : '';
}
function allowDiscard() { return !selectionDirty || window.confirm('本次裁剪尚未保存，确定放弃？'); }
function clearDraft() { metadataDirty = false; selectionDirty = false; cropEditor.setBox(null); }
function updateButtons() {
  const shot = screenshots[shotIndex], mode = $('outputMode').value;
  const compatible = shot && (mode === 'template' || shot.width === 720 && shot.height === 1280);
  const validName = !(mode === 'template' && outputName().includes('.')) && /^[A-Z][A-Z0-9_]*(?:\.(?:AREA|COLOR|BUTTON))?$/.test(outputName()) && !(mode === 'button' && outputName().startsWith('TEMPLATE_'));
  cropEditor.setDisabled(busy || Boolean(pendingAction));
  $('btnSaveAsset').disabled = busy || !library || !draft || !validName || !selectionDirty || selection?.editing || Boolean(selected && !selected.editable);
  $('btnDeleteAsset').disabled = busy || !selected;
  for (const id of ['btnNewAsset','btnReload','featureSelect','featureSearch','sourceSelect','btnUpload','assetSearch','assetType']) $(id).disabled = busy;
  $('outputMode').disabled = busy || Boolean(selected);
  $('outputName').disabled = busy || Boolean(selected);
  $('btnCommitAsset').disabled = busy || !pendingAction;
  $('btnCancelAsset').disabled = busy;
  $('btnCommitAsset').textContent = busy ? '正在处理…' : pendingAction?.deleting ? '确认删除' : '确认保存';
  const cropError = selection?.error || (shot && !compatible ? '此截图不是 720 × 1280，请选择 Template 保存局部小图。' : '');
  $('cropHint').textContent = cropError || (!shot ? '先导入截图。' : !selection?.valid ? '在原图上拖动框选，右侧实时显示裁剪结果。' : !validName ? '裁剪已就绪，填写右侧模板名称即可保存。' : selectionDirty ? '右侧已实时更新，核对后保存。' : '裁剪已保存，可继续拖动选区调整。');
  $('cropHint').classList.toggle('invalid', Boolean(cropError));
}
function renderFeatures() {
  const query = $('featureSearch').value.trim().toLowerCase(), current = library?.feature || $('featureSelect').value;
  const visible = features.filter(f => f.label.toLowerCase().includes(query) || f.id === current);
  $('featureSelect').innerHTML = visible.map(f => `<option value="${esc(f.id)}">${esc(f.label)} (${f.count})</option>`).join('');
  if (visible.some(f => f.id === current)) $('featureSelect').value = current;
}
function renderAssets() {
  const query = $('assetSearch').value.trim().toLowerCase(), mode = $('assetType').value;
  const items = (library?.assets || []).filter(a => a.name.toLowerCase().includes(query) && (!mode || a.mode === mode));
  $('assetCount').textContent = `${items.length} 项`;
  $('assetList').innerHTML = items.map(a => `<button class="resource-item" data-name="${esc(a.name)}" aria-pressed="${selected?.name === a.name}"><img src="${esc(imageUrl(a))}" alt="" loading="lazy"><span><strong>${esc(a.name)}</strong><small>${a.mode === 'template' ? 'Template' : 'Button'} · ${a.width} × ${a.height}${a.override ? ' · 属性覆盖' : ''}${!a.editable ? ' · GIF' : ''}</small></span></button>`).join('') || '<p class="empty">此功能没有符合筛选条件的模板。可以新增模板。</p>';
}
function renderShots() {
  $('sourceSelect').innerHTML = screenshots.map((s, i) => `<option value="${i}">${esc(s.name)} · ${s.width} × ${s.height}</option>`).join('') || '<option value="">尚未导入</option>';
  $('sourceSelect').value = shotIndex >= 0 ? String(shotIndex) : '';
  const shot = screenshots[shotIndex];
  $('sourceInfo').textContent = shot ? `${shot.width} × ${shot.height} · ${shot.name}` : '';
  $('sourceInfo').title = shot?.file || '';
  if (loadedSource !== (shot?.file || '')) {
    loadedSource = shot?.file || ''; cropEditor.load(loadedSource);
  }
  updateButtons();
}
function refreshSelection() {
  const mode = $('outputMode').value, image = selection?.image;
  const compatible = mode === 'template' || image?.naturalWidth === 720 && image?.naturalHeight === 1280;
  draft = selection?.valid && compatible ? {source:selection.source,box:[...selection.box],mode} : null;
  renderDraftPreview(); renderEditor();
}
function renderDraftPreview() {
  const preview = $('draftPreview');
  preview.hidden = !draft; $('draftEmpty').hidden = Boolean(draft);
  $('cropDimensions').textContent = selection?.valid ? `${selection.box[2]-selection.box[0]} × ${selection.box[3]-selection.box[1]}` : '';
  $('draftMeta').textContent = draft ? `${draft.mode === 'button' ? '输出 720 × 1280' : `输出 ${draft.box[2]-draft.box[0]} × ${draft.box[3]-draft.box[1]}`} · [${draft.box.join(', ')}]` : '';
  if (!draft) return;
  const [x0,y0,x1,y1] = draft.box, full = $('showFull').checked && draft.mode === 'button';
  preview.width = full ? 720 : x1-x0; preview.height = full ? 1280 : y1-y0;
  const ctx = preview.getContext('2d');
  if (full) { ctx.fillStyle = '#000'; ctx.fillRect(0,0,720,1280); }
  ctx.drawImage(selection.image,x0,y0,x1-x0,y1-y0,full ? x0 : 0,full ? y0 : 0,x1-x0,y1-y0);
}
function renderComparison() {
  setPreview('existingImage','existingEmpty', imageUrl(selected, $('showFull').checked));
  $('existingMeta').textContent = selected ? `${selected.mode === 'template' ? '局部小图' : '整屏按钮'} · ${selected.width} × ${selected.height}${selected.box ? ` · [${selected.box.join(', ')}]` : ''}` : '';
  renderDraftPreview();
}
function renderEditor() {
  $('editTitle').textContent = selected ? '编辑现有模板' : '新增模板';
  $('editBadge').textContent = draft ? selectionDirty ? '待保存' : '已保存' : selected ? '已存在' : '待裁剪';
  $('btnSaveAsset').textContent = selected ? '覆盖当前资源' : '保存新模板';
  $('modeHint').textContent = $('outputMode').value === 'template' ? 'Template 只保存选区原始像素，文件名自动添加 TEMPLATE_ 前缀。' : 'Button 保留 720 × 1280 尺寸与选区坐标，其余区域填黑。';
  $('outputPath').textContent = library ? `assets/zh-CN/${library.feature}/${outputName() || '模板名称'}.png` : '先选择功能目录';
  $('assetWarning').hidden = !selected || Boolean(selected.editable && !selected.override && !selected.companions?.length);
  $('assetWarning').textContent = selected && !selected.editable ? 'GIF 可对比或删除。新增裁剪请使用不同名称，保存为 PNG。' : selected?.override ? '这是 Button 的属性覆盖图，保存会更新对应的区域、颜色或点击位置。' : '此 Button 有属性覆盖图。覆盖基础图片会保留这些设置；删除会同时删除对应的属性覆盖图。';
  updateButtons();
}
function chooseAsset(name) {
  if (busy || !allowDiscard()) return;
  clearDraft(); selected = library?.assets.find(a => a.name === name) || null;
  $('outputName').value = selected ? selected.name.replace(/\.(png|gif)$/, '') : '';
  if (selected) $('outputMode').value = selected.mode;
  renderAssets(); refreshSelection(); renderComparison();
}
async function loadFeature(feature, keepDraft = false) {
  const next = await api(`/api/templates/assets?feature=${encodeURIComponent(feature)}`);
  if (!keepDraft) { clearDraft(); selected = null; $('outputName').value = ''; }
  else if (selected) selected = next.assets.find(a => a.name === selected.name) || null;
  library = next;
  const featureInfo = features.find(f => f.id === feature);
  if (featureInfo) featureInfo.count = next.assets.length;
  $('featurePath').textContent = `assets/zh-CN/${feature}`;
  renderFeatures(); renderAssets(); renderEditor(); await renderComparison();
}
async function importFiles(files) {
  if (busy || !files.length || !allowDiscard()) return;
  busy = true; updateButtons(); let imported = 0;
  try {
    for (const file of files) {
      const response = await fetch(`/api/templates/upload?name=${encodeURIComponent(file.name)}`, {method:'POST',body:file});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      screenshots.unshift(data); imported++;
    }
    notify(`已导入 ${imported} 张截图，直接在原图上拖动框选`);
  } finally {
    if (imported) { clearDraft(); shotIndex = 0; }
    busy = false; $('uploadFiles').value = ''; renderShots(); renderEditor(); renderComparison();
  }
}
function confirmAction(deleting) {
  if (!library || deleting && !selected || !deleting && !draft) return;
  const name = deleting ? selected.name : `${outputName()}.png`;
  if (!deleting && !selected && library.assets.some(a => a.name === name)) throw new Error('同名资源已存在，请从左侧选择该模板后覆盖');
  pendingAction = {deleting, body:{...draft,feature:library.feature,name:deleting ? name : outputName(),revision:library.revision,action:selected ? 'overwrite' : 'add'}};
  $('confirmTitle').textContent = deleting ? '删除模板资源' : selected ? '覆盖模板资源' : '新增模板资源';
  $('confirmMessage').textContent = `assets/zh-CN/${library.feature}/${name}`;
  $('confirmDetail').textContent = deleting ? `将删除此资源${selected.companions?.length ? `以及 ${selected.companions.join('、')}` : ''}并更新资产定义。引用此模板的功能代码需要同步检查。操作前自动备份。` : `${draft.mode === 'template' ? 'Template 局部小图' : 'Button 整屏黑底'}，保存后更新此功能的资产定义${selected ? '，旧资源自动备份' : ''}。`;
  $('confirmError').hidden = true; $('btnCommitAsset').className = deleting ? 'danger' : 'primary';
  updateButtons(); $('assetConfirm').showModal();
}
async function commitAction() {
  if (!pendingAction || busy) return;
  busy = true; updateButtons(); $('confirmError').hidden = true;
  try {
    const result = await api(pendingAction.deleting ? '/api/templates/delete' : '/api/templates/save', pendingAction.body);
    if (pendingAction.deleting) clearDraft();
    else { selectionDirty = metadataDirty = false; cropEditor.markSaved(); }
    library = result;
    const featureInfo = features.find(f => f.id === result.feature);
    if (featureInfo) featureInfo.count = result.assets.length;
    renderFeatures();
    selected = result.assets.find(a => a.name === result.saved_name) || null;
    $('outputName').value = selected ? selected.name.replace(/\.png$/, '') : '';
    $('backupInfo').textContent = `上次操作备份：${result.backup}`;
    renderAssets(); renderEditor(); await renderComparison(); $('assetConfirm').close();
    notify(result.saved_name ? `已保存 ${result.saved_name}，资产定义已更新` : '已删除资源，资产定义已更新');
  } catch (error) { $('confirmError').textContent = error.message; $('confirmError').hidden = false; }
  finally { busy = false; updateButtons(); }
}
bind('btnUpload', () => $('uploadFiles').click());
bind('btnNewAsset', () => chooseAsset(null));
bind('btnReload', async () => {
  if (!library) return;
  busy = true; updateButtons();
  try { await loadFeature(library.feature, true); notify('已读取最新资源，本次裁剪保留，请重新对比后保存'); }
  finally { busy = false; updateButtons(); }
});
bind('btnSaveAsset', () => confirmAction(false)); bind('btnDeleteAsset', () => confirmAction(true));
bind('btnCommitAsset', commitAction); bind('btnCancelAsset', () => { if (!busy) $('assetConfirm').close(); });
$('assetConfirm').addEventListener('cancel', e => { if (busy) e.preventDefault(); });
$('assetConfirm').addEventListener('close', () => { pendingAction = null; updateButtons(); });
$('featureSearch').addEventListener('input', renderFeatures);
$('featureSelect').addEventListener('change', async () => {
  if (!allowDiscard()) { $('featureSelect').value = library.feature; return; }
  busy = true; updateButtons();
  try { await loadFeature($('featureSelect').value); } catch (error) { notify(error.message, true); if (library) $('featureSelect').value = library.feature; }
  finally { busy = false; updateButtons(); }
});
$('assetSearch').addEventListener('input', renderAssets); $('assetType').addEventListener('change', renderAssets);
$('assetList').addEventListener('click', e => { const item = e.target.closest('[data-name]'); if (item) chooseAsset(item.dataset.name); });
$('outputName').addEventListener('input', () => { metadataDirty = true; selectionDirty = Boolean(selection?.box); renderEditor(); });
$('outputMode').addEventListener('change', () => { metadataDirty = true; selectionDirty = Boolean(selection?.box); refreshSelection(); });
$('sourceSelect').addEventListener('change', () => { if (!allowDiscard()) { $('sourceSelect').value = String(shotIndex); return; } clearDraft(); shotIndex = Number($('sourceSelect').value); renderShots(); renderEditor(); renderComparison(); });
$('showFull').addEventListener('change', renderComparison);
$('uploadFiles').addEventListener('change', e => importFiles([...e.target.files]).catch(error => notify(error.message, true)));
for (const event of ['dragenter','dragover']) $('dropZone').addEventListener(event, e => { e.preventDefault(); $('dropZone').classList.add('dragging'); });
$('dropZone').addEventListener('dragleave', () => $('dropZone').classList.remove('dragging'));
$('dropZone').addEventListener('drop', e => { e.preventDefault(); $('dropZone').classList.remove('dragging'); importFiles([...e.dataTransfer.files]).catch(error => notify(error.message, true)); });
window.addEventListener('paste', e => { if (document.querySelector('dialog[open]') || e.target.closest('input,textarea,select')) return; const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); importFiles(files).catch(error => notify(error.message, true)); } });
window.addEventListener('beforeunload', e => { if (selectionDirty || busy) { e.preventDefault(); e.returnValue = ''; } });
api('/api/templates/features').then(async data => {
  features = data.features; screenshots = data.screenshots; shotIndex = screenshots.length ? 0 : -1;
  renderFeatures(); renderShots();
  if (features.length) await loadFeature(features[0].id);
  notify('导入截图后直接框选，右侧实时对比；选择功能和模板名称后保存');
}).catch(error => notify(error.message, true)).finally(() => { busy = false; updateButtons(); });
