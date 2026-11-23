'use strict';

function createWikiController(editor) {
  const $ = id => document.getElementById(id);
  let job = null, pending = false, loading = false, review = null, reviewedJob = '';
  let selectedId = null, candidateIndex = 0, position = null, placing = false;
  const staged = new Set();
  const active = () => pending || Boolean(job?.running);
  const post = (path, body) => editor.api(path, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': editor.token() }, body: JSON.stringify(body) });
  const currentItem = () => review?.items.find(item => item.id === selectedId) || null;
  const actionable = item => Boolean(item && !item.imported && !item.skipped && !staged.has(item.id)
    && !active() && !loading && !editor.busy() && review?.map_id === editor.map()?.id);

  function visibleItems() {
    const filter = $('wiki-filter').value;
    return (review?.items || []).filter(item => {
      if (filter === 'pending') return !item.imported && !item.skipped && !staged.has(item.id);
      if (filter === 'skipped') return item.skipped && !item.imported;
      if (filter === 'imported') return item.imported || staged.has(item.id);
      return true;
    });
  }

  function sync() {
    let reconciled = false;
    for (const entry of review?.items || []) {
      if (entry.imported || entry.skipped) continue;
      if (editor.has(entry.id) && !staged.has(entry.id)) { staged.add(entry.id); reconciled = true; }
      if (!editor.has(entry.id) && staged.delete(entry.id)) reconciled = true;
    }
    if (reconciled) { renderSummary(); renderList(); }
    const map = editor.map(), blocked = active() || loading || editor.busy(), item = currentItem(), allowed = actionable(item);
    $('wiki-start').disabled = !map || blocked;
    $('wiki-stop').disabled = !job?.running || pending;
    $('wiki-difficulty').disabled = blocked || loading;
    $('wiki-filter').disabled = blocked || loading || !review;
    $('wiki-apply').disabled = blocked || !job?.ready || !job.accepted || job.map_id !== map?.id
      || job.revision !== map?.revision || editor.dirty();
    $('wiki-accept').disabled = !allowed || !position;
    $('wiki-place').disabled = !allowed;
    $('wiki-skip').disabled = !item || item.imported || staged.has(item.id) || blocked || loading;
    $('wiki-next').disabled = !review || blocked || loading || !visibleItems().length;
    $('wiki-candidates').disabled = !allowed || !item?.candidates.length;
  }

  function renderSummary() {
    if (!review) { $('wiki-review-summary').textContent = '0'; return; }
    const stagedHere = review.items.filter(item => staged.has(item.id) && !item.imported && !item.skipped).length;
    const remaining = review.items.filter(item => !item.imported && !item.skipped && !staged.has(item.id)).length;
    $('wiki-review-summary').textContent = `${remaining} 待处理${stagedHere ? ` · ${stagedHere} 待保存` : ''}`;
  }

  function statusText(item) {
    if (item.imported) return '已导入';
    if (staged.has(item.id)) return '待保存';
    if (item.skipped) return '已跳过';
    return item.status === 'accepted' ? '自动通过' : '待复核';
  }

  function renderList() {
    const list = $('wiki-review'); list.replaceChildren();
    const items = visibleItems();
    for (const item of items) {
      const row = document.createElement('button'); row.className = `wiki-item${item.id === selectedId ? ' selected' : ''}`;
      const name = document.createElement('span'), status = document.createElement('span'), detail = document.createElement('small');
      name.textContent = `${item.difficulty === 'hard' ? '困难' : '普通'} ${item.number} · ${item.name}`;
      status.textContent = statusText(item); status.className = item.status === 'accepted' ? 'accepted' : 'muted';
      detail.textContent = `${item.candidates.length} 个候选 · ${statusText(item)}`;
      row.append(name, status, detail); row.addEventListener('click', () => selectItem(item.id)); list.append(row);
    }
    if (!items.length) {
      const empty = document.createElement('p'); empty.className = 'muted empty-list';
      empty.textContent = review ? '当前显示范围内没有收集品。' : '正在读取本章 Wiki 缓存…'; list.append(empty);
    }
  }

  function renderDetail() {
    const item = currentItem(); $('wiki-detail').hidden = !item;
    if (!item) { editor.redraw(); return; }
    $('wiki-item-title').textContent = `${item.difficulty === 'hard' ? '困难' : '普通'} ${item.number} · ${item.name}`;
    let meta = statusText(item);
    if (!item.imported && !item.skipped && !staged.has(item.id)) {
      if (item.match_stale) meta += ' · 底图已变化，仅可手工点选';
      else if (item.candidates.length) meta += ` · ${item.candidates.length} 个候选`;
      else meta += ' · 无候选，需地图点选';
    }
    $('wiki-item-meta').textContent = meta;
    const images = $('wiki-images'); images.replaceChildren();
    for (const source of item.images) {
      if (!source.available) continue;
      const figure = document.createElement('figure'); figure.className = 'wiki-image';
      const image = document.createElement('img'); image.loading = 'lazy';
      image.alt = `${item.name}的Wiki${source.role === 'map' ? '地图' : '场景'}图片`;
      image.src = `/api/wiki/image?${new URLSearchParams({ map: review.map_id, difficulty: item.difficulty, url: source.url })}`;
      const caption = document.createElement('figcaption');
      caption.textContent = source.role === 'map' ? 'Wiki 地图图' : source.role === 'area' ? 'Wiki 场景图' : 'Wiki 原始图片';
      figure.append(image, caption); images.append(figure);
    }
    const picker = $('wiki-candidates'); picker.replaceChildren();
    item.candidates.forEach((candidate, index) => {
      const iou = Number(candidate.road_iou || 0).toFixed(3);
      picker.add(new Option(`候选 ${index + 1} · 道路重合度 ${iou}`, String(index)));
    });
    picker.value = String(candidateIndex);
    $('wiki-candidate-label').hidden = !item.candidates.length;
    $('wiki-position').textContent = placing ? '等待在地图上单击位置'
      : position ? `X ${position[0].toFixed(1)} · Y ${position[1].toFixed(1)}` : '尚未选择位置';
    $('wiki-placement-hint').textContent = placing ? '单击中间地图即可加入标注并跳到下一条；Esc 取消点选。'
      : position ? '候选已在画布标记；位置不准时改用地图点选。' : '使用“地图点选”指定收集品位置。';
    $('wiki-place').setAttribute('aria-pressed', String(placing));
    $('wiki-skip').textContent = item.skipped ? '恢复待处理' : '跳过 S';
    editor.redraw();
  }

  function render() { renderSummary(); renderList(); renderDetail(); sync(); }

  function selectItem(identifier, locate = true) {
    selectedId = identifier; candidateIndex = 0;
    const item = currentItem();
    position = item?.candidates[0]?.position || null;
    placing = Boolean(item && !item.imported && !item.skipped && !staged.has(item.id) && !position);
    if (item) {
      editor.preparePlacement(item.difficulty);
      if (position && locate) editor.showPosition(position, item.difficulty);
    }
    render();
  }

  function step(direction) {
    const items = visibleItems(); if (!items.length) { selectItem(null); return; }
    const index = items.findIndex(item => item.id === selectedId);
    if (index < 0) { selectItem(items[0].id); return; }
    selectItem(items[(index + direction + items.length) % items.length].id);
  }

  async function loadReview() {
    const map = editor.map();
    if (!map || loading || job?.running) return;
    const mode = $('wiki-difficulty').value;
    loading = true; sync();
    try {
      const value = await editor.api(`/api/wiki/review?${new URLSearchParams({ map: map.id, difficulty: mode })}`);
      if (editor.map()?.id !== value.map_id || $('wiki-difficulty').value !== value.difficulty) return;
      review = value; selectItem(visibleItems()[0]?.id || null, false);
    } catch (error) {
      review = null; selectedId = null; position = null; render();
      $('wiki-review-summary').textContent = `读取失败：${error.message}`;
    } finally {
      loading = false; sync();
      if (editor.map() && (editor.map().id !== map.id || $('wiki-difficulty').value !== mode)) loadReview();
    }
  }

  function opened() {
    review = null; reviewedJob = ''; selectedId = null; position = null; placing = false; staged.clear();
    render(); loadReview();
  }

  function annotationFor(item) { return item.annotation; }

  function accept() {
    const item = currentItem();
    if (!actionable(item) || !position) return;
    if (!editor.add(annotationFor(item), position)) return;
    staged.add(item.id); placing = false; position = null;
    const items = visibleItems();
    selectItem(items[0]?.id || null);
  }

  async function skip() {
    const item = currentItem();
    if (!item || item.imported || staged.has(item.id) || active()) return;
    pending = true; sync();
    try {
      const result = await post('/api/wiki/skip', { id: review.map_id, item: item.id, skipped: !item.skipped });
      item.skipped = result.skipped;
      if (item.skipped) selectItem(visibleItems()[0]?.id || null);
      else render();
    } catch (error) { $('wiki-review-summary').textContent = `操作失败：${error.message}`; }
    finally { pending = false; sync(); }
  }

  async function show(value) {
    job = value;
    $('wiki-status').textContent = value.message;
    $('wiki-status').classList.toggle('error', value.state === 'failed');
    $('wiki-counts').replaceChildren();
    for (const row of value.records || []) {
      const line = document.createElement('p');
      line.textContent = `${row.difficulty === 'hard' ? '困难' : '普通'}：${row.accepted || 0} / ${row.items || 0} 通过${row.status === 'missing_article' ? ' · 攻略缺失' : ''}`;
      $('wiki-counts').append(line);
    }
    $('wiki-log').textContent = value.log_path || '';
    editor.sync();
    if (value.id && !value.running && reviewedJob !== value.id && value.map_id === editor.map()?.id) {
      reviewedJob = value.id; loadReview();
    }
  }

  async function poll() {
    try { const value = await editor.api('/api/wiki'); if (!pending) await show(value); }
    catch (error) { $('wiki-status').textContent = `Wiki 状态读取失败：${error.message}`; }
    finally { setTimeout(poll, active() ? 1000 : 3000); }
  }

  $('wiki-start').addEventListener('click', async () => {
    if (active() || editor.busy()) return;
    if (editor.dirty() && !(await editor.save())) return;
    const map = editor.map(); if (!map) return;
    pending = true; editor.sync();
    try { await show(await post('/api/wiki/start', { id: map.id, revision: map.revision,
      image_sha256: map.annotations.image_sha256, difficulty: $('wiki-difficulty').value })); }
    catch (error) { $('wiki-status').textContent = `无法匹配：${error.message}`; }
    finally { pending = false; editor.sync(); }
  });
  $('wiki-stop').addEventListener('click', async () => {
    if (!job?.running || pending) return;
    try { await show(await post('/api/wiki/stop', { job: job.id })); }
    catch (error) { $('wiki-status').textContent = error.message; }
  });
  $('wiki-apply').addEventListener('click', async () => {
    if ($('wiki-apply').disabled) return;
    pending = true; editor.sync();
    try {
      await show(await post('/api/wiki/apply', { job: job.id, revision: editor.map().revision }));
      await editor.reload();
    } catch (error) { $('wiki-status').textContent = `导入失败：${error.message}`; }
    finally { pending = false; editor.sync(); }
  });
  $('wiki-filter').addEventListener('change', () => selectItem(visibleItems()[0]?.id || null, false));
  $('wiki-difficulty').addEventListener('change', loadReview);
  $('wiki-candidates').addEventListener('change', () => {
    const item = currentItem(); candidateIndex = Number($('wiki-candidates').value);
    position = item?.candidates[candidateIndex]?.position || null; placing = false;
    if (position) editor.showPosition(position, item.difficulty);
    render();
  });
  $('wiki-accept').addEventListener('click', accept);
  $('wiki-place').addEventListener('click', () => {
    if (!actionable(currentItem())) return;
    placing = true; position = null; editor.preparePlacement(currentItem().difficulty); render();
    $('canvas').focus({ preventScroll: true });
  });
  $('wiki-skip').addEventListener('click', skip);
  $('wiki-next').addEventListener('click', () => step(1));

  function draw(scale) {
    const group = $('wiki-markers'); group.replaceChildren();
    const item = currentItem();
    if ($('panel-wiki').hidden || !$('show-annotations').checked || !position || !actionable(item)) return;
    const color = item.difficulty === 'hard' ? '#c792ea' : '#ffd166';
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    for (const [key, value] of Object.entries({ cx: position[0], cy: position[1], r: 10 / scale,
      fill: 'none', stroke: color, 'stroke-width': 3 / scale })) circle.setAttribute(key, value);
    group.append(circle);
  }

  setTimeout(poll, 0);
  return {
    get running() { return active(); }, sync, draw, opened,
    saved() { staged.clear(); loadReview(); },
    click(point) {
      if (!placing || !actionable(currentItem())) return false;
      position = point; placing = false; accept(); return true;
    },
    keydown(event) {
      if ($('panel-wiki').hidden || active() || editor.busy()
          || event.target.closest('input, textarea, select, [contenteditable]')) return false;
      if (event.key === 'Enter' && position) { event.preventDefault(); accept(); return true; }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault(); step(event.key === 'ArrowDown' ? 1 : -1); return true;
      }
      if (event.key.toLowerCase() === 's' && !event.ctrlKey && !event.metaKey) {
        event.preventDefault(); skip(); return true;
      }
      if (event.key === 'Escape' && placing) {
        event.preventDefault(); placing = false;
        position = currentItem()?.candidates[candidateIndex]?.position || null; render(); return true;
      }
      return false;
    },
  };
}
