'use strict';
window.createCropEditor = function ({onChange, isActive = () => true}) {
  const canvas = document.getElementById('cropCanvas'), viewport = document.getElementById('cropViewport');
  const coordinates = ['cropX0', 'cropY0', 'cropX1', 'cropY1'].map(id => document.getElementById(id));
  const pan = document.getElementById('cropPan'), message = document.getElementById('cropImageMessage');
  const fitButton = document.getElementById('btnCropFit'), actualButton = document.getElementById('btnCropActual');
  const resetButton = document.getElementById('btnCropReset'), zoomLabel = document.getElementById('cropZoom');
  const sample = document.createElement('canvas');
  let image = null, source = '', box = null, disabled = false, sequence = 0, gesture = null;
  let zoom = 1, ox = 0, oy = 0, fit = true, changed = false;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const validBox = () => image && box?.every(Number.isInteger) && box[0] >= 0 && box[1] >= 0
    && box[2] <= image.naturalWidth && box[3] <= image.naturalHeight && box[2] - box[0] >= 2 && box[3] - box[1] >= 2;
  function controls() {
    for (const el of [pan, fitButton, actualButton, resetButton, ...coordinates]) el.disabled = disabled || !image;
    canvas.setAttribute('aria-disabled', String(disabled || !image));
    canvas.style.cursor = disabled ? 'default' : pan.checked ? 'grab' : 'crosshair';
  }
  function draw() {
    if (!isActive()) return;
    const {width, height} = canvas.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (!image) { zoomLabel.textContent = '—'; return; }
    ctx.imageSmoothingEnabled = zoom < 1;
    ctx.drawImage(image, ox, oy, image.naturalWidth * zoom, image.naturalHeight * zoom);
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
    zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
  }
  function publish(writeInputs = true) {
    if (writeInputs) coordinates.forEach((input, i) => input.value = Number.isFinite(box?.[i]) ? box[i] : '');
    let valid = false;
    if (validBox()) {
      sample.width = box[2] - box[0]; sample.height = box[3] - box[1];
      const ctx = sample.getContext('2d', {willReadFrequently:true});
      ctx.drawImage(image, box[0], box[1], sample.width, sample.height, 0, 0, sample.width, sample.height);
      valid = ctx.getImageData(0, 0, sample.width, sample.height).data.some((value, i) => i % 4 !== 3 && value > 0);
    }
    const error = !image || !box ? '' : !validBox() ? '请输入有效整数坐标，选区至少 2 × 2 px，且不能超出原图。'
      : valid ? '' : '选区全黑，请重新框选。';
    controls(); draw();
    onChange({source, image, box:box ? [...box] : null, valid, error, changed, editing:Boolean(gesture)});
  }
  function fitImage() {
    if (!image || !isActive()) return;
    const r = canvas.getBoundingClientRect();
    zoom = Math.max(.01, Math.min((r.width - 16) / image.naturalWidth, (r.height - 16) / image.naturalHeight));
    ox = (r.width - image.naturalWidth * zoom) / 2; oy = (r.height - image.naturalHeight * zoom) / 2;
    fit = true; draw();
  }
  function zoomAt(next, x, y) {
    next = clamp(next, .01, 8); ox = x - (x - ox) * next / zoom; oy = y - (y - oy) * next / zoom;
    zoom = next; fit = false; draw();
  }
  function load(file, initialBox = null, requiredSize = null) {
    const seq = ++sequence;
    source = file || ''; image = null; box = initialBox ? [...initialBox] : null;
    gesture = null; changed = false; fit = true; pan.checked = false;
    message.hidden = false; message.textContent = source ? '正在读取原图…' : '导入或选择截图后，直接在这里拖动框选。';
    publish();
    if (!source) return;
    const next = new Image();
    next.onload = () => {
      if (seq !== sequence) return;
      if (requiredSize && (next.naturalWidth !== requiredSize[0] || next.naturalHeight !== requiredSize[1])) {
        message.textContent = `请使用 ${requiredSize[0]} × ${requiredSize[1]} 原始截图。`; return;
      }
      image = next; message.hidden = true;
      coordinates.forEach((input, i) => input.max = (i % 2 ? image.naturalHeight : image.naturalWidth) - (i < 2 ? 2 : 0));
      fitImage(); publish();
    };
    next.onerror = () => { if (seq === sequence) message.textContent = '原图读取失败，请重新导入或选择截图。'; };
    next.src = `/api/image?path=${encodeURIComponent(source)}`;
  }
  function point(e) {
    const r = canvas.getBoundingClientRect();
    return [clamp(Math.round((e.clientX-r.left-ox)/zoom), 0, image.naturalWidth), clamp(Math.round((e.clientY-r.top-oy)/zoom), 0, image.naturalHeight)];
  }
  function hitTest(e) {
    if (!validBox()) return {mode:'draw'};
    const r = canvas.getBoundingClientRect(), x = e.clientX-r.left, y = e.clientY-r.top;
    for (const [xi, yi] of [[0,1], [2,1], [0,3], [2,3]]) {
      if (Math.abs(x-ox-box[xi]*zoom) <= 8 && Math.abs(y-oy-box[yi]*zoom) <= 8) return {mode:'resize',xi,yi};
    }
    return {mode:x >= ox+box[0]*zoom && x <= ox+box[2]*zoom && y >= oy+box[1]*zoom && y <= oy+box[3]*zoom ? 'move' : 'draw'};
  }
  function moveBox(original, dx, dy) {
    dx = clamp(dx, -original[0], image.naturalWidth-original[2]); dy = clamp(dy, -original[1], image.naturalHeight-original[3]);
    box = [original[0]+dx, original[1]+dy, original[2]+dx, original[3]+dy];
  }
  coordinates.forEach(input => input.addEventListener('input', () => {
    if (disabled || !image) return;
    box = coordinates.map(el => el.valueAsNumber); changed = true; publish(false);
  }));
  resetButton.addEventListener('click', () => {
    if (disabled || !image) return;
    box = null; changed = true; pan.checked = false; publish(); canvas.focus();
  });
  fitButton.addEventListener('click', fitImage);
  actualButton.addEventListener('click', () => {
    if (!image) return;
    const r = canvas.getBoundingClientRect(); zoomAt(1, r.width/2, r.height/2);
  });
  pan.addEventListener('change', controls);
  new ResizeObserver(() => { if (fit) fitImage(); else draw(); }).observe(viewport);
  canvas.addEventListener('contextmenu', e => e.preventDefault());
  canvas.addEventListener('pointerdown', e => {
    if (!image || disabled || gesture || ![0,1,2].includes(e.button)) return;
    e.preventDefault(); canvas.focus();
    gesture = {...(e.button !== 0 || pan.checked ? {mode:'pan'} : hitTest(e)), start:point(e), client:[e.clientX,e.clientY],
      original:box ? [...box] : null, wasChanged:changed, pointer:e.pointerId, offset:[ox,oy]};
    if (gesture.mode === 'draw') { box = [...gesture.start,...gesture.start]; changed = true; }
    publish(); canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener('pointermove', e => {
    if (!image || disabled) return;
    if (!gesture) {
      const hit = hitTest(e);
      canvas.style.cursor = pan.checked ? 'grab' : hit.mode === 'move' ? 'move' : hit.mode === 'resize' ? (hit.xi+hit.yi === 3 ? 'nesw-resize' : 'nwse-resize') : 'crosshair'; return;
    }
    if (e.pointerId !== gesture.pointer) return;
    const [x,y] = point(e), [sx,sy] = gesture.start;
    if (gesture.mode === 'pan') {
      ox = gesture.offset[0]+e.clientX-gesture.client[0]; oy = gesture.offset[1]+e.clientY-gesture.client[1]; fit = false; draw(); return;
    }
    if (gesture.mode === 'move') moveBox(gesture.original, x-sx, y-sy);
    else if (gesture.mode === 'resize') {
      const fx = gesture.original[gesture.xi === 0 ? 2 : 0], fy = gesture.original[gesture.yi === 1 ? 3 : 1];
      box = [Math.min(x,fx),Math.min(y,fy),Math.max(x,fx),Math.max(y,fy)];
    } else box = [Math.min(x,sx),Math.min(y,sy),Math.max(x,sx),Math.max(y,sy)];
    changed = true; publish();
  });
  function endGesture(e) {
    if (!gesture || gesture.pointer !== e.pointerId) return;
    if (e.type === 'pointercancel' || (gesture.mode === 'draw' || gesture.mode === 'resize') && !validBox()) {
      box = gesture.original; changed = gesture.wasChanged;
    }
    gesture = null;
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    publish();
  }
  canvas.addEventListener('pointerup', endGesture); canvas.addEventListener('pointercancel', endGesture);
  canvas.addEventListener('lostpointercapture', endGesture);
  canvas.addEventListener('wheel', e => {
    e.preventDefault(); if (!image || disabled || gesture) return;
    const r = canvas.getBoundingClientRect(); zoomAt(zoom * (e.deltaY < 0 ? 1.12 : 1/1.12), e.clientX-r.left, e.clientY-r.top);
  }, {passive:false});
  canvas.addEventListener('keydown', e => {
    if (!image || disabled || gesture) return;
    const direction = {ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[e.key];
    if (direction && validBox()) {
      e.preventDefault(); const step = e.shiftKey ? 10 : 1;
      moveBox(box, direction[0]*step, direction[1]*step); changed = true; publish();
    }
    if (['+','=','-'].includes(e.key)) {
      e.preventDefault(); const r = canvas.getBoundingClientRect(); zoomAt(zoom*(e.key === '-' ? 1/1.12 : 1.12), r.width/2,r.height/2);
    }
  });
  controls();
  return {
    load,
    setBox(value) { box = value ? [...value] : null; gesture = null; changed = false; publish(); },
    markSaved() { changed = false; },
    setDisabled(value) { disabled = value; controls(); },
  };
};
