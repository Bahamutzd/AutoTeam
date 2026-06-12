"""配置文件 - 从 .env 文件或环境变量加载"""

import logging
import os
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from autoteam.textio import parse_env_line, parse_env_value, read_text

# 项目根目录（pyproject.toml 所在位置）
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 加载 .env 文件（从项目根目录）
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in read_text(_env_file).splitlines():
        parsed = parse_env_line(line)
        if parsed:
            key, value = parsed
            os.environ.setdefault(key, value)


def _get_int_env(name: str, default: int) -> int:
    return int(parse_env_value(os.environ.get(name, str(default))))


def _get_float_env(name: str, default: float) -> float:
    return float(parse_env_value(os.environ.get(name, str(default))))


def _get_bool_env(name: str, default: bool) -> bool:
    raw = parse_env_value(os.environ.get(name, ""))
    if not raw:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _get_str_env(name: str, default: str = "") -> str:
    value = parse_env_value(os.environ.get(name, default))
    return str(value).strip()


def _normalize_sub2api_ws_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"off", "ctx_pool", "passthrough"}:
        return mode
    return "off"


def _normalize_chatgpt_api_transport(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"auto", "playwright", "curl_cffi"}:
        return mode
    return "auto"


# CloudMail 配置
CLOUDMAIL_BASE_URL = os.environ.get("CLOUDMAIL_BASE_URL", "")
CLOUDMAIL_EMAIL = os.environ.get("CLOUDMAIL_EMAIL", "")
CLOUDMAIL_PASSWORD = os.environ.get("CLOUDMAIL_PASSWORD", "")
CLOUDMAIL_DOMAIN = os.environ.get("CLOUDMAIL_DOMAIN", "")

# 邮箱提供者配置
MAIL_PROVIDER = os.environ.get("MAIL_PROVIDER", "cloudmail").strip().lower() or "cloudmail"

# Cloudflare Temp Email 配置
CF_TEMP_EMAIL_BASE_URL = os.environ.get("CF_TEMP_EMAIL_BASE_URL", "")
CF_TEMP_EMAIL_ADMIN_PASSWORD = os.environ.get("CF_TEMP_EMAIL_ADMIN_PASSWORD", "")
CF_TEMP_EMAIL_DOMAIN = os.environ.get("CF_TEMP_EMAIL_DOMAIN", "")

# ChatGPT Team 配置
CHATGPT_ACCOUNT_ID = os.environ.get("CHATGPT_ACCOUNT_ID", "")

# CPA (CLIProxyAPI) 配置
CPA_URL = os.environ.get("CPA_URL", "")
CPA_KEY = os.environ.get("CPA_KEY", "")

# Sub2API 配置
SUB2API_URL = os.environ.get("SUB2API_URL", "")
SUB2API_EMAIL = os.environ.get("SUB2API_EMAIL", "")
SUB2API_PASSWORD = os.environ.get("SUB2API_PASSWORD", "")
SUB2API_GROUP = os.environ.get("SUB2API_GROUP", "")
SUB2API_PROXY = _get_str_env("SUB2API_PROXY", "")
SUB2API_CONCURRENCY = _get_int_env("SUB2API_CONCURRENCY", 10)
SUB2API_PRIORITY = _get_int_env("SUB2API_PRIORITY", 1)
SUB2API_RATE_MULTIPLIER = _get_float_env("SUB2API_RATE_MULTIPLIER", 1)
SUB2API_AUTO_PAUSE_ON_EXPIRED = _get_bool_env("SUB2API_AUTO_PAUSE_ON_EXPIRED", True)
SUB2API_MODEL_WHITELIST = _get_str_env("SUB2API_MODEL_WHITELIST", "")
SUB2API_OPENAI_WS_MODE = _normalize_sub2api_ws_mode(_get_str_env("SUB2API_OPENAI_WS_MODE", "off"))
SUB2API_OPENAI_PASSTHROUGH = _get_bool_env("SUB2API_OPENAI_PASSTHROUGH", False)
SUB2API_OVERWRITE_ACCOUNT_SETTINGS = _get_bool_env("SUB2API_OVERWRITE_ACCOUNT_SETTINGS", False)

# 同步时保留远端指定 plan 类型的认证文件（逗号分隔），如 "team,plus" 表示保留 team 和 plus 文件
SYNC_KEEP_PLANS = _get_str_env("SYNC_KEEP_PLANS", "")

# 轮询邮件间隔/超时（秒）
EMAIL_POLL_INTERVAL = _get_int_env("EMAIL_POLL_INTERVAL", 3)
EMAIL_POLL_TIMEOUT = _get_int_env("EMAIL_POLL_TIMEOUT", 300)

# API 鉴权（不设置则不启用）
API_KEY = os.environ.get("API_KEY", "")

# 自动巡检配置
AUTO_CHECK_INTERVAL = _get_int_env("AUTO_CHECK_INTERVAL", 300)  # 巡检间隔（秒），默认 5 分钟
AUTO_CHECK_TARGET_SEATS = _get_int_env("AUTO_CHECK_TARGET_SEATS", 5)  # 自动巡检目标 Team seat 数
AUTO_CHECK_THRESHOLD = _get_int_env("AUTO_CHECK_THRESHOLD", 10)  # 额度低于此百分比触发轮转，默认 10%
AUTO_CHECK_MIN_LOW = _get_int_env("AUTO_CHECK_MIN_LOW", 2)  # 至少几个账号低于阈值才触发，默认 2
AUTO_CHECK_RETRY_ADD_PHONE = _get_bool_env("AUTO_CHECK_RETRY_ADD_PHONE", True)  # 是否自动重试 add_phone
AUTO_CHECK_ADD_PHONE_MAX_RETRIES = _get_int_env("AUTO_CHECK_ADD_PHONE_MAX_RETRIES", 3)  # add_phone 最大自动重试次数

# Playwright 代理配置
PLAYWRIGHT_PROXY_URL = os.environ.get("PLAYWRIGHT_PROXY_URL", "").strip()
PLAYWRIGHT_PROXY_SERVER = os.environ.get("PLAYWRIGHT_PROXY_SERVER", "").strip()
PLAYWRIGHT_PROXY_USERNAME = os.environ.get("PLAYWRIGHT_PROXY_USERNAME", "").strip()
PLAYWRIGHT_PROXY_PASSWORD = os.environ.get("PLAYWRIGHT_PROXY_PASSWORD", "").strip()
PLAYWRIGHT_PROXY_BYPASS = os.environ.get("PLAYWRIGHT_PROXY_BYPASS", "").strip()

# 浏览器渠道：patchright 推荐使用真实 Chrome（而非自带 Chromium）以最大化反检测效果，
# 因此使用 patchright 时默认 channel="chrome"。可用此环境变量覆盖（如 "msedge"），
# 设为空字符串或 "chromium" 则使用自带 Chromium（无真实 Chrome 的环境可这样关闭）。
PLAYWRIGHT_BROWSER_CHANNEL = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL", "").strip()

# WebDAV 远程备份
WEBDAV_BACKUP_ENABLED = _get_bool_env("WEBDAV_BACKUP_ENABLED", False)
WEBDAV_URL = os.environ.get("WEBDAV_URL", "").strip()
WEBDAV_USERNAME = os.environ.get("WEBDAV_USERNAME", "").strip()
WEBDAV_PASSWORD = os.environ.get("WEBDAV_PASSWORD", "").strip()
WEBDAV_BACKUP_INTERVAL = _get_int_env("WEBDAV_BACKUP_INTERVAL", 3600)  # 备份间隔（秒），默认 1 小时
WEBDAV_BACKUP_KEEP_VERSIONS = _get_int_env("WEBDAV_BACKUP_KEEP_VERSIONS", 10)  # 保留最近 N 个版本


def _format_proxy_host(hostname: str) -> str:
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


# 各 channel 对应的可执行文件路径（patchright/playwright 的 channel 参数值 → 可执行文件名）
_CHANNEL_EXECUTABLE_NAMES = {
    "chrome": "google-chrome",
    "msedge": "microsoft-edge",
    "chromium": "chromium",
    "chromium-browser": "chromium-browser",
}


def _channel_executable_exists(channel: str) -> bool:
    """检测指定 channel 对应的浏览器可执行文件是否在 PATH 中可找到。"""
    name = _CHANNEL_EXECUTABLE_NAMES.get(channel.lower(), channel)
    return shutil.which(name) is not None


def _parse_proxy_url(proxy_url: str):
    if "://" not in proxy_url:
        return {"server": proxy_url}

    parsed = urlsplit(proxy_url)
    if not parsed.scheme or not parsed.hostname:
        return {"server": proxy_url}

    host = _format_proxy_host(parsed.hostname)
    server = f"{parsed.scheme}://{host}"
    if parsed.port:
        server = f"{server}:{parsed.port}"

    proxy = {"server": server}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


def get_chatgpt_api_transport() -> str:
    return _normalize_chatgpt_api_transport(_get_str_env("CHATGPT_API_TRANSPORT", "auto"))


def get_chatgpt_api_http_timeout() -> int:
    return max(5, _get_int_env("CHATGPT_API_HTTP_TIMEOUT", 60))


def get_chatgpt_api_impersonate() -> str:
    return _get_str_env("CHATGPT_API_IMPERSONATE", "chrome136") or "chrome136"


def get_chatgpt_http_proxy_url() -> str:
    proxy_url = _get_str_env("PLAYWRIGHT_PROXY_URL", "")
    if proxy_url:
        return proxy_url

    proxy_server = _get_str_env("PLAYWRIGHT_PROXY_SERVER", "")
    if not proxy_server:
        return ""

    username = _get_str_env("PLAYWRIGHT_PROXY_USERNAME", "")
    password = _get_str_env("PLAYWRIGHT_PROXY_PASSWORD", "")
    if not (username or password):
        return proxy_server

    parsed = urlsplit(proxy_server)
    if not parsed.scheme or not parsed.hostname:
        return proxy_server

    host = _format_proxy_host(parsed.hostname)
    auth = quote(username, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"

    proxy = f"{parsed.scheme}://{auth}@{host}"
    if parsed.port:
        proxy = f"{proxy}:{parsed.port}"
    return proxy


def get_playwright_launch_options():
    """统一的 Playwright Chromium 启动参数。

    使用 patchright 时遵循其反检测最佳实践：使用真实 Chrome 渠道，并避免注入
    会反过来成为指纹的 automation flag（patchright 已在内核层抹除 webdriver 等痕迹）；
    回退官方 playwright 时维持原有的 --disable-blink-features 注入。
    """
    from autoteam.browser_runtime import USING_PATCHRIGHT

    options = {"headless": False}

    if USING_PATCHRIGHT:
        options["args"] = ["--no-sandbox"]
    else:
        options["args"] = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]

    # 真实 Chrome 渠道：patchright 下默认启用，可用 PLAYWRIGHT_BROWSER_CHANNEL 覆盖；
    # 显式设为 "chromium" 则使用自带 Chromium（无真实 Chrome 的环境）。
    # 自动检测：如果真实 Chrome 未安装，回退到自带 Chromium，避免 Linux 服务器报错。
    channel = PLAYWRIGHT_BROWSER_CHANNEL or ("chrome" if USING_PATCHRIGHT else "")
    if channel and channel.lower() != "chromium":
        if _channel_executable_exists(channel):
            options["channel"] = channel
        else:
            logger = logging.getLogger(__name__)
            logger.info("[Config] 未找到 %s 可执行文件，回退到自带 Chromium", channel)

    proxy = None
    if PLAYWRIGHT_PROXY_URL:
        proxy = _parse_proxy_url(PLAYWRIGHT_PROXY_URL)
    elif PLAYWRIGHT_PROXY_SERVER:
        proxy = {"server": PLAYWRIGHT_PROXY_SERVER}
        if PLAYWRIGHT_PROXY_USERNAME:
            proxy["username"] = PLAYWRIGHT_PROXY_USERNAME
        if PLAYWRIGHT_PROXY_PASSWORD:
            proxy["password"] = PLAYWRIGHT_PROXY_PASSWORD

    if proxy:
        if PLAYWRIGHT_PROXY_BYPASS:
            proxy["bypass"] = PLAYWRIGHT_PROXY_BYPASS
        options["proxy"] = proxy

    return options


def get_playwright_context_options():
    """统一的浏览器 context 参数。

    不再覆盖 user_agent：让浏览器使用与 sec-ch-ua client-hints 相一致的原生 UA，
    避免"UA 与 client-hints 版本不一致"这一典型机器人特征被 Cloudflare 识别
    （此前硬编码 Chrome/146 但真实内核为 148，二者矛盾会拉低风控评分）。
    """
    return {"viewport": {"width": 1280, "height": 800}}
