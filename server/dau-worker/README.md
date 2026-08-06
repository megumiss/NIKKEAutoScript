# NKAS DAU 统计 Worker

基于 Cloudflare Workers + KV 的每日活跃用户统计接口，免费额度足够小规模使用。

## 部署步骤

前置：安装 Node.js 20+，注册 Cloudflare 账号（https://dash.cloudflare.com）。

```bash
cd server/dau-worker

# 1. 登录 Cloudflare（会打开浏览器授权）
npx wrangler login

# 2. 创建 KV 命名空间，记下输出中的 id
npx wrangler kv namespace create STATS

# 3. 编辑 wrangler.toml：
#    - 把 KV 的 id 填入 [[kv_namespaces]] 的 id 字段
#    - 把 STATS_TOKEN 改成你自己的随机字符串

# 4. 部署
npx wrangler deploy
```

部署成功后得到地址，形如 `https://nkas-dau.<你的子域>.workers.dev`。

## 接口

### 上报（客户端调用）

```bash
curl -X POST https://nkas-dau.<你的子域>.workers.dev/report \
  -H "Content-Type: application/json" \
  -d '{"id": "550e8400-e29b-41d4-a716-446655440000", "version": "abc1234"}'
```

同一天（UTC+8）同一 `id` 重复上报只计一次。

### 查询（仅作者自用，需要口令）

```bash
# 查今天
curl "https://nkas-dau.<你的子域>.workers.dev/stats?token=<STATS_TOKEN>"

# 查指定日期
curl "https://nkas-dau.<你的子域>.workers.dev/stats?date=2026-08-06&token=<STATS_TOKEN>"
```

返回示例：

```json
{"date": "2026-08-06", "dau": 123, "versions": {"abc1234": 100, "unknown": 23}}
```

## 说明

- 日期按 UTC+8（北京时间）划分。
- 去重数据保留 8 天，每日计数保留 92 天；更早的历史数据需要定期导出（也可以之后再加一个归档接口）。
- 国内访问 `*.workers.dev` 偶尔不稳定，正式使用建议在 Cloudflare 控制台绑定自有域名（Workers → 触发器 → 自定义域）。
