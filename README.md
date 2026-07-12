# Grok Register (grok_reg)

基于 **Chromium + DrissionPage + turnstilePatch** 的免费 Grok 账号注册机，支持 Docker 部署与 Web 后台。

## 产物

| 文件 | 说明 |
|------|------|
| `accounts_cli.txt` | `email----password----sso` |
| `cpa_auths/xai-*.json` | 免费 Grok 4.5 用 OIDC / CPA 认证 |

> **SSO ≠ CPA/OIDC**。`accounts_cli.txt` 里的 SSO 只是登录态 cookie；免费 Grok 4.5 还需要再生成 `cpa_auths/xai-*.json`（有 SSO 时程序会优先走纯 HTTP Device Flow，无需再过登录页验证）。

## 最快上手（Docker）

```bash
# 1) data dir
sudo mkdir -p /media/docker/grok_reg
# 2) pull & run
docker pull ghcr.io/qcenjoyll/grok_reg:latest
docker rm -f grok-reg 2>/dev/null
docker run -d \
  --name grok-reg \
  --restart unless-stopped \
  --shm-size=1g \
  --cap-add=SYS_ADMIN \
  --security-opt seccomp=unconfined \
  -e WEB_TOKEN=change-me \
  -e VNC_PASSWORD=grokreg \
  -p 8089:8080 -p 6089:6080 \
  -v /media/docker/grok_reg:/data \
  ghcr.io/qcenjoyll/grok_reg:latest \
  web
```

- 后台 `http://SERVER:8089`  Token: `change-me`
- noVNC `http://SERVER:6089/vnc.html?autoconnect=1&resize=scale`  密码: `grokreg`

### 后台必填（MoeMail / mail.nloln.cn）

1. 邮箱渠道: `moemail`
2. `moemail_api_key`: 你的 X-API-Key
3. `moemail_api_base`: `https://mail.nloln.cn` (默认)
4. `defaultDomains`: 可选，例如 `moemail.app`
5. `proxy`: 服务器可访问的代理
6. 系统设置 填 noVNC 公网 URL (例 `http://IP:6089/vnc.html?autoconnect=1&resize=scale`)
7. 点?开始注册?

高级选项 / 完整 JSON / Backfill 默认折叠。

## 邮箱渠道

| provider | 说明 |
|----------|------|
| **moemail** | mail.nloln.cn / MoeMail API (`X-API-Key`) |
| cloudflare | cloudflare_temp_email Worker |
| cloudmail | 自建 CloudMail catch-all |
| duckmail / yyds | 其它 API |

### MoeMail API 映射

| 注册机 | API |
|--------|-----|
| 创建邮箱 | `POST /api/emails/generate` |
| 收信 | `GET /api/emails/{id}` + `.../{messageId}` |
| 域名 | `GET /api/config` 或 `defaultDomains` |

## 本地开发

```bash
# Python 3.13 + uv
uv sync
# config
cp config.example.json config.json
# CLI
uv run python register_cli.py --extra 1 --threads 1
# GUI
uv run python grok_register_ttk.py
```

## CLI

```bash
uv run python register_cli.py --extra N --threads 1
uv run python -u scripts/backfill_cpa_xai_from_accounts.py --limit 1 --probe
```

| 参数 | 含义 |
|------|------|
| `--extra N` | 再新注册 N 个 |
| `--count N` | 账本总数目标 |
| `--threads N` | 并发 1-10 |

## Docker / GHCR

- Image: `ghcr.io/qcenjoyll/grok_reg:latest`
- Compose: `docker-compose.yml`
- Details: [DOCKER.md](./DOCKER.md)
- CI: `.github/workflows/docker-image.yml` auto build on push

```bash
docker compose pull && docker compose up -d
```

## 数据目录 `/data`

```text
/data/config.json
/data/ui_settings.json      # token / noVNC URL
/data/accounts_cli.txt
/data/cpa_auths/xai-*.json
/data/logs/
```

## 注意

- 公网请修改 `WEB_TOKEN` / `VNC_PASSWORD`
- `cpa_headless` 建议 `false` (容器内 Xvfb 有头)
- 不要把 `config.json` / 账号 / OIDC 提交 git
- Chromium 需要 `--shm-size=1g`

## 目录结构

```text
grok_reg/
  register_cli.py
  grok_register_ttk.py
  cpa_export.py / cpa_xai/
  web/                 # dashboard
  docker/              # entrypoint + Xvfb/noVNC
  Dockerfile
  scripts/
  turnstilePatch/
```

## CPA 生成（SSO 优先）

账号格式：`email----password----sso`

1. 点击后台「为缺失账号生成 CPA」或注册成功后自动 mint
2. **有 SSO**：优先 `curl_cffi` 纯 HTTP Device Flow（不过登录页 Turnstile）
3. **SSO 失效/没有**：回退浏览器登录（可能需 noVNC 手点 Cloudflare）
4. 成功写入 `/data/cpa_auths/xai-<email>.json`

