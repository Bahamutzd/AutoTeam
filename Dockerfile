FROM python:3.12-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    x11-utils \
    fluxbox \
    novnc \
    curl \
    wget \
    gnupg \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 安装 Google Chrome（patchright channel="chrome" 需要真实 Chrome）
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
       > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# 复制项目文件
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

# 安装浏览器依赖（patchright 优先，回退官方 playwright）
# patchright 使用 channel="chrome" 需要真实 Chrome 的系统依赖
RUN uv run playwright install chromium && uv run playwright install-deps chromium
RUN uv run patchright install chrome || echo "patchright 未安装，跳过"

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
