FROM python:3.12-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    x11-utils \
    fluxbox \
    novnc \
    curl \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# 复制项目文件
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

# 安装 Playwright 浏览器（patchright 优先，回退官方 playwright）
RUN uv run playwright install chromium && uv run playwright install-deps chromium
RUN uv run patchright install chromium || echo "patchright 未安装，跳过"

# 复制源码
COPY src/ src/
COPY web/ web/

# 数据卷（.env、accounts.json、auths/、state.json、screenshots/）
VOLUME ["/app/data"]

# 启动时将数据目录软链到工作目录
RUN mkdir -p /app/data
ENV DISPLAY=:99
ENV ENABLE_NOVNC=true
ENV VNC_HOST=127.0.0.1
ENV VNC_PORT=5900
ENV NOVNC_WEB_DIR=/usr/share/novnc

EXPOSE 8787

# 启动脚本
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["api"]
