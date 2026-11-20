'use strict';
(() => {
  let selection = null, saving = false, sessionId = null, activeName = '', sourceKey = '', savedBoxKey = '';
  const editor = createCropEditor({onChange:value => {
    selection = value;
    const preview = $('cropPreview');
    $('liveCropSection').hidden = !value.valid || !value.changed;
    $('cropDimensions').textContent = value.valid ? `${value.box[2]-value.box[0]} × ${value.box[3]-value.box[1]}` : '';
    if (value.valid) {
      const [x0,y0,x1,y1] = value.box; preview.width = x1-x0; preview.height = y1-y0;
      preview.getContext('2d').drawImage(value.image,x0,y0,x1-x0,y1-y0,0,0,x1-x0,y1-y0);
    }
    updateControls(); updateButtons();
    if (value.valid && value.changed && view === 'source') switchView('review');
  }});
  function dirty() { return Boolean(selection?.changed); }
  function allowDiscard() {
    if (saving) return false;
    if (dirty() && !window.confirm('当前裁剪尚未保存，确定放弃修改？')) return false;
    editor.markSaved(); if (selection) selection.changed = false; return true;
  }
  function updateControls() {
    $('liveCropSection').hidden = !selection?.valid || !dirty();
    const target = state?.session?.templates.find(t => t.name === activeName);
    $('cropTemplate').disabled = saving || !state?.session?.templates.length;
    $('cropMode').textContent = target ? target.mode === 'template' ? 'Template · 保存局部小图' : 'Button · 保留 720 × 1280 原坐标，选区外填黑' : '';
    const rule = state?.rules?.[activeName];
    $('cropRule').textContent = target ? [rule?.desc,state.scenes[sceneKey(target)]?.label].filter(Boolean).join(' · ') : '';
    $('cropConfirmed').hidden = target?.status !== 'confirmed' || !dirty();
    $('cropValidation').textContent = selection?.error || (!target ? '可先框选，再选择对应的目标模板。' : !selection?.image ? '选择场景截图。' : selection?.valid ? dirty() ? '选区已保留，点击“保存并确认模板”完成本次裁剪。' : '直接拖动选区调整，或在原图上重新框选。' : '直接在原图上拖动框选，或输入选区坐标。');
    $('cropValidation').classList.toggle('invalid', Boolean(selection?.error));
    $('btnConfirm').textContent = saving ? '正在保存并确认…' : '保存并确认模板';
    editor.setDisabled(saving || !state?.session);
  }
  function canConfirm() {
    const target = state?.session?.templates.find(t => t.name === activeName);
    if (saving || !target) return false;
    if (dirty()) return Boolean(selection?.valid && !selection.editing);
    return target.status !== 'confirmed' && confirmInfo?.name === target.name && confirmInfo.valid;
  }
  function sync() {
    const session = state?.session, shot = session?.screenshots[shotIndex];
    if (sessionId !== state?.current) {
      sessionId = state?.current; activeName = ''; sourceKey = ''; savedBoxKey = ''; editor.load('');
    }
    const target = session?.templates.find(t => t.name === templateName);
    $('cropTemplate').innerHTML = '<option value="">先框选或选择模板</option>' + (session?.templates || []).map(t => `<option value="${esc(t.name)}">${[t.name,t.mode === 'template' ? 'Template' : 'Button',state.status_label[t.status]].map(esc).join(' · ')}</option>`).join('');
    $('cropTemplate').value = target?.name || '';
    const key = shot?.file || '';
    const initialBox = target?.source === shot?.file && target?.phase === shot?.phase ? target?.box : null;
    const boxKey = JSON.stringify(initialBox || null);
    const targetChanged = activeName !== (target?.name || '');
    activeName = target?.name || '';
    if (sourceKey !== key) {
      sourceKey = key; savedBoxKey = boxKey; $('cropError').hidden = true;
      editor.load(shot?.file || '',initialBox,[720,1280]);
    } else if (!dirty() && (targetChanged || savedBoxKey !== boxKey)) {
      savedBoxKey = boxKey; $('cropError').hidden = true; editor.setBox(initialBox);
    }
    updateControls();
  }
  window.eventCrop = {sync,dirty,allowDiscard,canConfirm,confirm,busy:() => saving};
  function focusCanvas() {
    $('cropCanvas').scrollIntoView({behavior:'smooth',block:'center'}); $('cropCanvas').focus({preventScroll:true});
  }
  bind('btnManualStart',async () => {
    await saveAnnotation(); if (reviewDirty) await saveReview();
    if (templateName) await selectTemplate(templateName);
    switchView('review'); focusCanvas();
  });
  bind('btnManualCrop',focusCanvas);
  $('cropTemplate').addEventListener('change',async () => {
    try { await selectTemplate($('cropTemplate').value); switchView('review'); }
    catch (error) { notify(error.message,true); }
    sync();
  });
  window.addEventListener('beforeunload',e => { if (dirty() || saving) { e.preventDefault(); e.returnValue = ''; } });
  async function confirm() {
    if (!canConfirm()) return false;
    const name = activeName, crop = dirty() ? {source:selection.source,box:[...selection.box]} : null;
    saving = true; updateControls(); updateButtons(); $('cropError').hidden = true;
    try {
      await saveAnnotation(); if (reviewDirty) await saveReview();
      const next = crop
        ? await api('/api/propose',{name,...crop,proposer:'user',confirm:true})
        : await api('/api/status',{name,value:'confirmed'});
      editor.markSaved(); if (selection) selection.changed = false;
      applyState(next); switchView('review');
      $('templateSearch').value = ''; $('templateScene').value = ''; $('templateStatus').value = ''; renderTemplates();
      notify(crop ? '裁剪已保存并确认，可直接导出模板' : '模板已确认');
      return true;
    } catch (error) {
      $('cropError').textContent = error.message; $('cropError').hidden = false;
      return false;
    } finally { saving = false; updateControls(); updateButtons(); }
  }
  sync();
})();
