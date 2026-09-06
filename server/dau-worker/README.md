# NKAS DAU 统计 Worker

基于 Cloudflare Workers + D1 的每日活跃用户统计接口，免费额度（每天 10 万次写入）足够小规模使用。

## 部署步骤

前置：安装 Node.js 20+，注册 Cloudflare 账号（https://dash.cloudflare.com）。

```bash
cd server/dau-worker

# 1. 登录 Cloudflare（会打开浏览器授权）
npx wrangler login

# 2. 创建 D1 数据库，记下输出中的 database_id
npx wrangler d1 create nkas-dau

# 3. 编辑 wrangler.toml：
#    - 把 D1 的 database_id 填入 [[d1_databases]] 的 database_id 字段
#    - 把 STATS_TOKEN 改成你自己的随机字符串

# 4. 建表
npx wrangler d1 execute nkas-dau --remote --file=schema.sql

# 5. 部署
npx wrangler deploy
```

部署成功后得到地址，形如 `https://nkas-dau.<你的子域>.workers.dev`。

## 接口

### 上报（客户端调用）

```bash
curl -X POST https://nkas-dau.<你的子域>.workers.dev/report \
  -H "Content-Type: application/json" \
  -d '{"id": "550e8400-e29b-41d4-a716-446655440000", "version": "abc1234", "os": "Windows 11", "res": "1920x1080"}'
```

同一天（UTC+8）同一 `id` 重复上报只计一次。

字段说明：

- `id`：必填，匿名 ID（uuid，hex 和连字符，8~64 位）。
- `version`：可选，客户端版本，`[\w.-]{1,32}`，不合法归 `other`，缺省归 `unknown`。
- `os`：可选，仅接受 `Windows 10` / `Windows 11` / `Linux` / `macOS` / `Android`，其它值归 `other`，缺省归 `unknown`。Android 客户端应上报 `Android`。
- `res`：可选，分辨率如 `1920x1080`（分隔符 `x` `*` `×` 均可，存储统一为 `x`），不合法归 `other`，缺省归 `unknown`。
- 地区无需客户端上报，由服务端从 `request.cf.country` 取两位国家码。

### 查询（仅作者自用，需要口令）

```bash
# 查今天
curl "https://nkas-dau.<你的子域>.workers.dev/stats?token=<STATS_TOKEN>"

# 查指定日期
curl "https://nkas-dau.<你的子域>.workers.dev/stats?date=2026-08-06&token=<STATS_TOKEN>"
```

返回示例：

```json
{
  "date": "2026-08-06",
  "dau": 123,
  "versions": {"abc1234": 100, "unknown": 23},
  "os": {"Windows 11": 80, "Windows 10": 40, "other": 3},
  "res": {"1920x1080": 90, "2560x1440": 33},
  "geo": {"CN": 100, "US": 23}
}
```

### 趋势查询（需要口令）

```bash
# 最近 30 天每日 DAU（days 范围 1~92）
curl "https://nkas-dau.<你的子域>.workers.dev/trend?days=30&token=<STATS_TOKEN>"
```

### 展示页面

直接用浏览器打开 Worker 地址根路径（如 `https://nkas-dau.<你的子域>.workers.dev/`），
输入 `STATS_TOKEN` 口令即可查看今日 DAU、最近 30 天用户趋势，以及版本/系统/分辨率/地区占比。
口令保存在浏览器 localStorage。

## 绑定自有域名

前提：域名的 DNS 已托管在 Cloudflare（控制台添加站点并把 NS 改到 CF，免费版即可）。

### 方式一：独立子域名（自定义域，推荐）

Cloudflare 控制台 → Workers 和 Pages → 进入 `nkas-dau` → **设置** → **域和路由** → **添加自定义域**，
填一个子域名（如 `stats.example.com`），证书由 CF 自动签发。绑定后：

- 客户端上报地址：`https://stats.example.com/report`
- 展示页面：`https://stats.example.com/`

### 方式二：挂在现有站点的子路径下（路由）

例如挂到 `https://nkas.megumiss.top/status/`：

1. 确认 `nkas.megumiss.top` 这条 DNS 记录开启了 CF 代理（橙色云朵）。
2. 给 Worker 添加文本变量 `PATH_PREFIX`，值为 `/status`（与代码中的路径前缀剥离逻辑对应）。
3. Worker → **设置** → **域和路由** → **添加路由**，填 `nkas.megumiss.top/status/*`，区域选 `megumiss.top`。
4. 为兼容不带尾斜杠的访问，再加一条路由 `nkas.megumiss.top/status`（Worker 会 302 跳转到 `/status/`）。

绑定后：

- 客户端上报地址：`https://nkas.megumiss.top/status/report`
- 展示页面：`https://nkas.megumiss.top/status/`

只有匹配 `/status/*` 的请求会进入 Worker，站点其它路径不受影响。

## 说明

- 日期按 UTC+8（北京时间）划分。
- 数据按上报明细存储（每天每个匿名 ID 一行），保留最近 92 天，过期数据在上报时自动清理；更早的历史数据需要定期导出（也可以之后再加一个归档接口）。
- 国内访问 `*.workers.dev` 偶尔不稳定，正式使用建议绑定自有域名。
