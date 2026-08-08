/**
 * NKAS DAU 统计 Worker
 *
 * 接口：
 *   POST /report  Body: {"id": "<匿名ID>", "version": "...", "os": "...", "res": "..."}
 *                 按天（UTC+8）对匿名ID去重计数，并按版本/系统/分辨率/地区维度分别计数。
 *                 地区无需客户端上报，由服务端从 request.cf.country 获取。
 *   GET  /stats?date=YYYY-MM-DD&token=<STATS_TOKEN>
 *                 返回当天 DAU 及各维度分布；date 缺省为今天。
 *   GET  /trend?days=30&token=<STATS_TOKEN>
 *                 返回最近 days 天（含今天）的每日 DAU 列表。
 *   GET  /        展示页面（HTML），输入口令后查看趋势与各维度占比。
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const TZ_OFFSET_MS = 8 * 60 * 60 * 1000; // UTC+8
// 去重 key 保留 8 天，统计计数 key 保留 92 天
const DEDUP_TTL_SECONDS = 8 * 24 * 3600;
const COUNT_TTL_SECONDS = 92 * 24 * 3600;
// 维度 key 前缀：v=版本 os=系统 res=分辨率 geo=地区
const DIMS = ['v', 'os', 'res', 'geo'];
const OS_LABELS = ['Windows 10', 'Windows 11', 'Linux', 'macOS'];

function today() {
  return new Date(Date.now() + TZ_OFFSET_MS).toISOString().slice(0, 10);
}

function isValidDate(s) {
  return /^\d{4}-\d{2}-\d{2}$/.test(s);
}

function isValidId(id) {
  return typeof id === 'string' && /^[0-9a-f-]{8,64}$/i.test(id);
}

function sanitizeVersion(v) {
  if (typeof v !== 'string' || !v) return 'unknown';
  return /^[\w.-]{1,32}$/.test(v) ? v : 'other';
}

function sanitizeOs(os) {
  if (typeof os !== 'string' || !os) return 'unknown';
  return OS_LABELS.includes(os) ? os : 'other';
}

function sanitizeRes(res) {
  if (typeof res !== 'string' || !res) return 'unknown';
  const m = /^(\d{3,5})[x*×](\d{3,5})$/i.exec(res.trim());
  return m ? `${m[1]}x${m[2]}` : 'other';
}

function sanitizeCountry(c) {
  return typeof c === 'string' && /^[A-Z0-9]{2}$/.test(c) ? c : 'unknown';
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function checkAuth(url, env) {
  return url.searchParams.get('token') === env.STATS_TOKEN;
}

function dateOffset(day, offsetDays) {
  const t = new Date(`${day}T00:00:00Z`).getTime() - offsetDays * DAY_MS;
  return new Date(t).toISOString().slice(0, 10);
}

async function getDist(env, dim, date) {
  const prefix = `${dim}:${date}:`;
  const list = await env.STATS.list({ prefix });
  const values = await Promise.all(list.keys.map((k) => env.STATS.get(k.name)));
  const dist = {};
  list.keys.forEach((k, i) => {
    dist[k.name.slice(prefix.length)] = parseInt(values[i] || '0', 10);
  });
  return dist;
}

const INDEX_HTML = `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NKAS DAU 统计</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 760px; margin: 2em auto; padding: 0 1em; color: #222; }
  h1 { font-size: 1.4em; }
  h2 { font-size: 1.05em; margin: 0 0 .6em; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 1em 1.2em; margin: 1em 0; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 1.5em; }
  @media (max-width: 640px) { .grid { grid-template-columns: 1fr; } }
  .dau { font-size: 2.4em; font-weight: 700; }
  .bar-row { display: flex; align-items: center; gap: .5em; font-size: .85em; margin: 2px 0; }
  .bar-row .label { width: 8em; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bar { background: #4a90d9; height: 14px; border-radius: 2px; min-width: 2px; }
  .bar-row .num { white-space: nowrap; }
  input { padding: 6px 8px; width: 16em; }
  button { padding: 6px 12px; cursor: pointer; }
  .err { color: #c00; }
  .trend-chart { width: 100%; height: auto; display: block; }
  .trend-chart .col { fill: #4a90d9; }
  .trend-chart .col:hover { fill: #357abd; }
  .trend-chart .axis { stroke: #ccc; stroke-width: 1; }
  .trend-chart text { font-size: 10px; fill: #666; }
</style>
</head>
<body>
<h1>NKAS 每日活跃用户统计</h1>
<div id="auth" class="card">
  <input id="token" type="password" placeholder="访问口令">
  <button onclick="saveToken()">确定</button>
  <span id="authErr" class="err"></span>
</div>
<div id="content" style="display:none">
  <div class="card"><h2>今日 DAU（UTC+8）</h2><div class="dau" id="dau">-</div></div>
  <div class="card"><h2>最近 30 天用户趋势</h2><div id="trend"></div></div>
  <div class="grid">
    <div class="card"><h2>版本占比</h2><div id="dim-v"></div></div>
    <div class="card"><h2>系统占比</h2><div id="dim-os"></div></div>
    <div class="card"><h2>分辨率占比</h2><div id="dim-res"></div></div>
    <div class="card"><h2>地区占比</h2><div id="dim-geo"></div></div>
  </div>
</div>
<script>
const tokenInput = document.getElementById('token');
tokenInput.value = localStorage.getItem('stats_token') || '';
if (tokenInput.value) load();

function saveToken() {
  localStorage.setItem('stats_token', tokenInput.value);
  load();
}

async function api(path) {
  const r = await fetch(path + (path.includes('?') ? '&' : '?') +
    'token=' + encodeURIComponent(tokenInput.value));
  if (r.status === 401) throw new Error('口令错误');
  if (!r.ok) throw new Error('请求失败: ' + r.status);
  return r.json();
}

function trendChart(days) {
  const W = 720, H = 220, padL = 36, padR = 8, padT = 12, padB = 24;
  const innerW = W - padL - padR, innerH = H - padT - padB;
  const max = Math.max(...days.map(d => d.dau), 1);
  // y 轴刻度取整到好看的步长
  const step = Math.max(1, Math.ceil(max / 4));
  const yMax = step * 4;
  const bw = innerW / days.length;
  let s = '<svg class="trend-chart" viewBox="0 0 ' + W + ' ' + H + '">';
  // 横向网格线与 y 轴刻度
  for (let v = 0; v <= yMax; v += step) {
    const y = padT + innerH - v / yMax * innerH;
    s += '<line class="axis" x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '"/>' +
      '<text x="' + (padL - 4) + '" y="' + (y + 3) + '" text-anchor="end">' + v + '</text>';
  }
  days.forEach((d, i) => {
    const h = d.dau / yMax * innerH;
    const x = padL + i * bw + bw * 0.15;
    const y = padT + innerH - h;
    s += '<rect class="col" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) +
      '" width="' + (bw * 0.7).toFixed(1) + '" height="' + Math.max(h, 0).toFixed(1) + '">' +
      '<title>' + d.date + '：' + d.dau + '</title></rect>';
    // x 轴日期标签隔 5 天显示一个
    if (i % 5 === 0 || i === days.length - 1) {
      s += '<text x="' + (padL + i * bw + bw / 2).toFixed(1) + '" y="' + (H - 8) +
        '" text-anchor="middle">' + d.date.slice(5) + '</text>';
    }
  });
  return s + '</svg>';
}

async function load() {
  try {
    const [stats, trend] = await Promise.all([api('stats'), api('trend?days=30')]);
    document.getElementById('auth').style.display = 'none';
    document.getElementById('content').style.display = '';
    document.getElementById('dau').textContent = stats.dau;
    const trendEl = document.getElementById('trend');
    trendEl.innerHTML = trend.days.length ? trendChart(trend.days) : '暂无数据';
    const dims = { v: stats.versions, os: stats.os, res: stats.res, geo: stats.geo };
    for (const [dim, dist] of Object.entries(dims)) {
      const rows = Object.entries(dist || {}).sort((a, b) => b[1] - a[1]);
      const total = rows.reduce((s, r) => s + r[1], 0);
      const max = Math.max(...rows.map(r => r[1]), 1);
      const el = document.getElementById('dim-' + dim);
      el.innerHTML = rows.length ? rows.map(([k, n]) =>
        '<div class="bar-row"><span class="label">' + k + '</span>' +
        '<div class="bar" style="width:' + Math.round(n / max * 200) + 'px"></div>' +
        '<span class="num">' + n + '（' + (total ? (n / total * 100).toFixed(1) : 0) + '%）</span></div>'
      ).join('') : '暂无数据';
    }
  } catch (e) {
    document.getElementById('authErr').textContent = e.message;
  }
}
</script>
</body>
</html>`;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // 通过路由挂在子路径下时（如 nkas.megumiss.top/status/*），
    // 用 PATH_PREFIX 变量配置前缀，这里剥掉前缀再匹配路由
    let path = url.pathname;
    const prefix = env.PATH_PREFIX || '';
    if (prefix) {
      if (path === prefix) {
        return Response.redirect(new URL(`${prefix}/`, url).toString(), 302);
      }
      if (path.startsWith(`${prefix}/`)) {
        path = path.slice(prefix.length);
      }
    }

    if (path === '/' && request.method === 'GET') {
      return new Response(INDEX_HTML, {
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      });
    }

    if (path === '/report' && request.method === 'POST') {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: 'invalid json' }, 400);
      }
      if (!isValidId(body.id)) {
        return json({ error: 'invalid id' }, 400);
      }

      const day = today();
      const dedupKey = `d:${day}:${body.id}`;
      const seen = await env.STATS.get(dedupKey);
      if (seen !== null) {
        return json({ ok: true, dedup: true });
      }

      // 各维度取值：客户端字段缺失计 unknown，校验不过计 other；地区来自 CF 边缘节点
      const dims = {
        v: sanitizeVersion(body.version),
        os: sanitizeOs(body.os),
        res: sanitizeRes(body.res),
        geo: sanitizeCountry(request.cf && request.cf.country),
      };
      const countKeys = [`c:${day}`, ...DIMS.map((d) => `${d}:${day}:${dims[d]}`)];
      const counts = await Promise.all(countKeys.map((k) => env.STATS.get(k)));
      await Promise.all([
        env.STATS.put(dedupKey, '1', { expirationTtl: DEDUP_TTL_SECONDS }),
        ...countKeys.map((k, i) =>
          env.STATS.put(k, String(parseInt(counts[i] || '0', 10) + 1), {
            expirationTtl: COUNT_TTL_SECONDS,
          }),
        ),
      ]);
      return json({ ok: true });
    }

    if (path === '/stats' && request.method === 'GET') {
      if (!checkAuth(url, env)) {
        return json({ error: 'unauthorized' }, 401);
      }
      const date = url.searchParams.get('date') || today();
      if (!isValidDate(date)) {
        return json({ error: 'invalid date, expect YYYY-MM-DD' }, 400);
      }
      const dau = parseInt((await env.STATS.get(`c:${date}`)) || '0', 10);
      const [versions, os, res, geo] = await Promise.all(
        DIMS.map((d) => getDist(env, d, date)),
      );
      return json({ date, dau, versions, os, res, geo });
    }

    if (path === '/trend' && request.method === 'GET') {
      if (!checkAuth(url, env)) {
        return json({ error: 'unauthorized' }, 401);
      }
      let days = parseInt(url.searchParams.get('days') || '30', 10);
      if (!Number.isInteger(days) || days < 1 || days > 92) {
        days = 30;
      }
      const day = today();
      const keys = [];
      for (let i = days - 1; i >= 0; i--) {
        keys.push(dateOffset(day, i));
      }
      const counts = await Promise.all(keys.map((d) => env.STATS.get(`c:${d}`)));
      return json({
        days: keys.map((d, i) => ({ date: d, dau: parseInt(counts[i] || '0', 10) })),
      });
    }

    return json({ error: 'not found' }, 404);
  },
};
