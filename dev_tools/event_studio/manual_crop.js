'use strict';
(() => {
  const dialog = $('cropDialog'), canvas = $('cropCanvas'), viewport = $('cropViewport');
  const coordinates = ['cropX0', 'cropY0', 'cropX1', 'cropY1'];
  let target = null, sourceIndex = -1, sourceImage = null, box = null;
  let zoom = 1, ox = 0, oy = 0, fit = true, gesture = null, sequence = 0;
  let dirty = false, saving = false, valid = false;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const validBox = () => box?.every(Number.isInteger) && box[0] >= 0 && box[1] >= 0
    && box[2] <= 720 && box[3] <= 1280 && box[2] - box[0] >= 2 && box[3] - box[1] >= 2;

  function allowDiscard() {
    return !saving && (!dirty || window.confirm('当前裁剪尚未保存，确定放弃修改？'));
  }
  function updateControls() {
    for (const el of dialog.querySelectorAll('button,input,select')) el.disabled = saving;
    for (const id of ['btnCropFit', 'btnCropActual', 'btnCropReset', 'cropPan', ...coordinates]) $(id).disabled = saving || !sourceImage;
    $('btnCropSave').disabled = saving || !valid;
    $('btnCropSave').textContent = saving ? '正在保存…' : '保存裁剪草稿';
    $('btnCropSave').setAttribute('aria-busy', String(saving));
  }
  function updateSelection(writeInputs = true) {
    if (writeInputs) coordinates.forEach((id, i) => $(id).value = Number.isFinite(box?.[i]) ? box[i] : '');
    const hasBox = sourceImage && validBox();
    const preview = $('cropPreview');
    preview.hidden = !hasBox; $('cropPreviewEmpty').hidden = Boolean(hasBox);
    $('cropDimensions').textContent = hasBox ? `${box[2] - box[0]} × ${box[3] - box[1]}` : '';
    valid = false;
    let message = box ? '请输入有效整数坐标，选区至少 2 × 2 px，且不能超出原图。' : '请在原图上框选，或填写四个坐标。';
    if (hasBox) {
      preview.width = box[2] - box[0]; preview.height = box[3] - box[1];
      const ctx = preview.getContext('2d');
      ctx.drawImage(sourceImage, box[0], box[1], preview.width, preview.height, 0, 0, preview.width, preview.height);
      valid = ctx.getImageData(0, 0, preview.width, preview.height).data.some((value, i) => i % 4 !== 3 && value > 0);
      message = valid ? '选区有效，可保存为草稿。' : '选区全黑，无法生成有效模板，请重新框选。';
    }
    $('cropValidation').textContent = sourceImage ? message : '';
    $('cropValidation').classList.toggle('invalid', Boolean(sourceImage && box && !valid));
    updateControls(); drawCrop();
  }
  function drawCrop() {
    if (!dialog.open) return;
    const {width, height} = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!sourceImage) { $('cropZoom').textContent = '—'; return; }
    ctx.imageSmoothingEnabled = zoom < 1;
    ctx.drawImage(sourceImage, ox, oy, 720 * zoom, 1280 * zoom);
    if (validBox()) {
      const x = ox + box[0] * zoom, y = oy + box[1] * zoom;
      const w = (box[2] - box[0]) * zoom, h = (box[3] - box[1]) * zoom;
      ctx.fillStyle = '#0008'; ctx.beginPath(); ctx.rect(0, 0, width, height); ctx.rect(x, y, w, h); ctx.fill('evenodd');
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 3; ctx.strokeRect(x, y, w, h);
      ctx.strokeStyle = '#2563eb'; ctx.lineWidth = 1; ctx.strokeRect(x, y, w, h);
      for (const [hx, hy] of [[x,y], [x+w,y], [x,y+h], [x+w,y+h]]) {
        ctx.fillStyle = '#fff'; ctx.fillRect(hx-4, hy-4, 8, 8); ctx.strokeRect(hx-4, hy-4, 8, 8);
      }
    }
    $('cropZoom').textContent = `${Math.round(zoom * 100)}%`;
  }
  function fitImage() {
    if (!sourceImage || !dialog.open) return;
    const r = canvas.getBoundingClientRect();
    zoom = Math.max(.05, Math.min((r.width - 16) / 720, (r.height - 16) / 1280));
    ox = (r.width - 720 * zoom) / 2; oy = (r.height - 1280 * zoom) / 2;
    fit = true; drawCrop();
  }
  function zoomAt(next, x, y) {
    next = clamp(next, .05, 8);
    ox = x - (x - ox) * next / zoom; oy = y - (y - oy) * next / zoom;
    zoom = next; fit = false; drawCrop();
  }
  function loadSource(index) {
    sourceIndex = index; $('cropSource').value = String(index);
    const shot = state.session.screenshots[index], seq = ++sequence;
    sourceImage = null; gesture = null; fit = true;
    box = shot.file === target.source && target.box ? [...target.box] : null;
    $('cropSourceHint').textContent = `${state.scenes[sceneKey(shot)]?.label || '未关联场景'} · ${baseName(shot.file)}`;
    $('cropSourceHint').title = shot.file;
    $('cropImageMessage').hidden = false; $('cropImageMessage').textContent = '正在读取原图…';
    updateSelection();
    const next = new Image();
    next.onload = () => {
      if (seq !== sequence || !dialog.open) return;
      if (next.naturalWidth !== 720 || next.naturalHeight !== 1280) {
        $('cropImageMessage').textContent = '请使用 720 × 1280 原始截图。'; return;
      }
      sourceImage = next; $('cropImageMessage').hidden = true; fitImage(); updateSelection();
    };
    next.onerror = () => {
      if (seq === sequence && dialog.open) $('cropImageMessage').textContent = '原图读取失败，请重新选择来源截图。';
    };
    next.src = `/api/image?path=${encodeURIComponent(shot.file)}`;
  }
  function chooseTemplate(name, preferred = sourceIndex) {
    target = state.session.templates.find(t => t.name === name); dirty = false;
    $('cropTemplate').value = name; $('cropError').hidden = true;
    const rule = state.rules[name] || {};
    $('cropRule').textContent = [rule.desc, state.scenes[sceneKey(target)]?.label, rule.requires].filter(Boolean).join(' · ');
    $('cropMode').textContent = target.mode === 'button' ? '导出为 720 × 1280 黑底图片，保留选区原始坐标。' : '导出为原始像素局部小图，使用 TEMPLATE_ 文件名前缀。';
    $('cropConfirmed').hidden = target.status !== 'confirmed';
    const shots = state.session.screenshots;
    $('cropSource').innerHTML = shots.map((s, i) => `<option value="${i}">${esc(state.scenes[sceneKey(s)]?.label || '未关联场景')} · ${esc(baseName(s.file))}</option>`).join('');
    const existing = shots.findIndex(s => s.file === target.source && s.phase === target.phase);
    const matching = shots.findIndex(s => sceneKey(s) === sceneKey(target));
    const selected = existing >= 0 ? existing : sceneKey(shots[preferred]) === sceneKey(target) ? preferred : matching >= 0 ? matching : preferred >= 0 ? preferred : 0;
    setPreview('cropReference', 'cropReferenceEmpty', `/api/reference?name=${encodeURIComponent(name)}&event_id=${encodeURIComponent(state.current)}&v=${state.session.revision || 0}`);
    loadSource(selected);
  }
  async function openCrop(fromReview) {
    await saveAnnotation(); if (reviewDirty) await saveReview();
    const items = state?.session?.templates || [], shots = state?.session?.screenshots || [];
    if (!items.length || !shots.length) return;
    const candidate = fromReview ? items.find(t => t.name === templateName)
      : items.find(t => t.status !== 'confirmed' && sceneKey(t) === sceneKey(shots[shotIndex]))
        || items.find(t => t.status !== 'confirmed') || items[0];
    if (!candidate) return;
    $('cropTemplate').innerHTML = items.map(t => `<option value="${esc(t.name)}">${esc(t.name)} · ${esc(state.rules[t.name]?.desc || '')} · ${esc(state.status_label[t.status])}</option>`).join('');
    $('cropPan').checked = false; canvas.style.cursor = 'crosshair';
    dialog.showModal(); chooseTemplate(candidate.name, shotIndex); canvas.focus();
  }
  function closeCrop() { if (allowDiscard()) dialog.close(); }
  bind('btnManualStart', () => openCrop(false));
  bind('btnManualCrop', () => openCrop(true));
  $('btnCropClose').addEventListener('click', closeCrop);
  dialog.addEventListener('cancel', e => { e.preventDefault(); closeCrop(); });
  dialog.addEventListener('close', () => { ++sequence; dirty = false; gesture = null; sourceImage = null; });
  window.addEventListener('beforeunload', e => { if (dirty || saving) { e.preventDefault(); e.returnValue = ''; } });
  $('cropTemplate').addEventListener('change', () => {
    if (!allowDiscard()) { $('cropTemplate').value = target.name; return; }
    chooseTemplate($('cropTemplate').value);
  });
  $('cropSource').addEventListener('change', () => {
    if (!allowDiscard()) { $('cropSource').value = String(sourceIndex); return; }
    loadSource(Number($('cropSource').value)); dirty = false;
  });
  for (const id of coordinates) $(id).addEventListener('input', () => {
    box = coordinates.map(key => $(key).valueAsNumber); dirty = true; updateSelection(false);
  });
  $('btnCropReset').addEventListener('click', () => {
    box = null; dirty = true; $('cropPan').checked = false; canvas.style.cursor = 'crosshair'; updateSelection(); canvas.focus();
  });
  $('btnCropFit').addEventListener('click', fitImage);
  $('btnCropActual').addEventListener('click', () => {
    const r = canvas.getBoundingClientRect(); zoomAt(1, r.width / 2, r.height / 2);
  });
  $('cropPan').addEventListener('change', () => canvas.style.cursor = $('cropPan').checked ? 'grab' : 'crosshair');
  new ResizeObserver(() => { if (fit) fitImage(); else drawCrop(); }).observe(viewport);

  function imagePoint(e) {
    const r = canvas.getBoundingClientRect();
    return [clamp(Math.round((e.clientX - r.left - ox) / zoom), 0, 720), clamp(Math.round((e.clientY - r.top - oy) / zoom), 0, 1280)];
  }
  function hitTest(e) {
    if (!validBox()) return {mode:'draw'};
    const r = canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
    for (const [xi, yi] of [[0,1], [2,1], [0,3], [2,3]]) {
      if (Math.abs(x - ox - box[xi] * zoom) <= 8 && Math.abs(y - oy - box[yi] * zoom) <= 8) return {mode:'resize', xi, yi};
    }
    return {mode:x >= ox + box[0]*zoom && x <= ox + box[2]*zoom && y >= oy + box[1]*zoom && y <= oy + box[3]*zoom ? 'move' : 'draw'};
  }
  function moveBox(original, dx, dy) {
    dx = clamp(dx, -original[0], 720-original[2]); dy = clamp(dy, -original[1], 1280-original[3]);
    box = [original[0]+dx, original[1]+dy, original[2]+dx, original[3]+dy];
  }
  canvas.addEventListener('contextmenu', e => e.preventDefault());
  canvas.addEventListener('pointerdown', e => {
    if (!sourceImage || saving || gesture || ![0,1,2].includes(e.button)) return;
    e.preventDefault(); canvas.focus();
    const pan = e.button !== 0 || $('cropPan').checked;
    gesture = {...(pan ? {mode:'pan'} : hitTest(e)), start:imagePoint(e), client:[e.clientX,e.clientY],
      original:box ? [...box] : null, wasDirty:dirty, pointer:e.pointerId, offset:[ox,oy]};
    if (gesture.mode === 'draw') { box = [...gesture.start, ...gesture.start]; dirty = true; updateSelection(); }
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', e => {
    if (!sourceImage || saving) return;
    if (!gesture) {
      const hit = hitTest(e);
      canvas.style.cursor = $('cropPan').checked ? 'grab' : hit.mode === 'move' ? 'move' : hit.mode === 'resize' ? (hit.xi + hit.yi === 3 ? 'nesw-resize' : 'nwse-resize') : 'crosshair';
      return;
    }
    if (e.pointerId !== gesture.pointer) return;
    const [x,y] = imagePoint(e), [sx,sy] = gesture.start;
    if (gesture.mode === 'pan') {
      ox = gesture.offset[0] + e.clientX - gesture.client[0]; oy = gesture.offset[1] + e.clientY - gesture.client[1]; fit = false; drawCrop(); return;
    }
    if (gesture.mode === 'move') moveBox(gesture.original, x-sx, y-sy);
    else if (gesture.mode === 'resize') {
      const fixedX = gesture.original[gesture.xi === 0 ? 2 : 0], fixedY = gesture.original[gesture.yi === 1 ? 3 : 1];
      box = [Math.min(x,fixedX), Math.min(y,fixedY), Math.max(x,fixedX), Math.max(y,fixedY)];
    } else box = [Math.min(x,sx), Math.min(y,sy), Math.max(x,sx), Math.max(y,sy)];
    dirty = true; updateSelection();
  });
  function endGesture(e) {
    if (!gesture || gesture.pointer !== e.pointerId) return;
    if (e.type === 'pointercancel' || (gesture.mode === 'draw' || gesture.mode === 'resize') && !validBox()) {
      box = gesture.original; dirty = gesture.wasDirty; updateSelection();
    }
    gesture = null;
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  }
  canvas.addEventListener('pointerup', endGesture); canvas.addEventListener('pointercancel', endGesture);
  canvas.addEventListener('lostpointercapture', endGesture);
  canvas.addEventListener('wheel', e => {
    e.preventDefault(); if (!sourceImage || saving || gesture) return;
    const r = canvas.getBoundingClientRect(); zoomAt(zoom * (e.deltaY < 0 ? 1.12 : 1/1.12), e.clientX-r.left, e.clientY-r.top);
  }, {passive:false});
  canvas.addEventListener('keydown', e => {
    if (!sourceImage || saving || gesture) return;
    const direction = {ArrowLeft:[-1,0], ArrowRight:[1,0], ArrowUp:[0,-1], ArrowDown:[0,1]}[e.key];
    if (direction && validBox()) {
      e.preventDefault(); const step = e.shiftKey ? 10 : 1;
      moveBox(box, direction[0]*step, direction[1]*step); dirty = true; updateSelection();
    }
    if (['+','=','-'].includes(e.key)) {
      e.preventDefault(); const r = canvas.getBoundingClientRect(); zoomAt(zoom * (e.key === '-' ? 1/1.12 : 1.12), r.width/2, r.height/2);
    }
  });
  $('btnCropSave').addEventListener('click', async () => {
    if (!valid || saving || gesture) return;
    saving = true; updateControls(); $('cropError').hidden = true;
    try {
      const name = target.name, index = sourceIndex;
      const next = await api('/api/propose', {name, source:state.session.screenshots[index].file, box:[...box], proposer:'user'});
      dirty = false; templateName = name; shotIndex = index;
      applyState(next); switchView('review'); dialog.close();
      $('templateSearch').value = ''; $('templateScene').value = ''; $('templateStatus').value = ''; renderTemplates();
      $('btnManualCrop').focus(); notify('手动裁剪已保存为草稿，请校对后确认模板');
    } catch (error) {
      $('cropError').textContent = error.message; $('cropError').hidden = false;
    } finally { saving = false; updateControls(); }
  });
})();
