# Grok Register - server image with Chromium + Xvfb + noVNC + web dashboard
FROM python:3.13-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    APP_HOME=/app \
    DATA_DIR=/data \
    VNC_PORT=5900 \
    NOVNC_PORT=6080 \
    WEB_PORT=8080 \
    VNC_PASSWORD=grokreg \
    RESOLUTION=1920x1080x24

WORKDIR /app

# System packages: Chromium, fonts, virtual display, VNC, noVNC deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        ca-certificates \
        curl \
        wget \
        gnupg \
        procps \
        xauth \
        xvfb \
        x11vnc \
        x11-utils \
        fluxbox \
        novnc \
        websockify \
        supervisor \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/chromium /usr/bin/google-chrome \
    && ln -sf /usr/bin/chromium /usr/bin/chrome \
    && mkdir -p /data /var/log/supervisor /var/run

# Python deps
COPY pyproject.toml README.md ./
COPY cpa_xai ./cpa_xai
COPY scripts ./scripts
COPY turnstilePatch ./turnstilePatch
COPY docker ./docker
COPY web ./web
COPY *.py ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "DrissionPage>=4.1" \
        "curl_cffi>=0.7" \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.32" \
        "jinja2>=3.1" \
        "python-multipart>=0.0.12" \
        "pydantic>=2.0" \
        "websockets>=13.0"

# Default config template (runtime mounts override)
COPY config.example.json /app/config.example.json
RUN if [ ! -f /app/config.json ]; then cp /app/config.example.json /app/config.json; fi \
    && sed -i "s/\r$//" /app/docker/entrypoint.sh /app/docker/start-display.sh \
    && chmod +x /app/docker/entrypoint.sh /app/docker/start-display.sh \
    && mkdir -p /data/cpa_auths /data/logs /data/cookies

VOLUME ["/data"]
EXPOSE 8080 5900 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/entrypoint.sh"]
CMD ["web"]
