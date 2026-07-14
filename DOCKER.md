# Docker 部署说明

镜像：`ghcr.io/qcenjoyll/grok_reg`

推荐标签：`v1.0.0`（正式版）或 `latest`。

## 运行

```bash
sudo mkdir -p /opt/grok_reg/data

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

| 主机端口 | 服务 |
|----------|------|
| 8080 | Web 后台 |
| 6080 | noVNC |

## 首次配置

1. 打开 `http://<IP>:8080`，使用 `WEB_TOKEN` 登录
2. 系统设置中填写 noVNC 公网地址（若需远程看浏览器）
3. 必要配置中填写邮箱渠道、代理、CPA 选项并保存
4. 启动注册任务

## Compose

```bash
cp .env.docker.example .env
# 修改 WEB_TOKEN / VNC_PASSWORD / 端口与镜像标签
docker compose up -d
```

## 排障

```bash
docker logs -f grok-reg
docker exec -it grok-reg bash
```

更多说明见 [README.md](./README.md)。
