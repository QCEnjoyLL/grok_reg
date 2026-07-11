# Docker 部署

## 一键启动

```bash
sudo mkdir -p /media/docker/grok_reg
docker pull ghcr.io/qcenjoyll/grok_reg:latest
docker rm -f grok-reg 2>/dev/null || true
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

| 端口 | 用途 |
|------|------|
| 8089 | Web 后台 |
| 6089 | noVNC (看 Chromium) |

## 后台

1. 打开 `http://IP:8089` ， Token `change-me`
2. 系统设置: noVNC URL = `http://IP:6089/vnc.html?autoconnect=1&resize=scale`
3. 开始前填这些:
   - email_provider = `moemail`
   - moemail_api_key = 你的 Key
   - proxy = 代理
4. 开始注册

## 命令

```bash
docker logs -f grok-reg
docker exec -it grok-reg bash
docker run --rm -it --shm-size=1g --cap-add=SYS_ADMIN \
  --security-opt seccomp=unconfined \
  -e DISPLAY=:99 -e DATA_DIR=/data \
  -v /media/docker/grok_reg:/data \
  ghcr.io/qcenjoyll/grok_reg:latest \
  register --extra 1 --threads 1
```

## GHCR

仓库 Actions 自动构建: `ghcr.io/qcenjoyll/grok_reg:latest`

私有包需 `docker login ghcr.io`
