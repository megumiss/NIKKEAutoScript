'use strict';

function createWikiController(editor) {
  const $ = id => document.getElementById(id);
  let job = null, pending = false, reviewed = '';
  const active = () => pending || Boolean(job?.running);
  const post = (path, body) => editor.api(path, { method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Annotation-Token': editor.token() }, body: JSON.stringify(body) });
  function sync() {
    const map = editor.map(), blocked = active() || editor.busy();
    $('wiki-start').disabled = !map || blocked;
    $('wiki-stop').disabled = !job?.running || pending;
    $('wiki-difficulty').disabled = blocked;
    $('wiki-apply').disabled = blocked || !job?.ready || !job.accepted || job.map_id !== map?.id
      || job.revision !== map?.revision || editor.dirty();
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
    if (value.id && !value.running && reviewed !== value.id) {
      const items = await editor.api('/api/wiki/review');
      $('wiki-review').replaceChildren();
      for (const item of items) {
        const row = document.createElement('div'); row.className = 'wiki-item';
        const name = document.createElement('span'), status = document.createElement('span');
        name.textContent = `${item.difficulty === 'hard' ? '困难' : '普通'} ${item.number} · ${item.name}`;
        status.textContent = item.status === 'accepted' ? '通过' : '待复核';
        status.className = item.status === 'accepted' ? 'accepted' : 'muted';
        row.append(name, status); row.title = item.reason || status.textContent; $('wiki-review').append(row);
      }
      reviewed = value.id;
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
  setTimeout(poll, 0);
  return { get running() { return active(); }, sync };
}
