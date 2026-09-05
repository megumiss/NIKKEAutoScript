# NKAS License Worker

这是独立的 GitHub Star 授权 Worker，不复用 `nkas-dau` Worker，也不需要 D1。

Cloudflare 控制台中创建新 Worker，例如：

```text
nkas-license
```

然后将 `worker.js` 的完整内容粘贴到 Worker 的编辑器并部署。

## Variables

普通变量：

- `GITHUB_CLIENT_ID`
- `GITHUB_OAUTH_CALLBACK_URL`，例如 `https://nkas-license.example.workers.dev/oauth/callback`
- `APP_CALLBACK_URI`，固定为 `nkas://auth/callback`

加密 Secret：

- `GITHUB_CLIENT_SECRET`
- `LICENSE_PRIVATE_KEY_PEM`

`LICENSE_PRIVATE_KEY_PEM` 必须使用与 Android App 内置公钥匹配的 PKCS#8 RSA 私钥。

## Endpoints

- `GET /`：服务状态。
- `GET /oauth/start?state=<state>`：跳转 GitHub OAuth。
- `GET /oauth/callback`：检查账号是否 Star 仓库，成功后跳回 App 并携带一年有效授权密钥。
