# Docker 部署指南

在服务器上用 **Chromium + Xvfb + noVNC + Web 控制台** 跑 Grok 注册机。

## 架构

```
浏览器访问
  ├─ :8080  Web 控制台（启停注册 / 日志 / 账号 / 配置）
  └─ :6080  noVNC（实时看 Chromium，过 CF / 点授权）
容器内
  Xvfb :99 → fluxbox → Chromium(+turnstilePatch)
  register_cli / backfill
  数据卷 /data → config、accounts_cli、cpa_auths、logs
```

> OIDC / Turnstile 推荐 **有头**（容器内有头 = Xvfb）。`cpa_headless=false`。

---

## 1. 准备文件

```bash
# 克隆仓库后
cd grok_reg

mkdir -p data/cpa_auths data/logs
cp config.example.json data/config.json
# 编辑 data/config.json：邮箱 API、proxy、CPA 等

cp .env.docker.example .env
# 编辑 .env：WEB_TOKEN、VNC_PASSWORD、镜像名 OWNER
```

**`data/config.json` 必改：**

| 字段 | 说明 |
|------|------|
| 邮箱相关 | `email_provider` + Cloudflare/CloudMail 等 |
| `proxy` | 能访问 xAI 的代理 |
| `cpa_export_enabled` | `true` 则铸 OIDC |
| `cpa_auth_dir` | 建议 `./cpa_auths`（会落到 `/data/cpa_auths`） |
| `cpa_headless` | **`false`** |
| `cpa_base_url` | `https://cli-chat-proxy.grok.com/v1` |

---

## 2. 本地构建运行

```bash
docker compose build
docker compose up -d

# 控制台
open http://SERVER_IP:8080
# 看浏览器（VNC 密码 = VNC_PASSWORD）
open http://SERVER_IP:6080/vnc.html?autoconnect=1&resize=scale
```

一键注册（不经 Web）：

```bash
docker compose run --rm grok-reg register --extra 1 --threads 1
docker compose run --rm grok-reg backfill --limit 1 --probe --timeout 300
```

进入 shell：

```bash
docker compose exec grok-reg bash
```

---

## 3. GitHub Actions 自动打包

已提供：`.github/workflows/docker-image.yml`

### 若 `grok_reg` 是独立仓库

1. 推到 GitHub（默认分支 `main` 或 `master`）
2. 仓库 **Settings → Actions → General**：允许 GitHub Actions 写 packages
3. push 后自动构建并推送到：

```text
ghcr.io/<owner>/<repo>:latest
ghcr.io/<owner>/<repo>:sha-xxxx
```

4. 服务器拉镜像：

```bash
# GitHub → Packages 里对该 package 授权（public 或 PAT login）
echo $GHCR_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# .env
GROK_REG_IMAGE=ghcr.io/yourname/grok_reg:latest

docker compose pull
docker compose up -d
```

### 若在 monorepo 根目录

把 workflow 挪到 monorepo 的 `.github/workflows/`，并改：

```yaml
defaults:
  run:
    working-directory: grok_reg
# build-push-action:
#   context: ./grok_reg
#   file: ./grok_reg/Dockerfile
```

或给 `grok_reg` 单独建仓库（推荐，镜像上下文更干净）。

### 手动触发

GitHub → Actions → **Build and push Docker image** → Run workflow。

打 tag 发版：

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 4. 环境变量

| 变量 | 默认 | 含义 |
|------|------|------|
| `WEB_TOKEN` | 空 | 控制台 API 鉴权；公网务必设置 |
| `VNC_PASSWORD` | `grokreg` | noVNC / VNC 密码 |
| `WEB_HOST_PORT` | `8080` | 控制台宿主端口 |
| `NOVNC_HOST_PORT` | `6080` | noVNC 宿主端口 |
| `VNC_HOST_PORT` | `5900` | 原生 VNC |
| `RESOLUTION` | `1920x1080x24` | 虚拟屏分辨率 |
| `GROK_REG_IMAGE` | compose 内 | 使用的镜像名 |

---

## 5. Web 控制台功能

- 启动 **注册**（`--extra` / `--threads` / mint workers）
- 启动 **Backfill** OIDC
- **停止**当前任务
- 实时日志（WebSocket）
- 账号列表 / CPA 文件列表
- 在线改 `config.json`（密钥脱敏；未改的 `***` 不会覆盖）
- 跳转 **noVNC** 看真实浏览器

鉴权：设置 `WEB_TOKEN` 后，打开控制台点「设置 Token」，或请求头：

```http
Authorization: Bearer <token>
X-Web-Token: <token>
```

---

## 6. 资源与网络建议

| 项 | 建议 |
|----|------|
| 内存 | ≥ 2–4GB（Chromium） |
| `shm_size` | compose 已设 `1gb` |
| CPU | 注册线程 1–2 起步 |
| 代理 | 写在 `data/config.json` 的 `proxy`；容器访问的是**服务器能达**的代理地址（不要写仅 Windows 本机可达的错 IP） |
| 安全 | 公网请设 `WEB_TOKEN` + 改 `VNC_PASSWORD`；或只绑定内网 / 反代鉴权 |

---

## 7. 常见问题

| 现象 | 处理 |
|------|------|
| noVNC 黑屏 | 等 2–3 秒；查 `data/logs/xvfb.log`；确认 6080 端口 |
| Cloudflare 拦截 | 用 noVNC 观察；关 headless；换代理；`threads=1` |
| 找不到 chromium | 镜像内为 `/usr/bin/chromium`（已兼容） |
| 配置不生效 | 确认挂载的是 `data/config.json`，改完重启任务（不必重启容器） |
| GHCR 拉不下 | `docker login ghcr.io`；package 设 public 或授权 |

---

## 8. 目录（运行时）

```text
data/
  config.json          # 你的实配
  accounts_cli.txt     # 账本
  cpa_auths/xai-*.json
  logs/                # xvfb / vnc / 可选
```
