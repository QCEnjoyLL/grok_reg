# Grok Register

基于 **Chromium + DrissionPage** 的 Grok 账号批量注册与 CPA 认证导出工具，提供 Docker 一键部署与 Web 管理后台。

![Version](https://img.shields.io/badge/version-v1.0.0-blue)
![Python](https://img.shields.io/badge/python-3.13-green)
![Docker](https://img.shields.io/badge/docker-ghcr.io-blue)

> ⚠️ 请仅用于学习与合规用途，遵守目标服务条款与当地法律法规。请自行保管 Token、代理与邮箱密钥，勿提交到公开仓库。

---

## ✨ 功能概览

- 🌐 **Web 后台**：任务启停、实时日志、账号/CPA 列表、必要配置表单
- 🧩 **多邮箱渠道**：MoeMail、Cloudflare Temp Email（Worker API）、CloudMail 等
- 🧵 **并发流水线**：注册线程 + CPA mint worker 队列，提升吞吐
- 🔐 **CPA 导出**：优先 SSO HTTP Device Flow；失败再回退浏览器路径
- ☁️ **CPAMC 上传**：自动/手动上传本地 `xai-*.json`，支持状态筛选与批量重传
- 🖥️ **noVNC**：容器内可观察 Chromium 页面（验证码/风控时排查）

---

## 📦 产物说明

| 路径 | 说明 |
|------|------|
| `accounts_cli.txt` | `email----password----sso` |
| `cpa_auths/xai-*.json` | CPA / OIDC 认证文件（与 SSO cookie **不是同一概念**） |
| `.upload_state.json` | 本地 CPAMC 上传状态账本（在 `cpa_auths` 目录下） |

> **SSO ≠ CPA**。有 SSO 时程序会优先走纯 HTTP mint；没有可用 free-build 探测结果时，仍会保留已写出的 CPA 文件（soft-fail）。

---

## 🚀 快速开始（Docker）

镜像：`ghcr.io/qcenjoyll/grok_reg`

```bash
# 数据目录（按需修改）
sudo mkdir -p /opt/grok_reg/data

# 拉取正式版
docker pull ghcr.io/qcenjoyll/grok_reg:v1.0.0

docker rm -f grok-reg 2>/dev/null || true
docker run -d \
  --name grok-reg \
  --restart unless-stopped \
  --shm-size=1g \
  --cap-add=SYS_ADMIN \
  --security-opt seccomp=unconfined \
  -e WEB_TOKEN=请改成强随机串 \
  -e VNC_PASSWORD=请改成自己的密码 \
  -p 8080:8080 -p 6080:6080 \
  -v /opt/grok_reg/data:/data \
  ghcr.io/qcenjoyll/grok_reg:v1.0.0 \
  web
```

| 访问 | 地址 |
|------|------|
| Web 后台 | `http://<服务器IP>:8080` |
| noVNC | `http://<服务器IP>:6080/vnc.html?autoconnect=1&resize=scale` |

登录后台使用环境变量 `WEB_TOKEN`。首次进入后在 **必要配置** 中填写邮箱渠道、代理与 CPA 相关项，保存后再启动注册任务。

### docker compose

```bash
cp .env.docker.example .env
# 编辑 .env：WEB_TOKEN / VNC_PASSWORD / 端口
docker compose pull   # 或 build
docker compose up -d
```

---

## ⚙️ 后台配置要点

### 邮箱渠道

| provider | 说明 |
|----------|------|
| `moemail` | MoeMail 兼容 API（`X-API-Key`） |
| `cloudflare` | [cloudflare_temp_email](https://github.com/dreamhunter2333/cloudflare_temp_email) 的 **Worker API** |
| `cloudmail` | 自建 CloudMail catch-all |
| `duckmail` / `yyds` | 其它 API 渠道 |

**Cloudflare 注意**

- `cloudflare_api_base` 必须填 **Worker** 地址（例如 `https://xxx.workers.dev`）
- 不要填前端 Pages 域名（常见现象：`POST` 返回 405）
- `defaultDomains` 可空（自动拉取）或填多个域名随机抽取

### CPA / CPAMC

| 配置项 | 建议 |
|--------|------|
| `cpa_export_enabled` | 需要 CPA 文件时开启 |
| `cpa_base_url` | free-build 探测接口（OpenAI 兼容 `/v1`） |
| `cpa_management_base` | CPA 管理端地址（用于上传 auth 文件） |
| `cpa_management_key` | 管理端 Bearer 密码 |
| `cpa_management_upload_enabled` | 是否 mint 成功后自动上传 |

后台 **CPA 文件** 页支持：筛选已上传/未上传、单条上传/重传、批量上传、标记已上传。

### 代理

- `proxy`：注册浏览器 / 邮箱 HTTP 默认代理
- `cpa_proxy`：CPA mint 专用；可设 `direct` 强制直连

---

## 🧰 CLI

```bash
# 依赖
uv sync
cp config.example.json config.json   # 填入你的配置，勿提交密钥

# 追加注册 1 个（调试）
uv run python -u register_cli.py --extra 1 --threads 1

# 批量：注册线程 2 + mint 2
uv run python -u register_cli.py --extra 50 --threads 2 --mint-workers 2 --fast

# 已有账号补 mint
uv run python -u scripts/backfill_cpa_xai_from_accounts.py --limit 10
```

| 参数 | 含义 |
|------|------|
| `--extra N` | 额外新注册 N 个 |
| `--count N` | 账号总数目标（`0`=不限） |
| `--threads N` | 注册并发 1–10 |
| `--mint-workers N` | mint 并发：`-1` 自动，`0` 内联，`1–10` 固定 |
| `--fast` / `--no-fast` | 压缩等待（默认开） |

流水线：`注册线程 → mint 队列 → mint workers`。有 SSO 时 mint 优先纯 HTTP。

---

## 📁 数据目录（Docker）

挂载 `/data` 后主要持久化：

```
/data/
  config.json
  accounts_cli.txt
  cpa_auths/
  logs/ cookies/ screenshots/   # 运行期
```

---

## 🏷️ 版本与镜像标签

| 标签 | 说明 |
|------|------|
| `v1.0.0` | 本发布正式版 |
| `latest` | 默认跟随最新构建 |
| `sha-*` | 提交级追溯 |

**发布约定（后续更新）**

- 小修复 / 文案 / 样式：补丁号 `v1.0.x`
- 功能增强 / 多模块改动：次版本 `v1.x.0`
- 破坏性变更 / 重大架构：主版本 `vX.0.0`

推送符合 `v*` 的 Git 标签会触发 GHCR 镜像构建。

```bash
docker pull ghcr.io/qcenjoyll/grok_reg:v1.0.0
# 或
docker pull ghcr.io/qcenjoyll/grok_reg:latest
```

---

## 🛠️ 运维

```bash
docker logs -f grok-reg
docker exec -it grok-reg bash

# 容器内直接跑一轮注册
docker run --rm -it --shm-size=1g --cap-add=SYS_ADMIN \
  --security-opt seccomp=unconfined \
  -v /opt/grok_reg/data:/data \
  ghcr.io/qcenjoyll/grok_reg:v1.0.0 \
  register --extra 1 --threads 1
```

常见问题：

1. **浏览器连不上**：确认 `--shm-size`、`SYS_ADMIN`、`seccomp=unconfined`
2. **Cloudflare 邮箱 405**：检查是否误填 Pages 前端地址
3. **CPA 探测失败但文件已生成**：属 soft-fail，可手动上传 CPAMC
4. **外网暴露**：务必设置强 `WEB_TOKEN`，不要使用文档示例密码

---

## 🧭 项目结构（简）

```
grok_reg/
  grok_register_ttk.py   # 注册核心
  register_cli.py        # CLI / 多线程入口
  cpa_export.py          # CPA 导出与 CPAMC 上传
  cpa_xai/               # mint / probe / SSO HTTP
  web/                   # FastAPI 后台
  docker/                # 入口与显示服务
  scripts/               # backfill 等工具
```

---

## 📄 许可与声明

本项目按仓库内许可条款使用。作者不对滥用、封号或数据丢失承担责任。请自行做好备份与密钥管理。

---

**Grok Register v1.0.0** · 祝部署顺利 🎉
