/**
 * NKAS DAU 统计 Worker
 *
 * 接口：
 *   POST /report  Body: {"id": "<匿名ID>", "version": "<客户端版本, 可选>"}
 *                 按天（UTC+8）对匿名ID去重计数。
 *   GET  /stats?date=YYYY-MM-DD&token=<STATS_TOKEN>
 *                 返回 {"date": "...", "dau": 123}；date 缺省为今天。
 */

const DAY_MS = 24 * 60 * 60 * 1000;
const TZ_OFFSET_MS = 8 * 60 * 60 * 1000; // UTC+8
// 去重 key 保留 8 天，统计计数 key 保留 92 天
const DEDUP_TTL_SECONDS = 8 * 24 * 3600;
const COUNT_TTL_SECONDS = 92 * 24 * 3600;

function today() {
  return new Date(Date.now() + TZ_OFFSET_MS).toISOString().slice(0, 10);
}

function isValidDate(s) {
  return /^\d{4}-\d{2}-\d{2}$/.test(s);
}

function isValidId(id) {
  return typeof id === 'string' && /^[0-9a-f-]{8,64}$/i.test(id);
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === '/report' && request.method === 'POST') {
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

      const countKey = `c:${day}`;
      const versionKey = `v:${day}:${body.version || 'unknown'}`;
      const count = parseInt((await env.STATS.get(countKey)) || '0', 10);
      const vcount = parseInt((await env.STATS.get(versionKey)) || '0', 10);
      await Promise.all([
        env.STATS.put(dedupKey, '1', { expirationTtl: DEDUP_TTL_SECONDS }),
        env.STATS.put(countKey, String(count + 1), { expirationTtl: COUNT_TTL_SECONDS }),
        env.STATS.put(versionKey, String(vcount + 1), { expirationTtl: COUNT_TTL_SECONDS }),
      ]);
      return json({ ok: true });
    }

    if (url.pathname === '/stats' && request.method === 'GET') {
      if (url.searchParams.get('token') !== env.STATS_TOKEN) {
        return json({ error: 'unauthorized' }, 401);
      }
      const date = url.searchParams.get('date') || today();
      if (!isValidDate(date)) {
        return json({ error: 'invalid date, expect YYYY-MM-DD' }, 400);
      }
      const dau = parseInt((await env.STATS.get(`c:${date}`)) || '0', 10);
      const versions = {};
      const list = await env.STATS.list({ prefix: `v:${date}:` });
      for (const key of list.keys) {
        versions[key.name.slice(`v:${date}:`.length)] = parseInt(
          (await env.STATS.get(key.name)) || '0',
          10,
        );
      }
      return json({ date, dau, versions });
    }

    return json({ error: 'not found' }, 404);
  },
};
