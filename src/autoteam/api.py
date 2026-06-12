"""AutoTeam HTTP API - 将 CLI 功能暴露为 HTTP 接口"""

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autoteam.config import API_KEY
from autoteam.textio import parse_env_line, read_text, write_text

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"
_SCREENSHOT_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

app = FastAPI(
    title="AutoTeam API",
    description="ChatGPT Team 账号自动轮转管理 API",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# API Key 鉴权中间件
# ---------------------------------------------------------------------------

_AUTH_SKIP_PATHS = {"/api/auth/check", "/api/setup/status", "/api/setup/save"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    try:
        _maybe_reload_runtime_config_from_env_file()
    except Exception as exc:
        logger.warning("[配置] 自动热加载失败: %s", exc)

    path = request.url.path
    # 不鉴权的路径：非 /api 路径、auth/check 端点
    if not path.startswith("/api/") or path in _AUTH_SKIP_PATHS:
        return await call_next(request)
    # 未配置 API_KEY 则跳过鉴权
    if not API_KEY:
        return await call_next(request)
    # 从 header 或 query param 获取 key
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.query_params.get("key", "")
    if token != API_KEY:
        return JSONResponse(status_code=401, content={"detail": "未授权，请提供有效的 API Key"})
    return await call_next(request)


@app.get("/api/auth/check")
def check_auth(request: Request):
    """验证 API Key 是否有效。未配置 API_KEY 时始终返回成功。"""
    if not API_KEY:
        return {"authenticated": True, "auth_required": False}
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == API_KEY:
        return {"authenticated": True, "auth_required": True}
    return JSONResponse(status_code=401, content={"authenticated": False, "auth_required": True})


# ---------------------------------------------------------------------------
# 初始配置 API（无需鉴权）
# ---------------------------------------------------------------------------


class SetupConfig(BaseModel):
    MAIL_PROVIDER: str = "cloudmail"
    CLOUDMAIL_BASE_URL: str = ""
    CLOUDMAIL_EMAIL: str = ""
    CLOUDMAIL_PASSWORD: str = ""
    CLOUDMAIL_DOMAIN: str = ""
    CF_TEMP_EMAIL_BASE_URL: str = ""
    CF_TEMP_EMAIL_ADMIN_PASSWORD: str = ""
    CF_TEMP_EMAIL_DOMAIN: str = ""
    SYNC_TARGET_CPA: str | bool = ""
    CPA_URL: str = "http://127.0.0.1:8317"
    CPA_KEY: str = ""
    SYNC_KEEP_PLANS: str = ""
    SYNC_TARGET_SUB2API: str | bool = ""
    SUB2API_URL: str = ""
    SUB2API_EMAIL: str = ""
    SUB2API_PASSWORD: str = ""
    SUB2API_GROUP: str = ""
    SUB2API_PROXY: str | int = ""
    SUB2API_CONCURRENCY: str | int = "10"
    SUB2API_PRIORITY: str | int = "1"
    SUB2API_RATE_MULTIPLIER: str | int | float = "1"
    SUB2API_AUTO_PAUSE_ON_EXPIRED: str | bool = "true"
    SUB2API_MODEL_WHITELIST: str = ""
    SUB2API_OPENAI_WS_MODE: str = "off"
    SUB2API_OPENAI_PASSTHROUGH: str | bool = "false"
    SUB2API_OVERWRITE_ACCOUNT_SETTINGS: str | bool = "false"
    PLAYWRIGHT_PROXY_URL: str = ""
    PLAYWRIGHT_PROXY_BYPASS: str = ""
    WEBDAV_BACKUP_ENABLED: str | bool = "false"
    WEBDAV_URL: str = ""
    WEBDAV_USERNAME: str = ""
    WEBDAV_PASSWORD: str = ""
    WEBDAV_BACKUP_INTERVAL: str | int = "3600"
    WEBDAV_BACKUP_KEEP_VERSIONS: str | int = "10"
    API_KEY: str = ""


class SourceConfig(BaseModel):
    content: str = ""


_RUNTIME_CONFIG_CLEARABLE_FIELDS = {
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_MODEL_WHITELIST",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_BYPASS",
    "WEBDAV_URL",
    "WEBDAV_USERNAME",
    "WEBDAV_PASSWORD",
}

_CLOUDMAIL_REQUIRED_KEYS = ("CLOUDMAIL_BASE_URL", "CLOUDMAIL_EMAIL", "CLOUDMAIL_PASSWORD", "CLOUDMAIL_DOMAIN")
_CF_TEMP_EMAIL_REQUIRED_KEYS = (
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
)
_CPA_REQUIRED_KEYS = ("CPA_URL", "CPA_KEY")
_SUB2API_REQUIRED_KEYS = ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD")
_SYNC_TARGET_TOGGLE_KEYS = ("SYNC_TARGET_CPA", "SYNC_TARGET_SUB2API")

_ALL_RUNTIME_ENV_KEYS = [
    "MAIL_PROVIDER",
    "CLOUDMAIL_BASE_URL",
    "CLOUDMAIL_EMAIL",
    "CLOUDMAIL_PASSWORD",
    "CLOUDMAIL_DOMAIN",
    "CF_TEMP_EMAIL_BASE_URL",
    "CF_TEMP_EMAIL_ADMIN_PASSWORD",
    "CF_TEMP_EMAIL_DOMAIN",
    "CHATGPT_ACCOUNT_ID",
    "SYNC_TARGET_CPA",
    "CPA_URL",
    "CPA_KEY",
    "SYNC_TARGET_SUB2API",
    "SUB2API_URL",
    "SUB2API_EMAIL",
    "SUB2API_PASSWORD",
    "SUB2API_GROUP",
    "SUB2API_PROXY",
    "SUB2API_CONCURRENCY",
    "SUB2API_PRIORITY",
    "SUB2API_RATE_MULTIPLIER",
    "SUB2API_AUTO_PAUSE_ON_EXPIRED",
    "SUB2API_MODEL_WHITELIST",
    "SUB2API_OPENAI_WS_MODE",
    "SUB2API_OPENAI_PASSTHROUGH",
    "SUB2API_OVERWRITE_ACCOUNT_SETTINGS",
    "EMAIL_POLL_INTERVAL",
    "EMAIL_POLL_TIMEOUT",
    "API_KEY",
    "AUTO_CHECK_INTERVAL",
    "AUTO_CHECK_TARGET_SEATS",
    "AUTO_CHECK_THRESHOLD",
    "AUTO_CHECK_MIN_LOW",
    "AUTO_CHECK_RETRY_ADD_PHONE",
    "AUTO_CHECK_ADD_PHONE_MAX_RETRIES",
    "PLAYWRIGHT_PROXY_URL",
    "PLAYWRIGHT_PROXY_SERVER",
    "PLAYWRIGHT_PROXY_USERNAME",
    "PLAYWRIGHT_PROXY_PASSWORD",
    "PLAYWRIGHT_PROXY_BYPASS",
    "WEBDAV_BACKUP_ENABLED",
    "WEBDAV_URL",
    "WEBDAV_USERNAME",
    "WEBDAV_PASSWORD",
    "WEBDAV_BACKUP_INTERVAL",
    "WEBDAV_BACKUP_KEEP_VERSIONS",
]
_RUNTIME_ENV_BASE = {key: os.environ.get(key) for key in _ALL_RUNTIME_ENV_KEYS}
_runtime_env_reload_lock = threading.Lock()
_runtime_env_reload_state = {"signature": None}


def _runtime_config_prompt_map():
    from autoteam.setup_wizard import REQUIRED_CONFIGS

    return {key: prompt for key, prompt, _default, _optional in REQUIRED_CONFIGS}


def _current_runtime_env():
    from autoteam.setup_wizard import _read_env

    env = _read_env()
    merged = {key: value for key, value in os.environ.items()}
    merged.update({key: value for key, value in env.items() if value is not None})
    return merged


def _missing_runtime_configs(keys: tuple[str, ...] | list[str], *, env: dict[str, str] | None = None):
    env_values = env or _current_runtime_env()
    prompt_map = _runtime_config_prompt_map()
    missing = []
    for key in keys:
        value = (env_values.get(key, "") or "").strip()
        if not value:
            missing.append((key, prompt_map.get(key, key)))
    return missing


def _format_missing_runtime_configs(missing: list[tuple[str, str]]) -> str:
    return "、".join(f"{key}（{prompt}）" for key, prompt in missing)


def _effective_sync_target_states(env: dict[str, str] | None = None):
    from autoteam.sync_targets import get_sync_target_states

    return get_sync_target_states(env or _current_runtime_env())


def _runtime_required_keys(env: dict[str, str] | None = None) -> set[str]:
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_required_keys

    states = _effective_sync_target_states(env)
    provider = get_mail_provider_name(env)
    required = set(get_mail_provider_required_keys(provider))
    required.add("API_KEY")
    if states.get("cpa"):
        required.update(_CPA_REQUIRED_KEYS)
    if states.get("sub2api"):
        required.update(_SUB2API_REQUIRED_KEYS)
    return required


def _require_runtime_configs(
    keys: tuple[str, ...] | list[str], action_label: str, *, env: dict[str, str] | None = None
):
    missing = _missing_runtime_configs(keys, env=env)
    if not missing:
        return

    detail = _format_missing_runtime_configs(missing)
    raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _require_mail_provider_configs(
    action_label: str, *, provider: str | None = None, env: dict[str, str] | None = None
):
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_prompt, get_mail_provider_required_keys

    env_values = env or _current_runtime_env()
    resolved_provider = provider or get_mail_provider_name(env_values)
    missing = _missing_runtime_configs(get_mail_provider_required_keys(resolved_provider), env=env_values)
    if not missing:
        return

    detail = _format_missing_runtime_configs(missing)
    provider_label = get_mail_provider_prompt(resolved_provider)
    raise HTTPException(
        status_code=400,
        detail=f"{action_label} 前请先在配置面板填写当前邮箱服务（{provider_label}）配置：{detail}",
    )


def _require_pool_operation_configs(action_label: str):
    from autoteam.sync_targets import get_enabled_sync_targets

    env = _current_runtime_env()
    _require_mail_provider_configs(action_label, env=env)

    enabled_targets = get_enabled_sync_targets(env)
    if not enabled_targets:
        raise HTTPException(
            status_code=400, detail=f"{action_label} 前请先在配置面板启用至少一个远端同步目标（CPA 或 Sub2API）"
        )

    missing = _missing_runtime_configs(
        [
            key
            for target in enabled_targets
            for key in (
                _CPA_REQUIRED_KEYS if target == "cpa" else _SUB2API_REQUIRED_KEYS if target == "sub2api" else ()
            )
        ],
        env=env,
    )
    if missing:
        detail = _format_missing_runtime_configs(missing)
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _require_account_mail_configs(account: dict, action_label: str):
    from autoteam.mail_provider import get_account_mail_provider

    provider = get_account_mail_provider(account)
    _require_mail_provider_configs(action_label, provider=provider, env=_current_runtime_env())


def _require_cpa_configs(action_label: str):
    _require_runtime_configs(_CPA_REQUIRED_KEYS, action_label)


def _require_sync_target_configs(action_label: str):
    from autoteam.sync_targets import get_enabled_sync_targets

    env = _current_runtime_env()
    enabled_targets = get_enabled_sync_targets(env)
    if not enabled_targets:
        raise HTTPException(
            status_code=400, detail=f"{action_label} 前请先在配置面板启用至少一个远端同步目标（CPA 或 Sub2API）"
        )

    missing = _missing_runtime_configs(
        [
            key
            for target in enabled_targets
            for key in (
                _CPA_REQUIRED_KEYS if target == "cpa" else _SUB2API_REQUIRED_KEYS if target == "sub2api" else ()
            )
        ],
        env=env,
    )
    if missing:
        detail = _format_missing_runtime_configs(missing)
        raise HTTPException(status_code=400, detail=f"{action_label} 前请先在配置面板填写：{detail}")


def _collect_config_fields(*, include_values: bool = False, configs=None):
    from autoteam.mail_provider import get_mail_provider_name
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _read_env

    env = _read_env()
    merged_env = dict(os.environ)
    merged_env.update(env)
    target_states = _effective_sync_target_states(merged_env)
    runtime_required_keys = _runtime_required_keys(merged_env)
    mail_provider = get_mail_provider_name(merged_env)
    config_items = configs or REQUIRED_CONFIGS
    fields = []
    all_ok = True
    for key, prompt, default, optional in config_items:
        raw_value = env.get(key, "") or os.environ.get(key, "")
        if key == "SYNC_TARGET_CPA":
            raw_value = "true" if target_states.get("cpa") else "false"
            configured = True
        elif key == "SYNC_TARGET_SUB2API":
            raw_value = "true" if target_states.get("sub2api") else "false"
            configured = True
        elif key == "MAIL_PROVIDER":
            raw_value = mail_provider
            configured = True
        else:
            configured = bool(raw_value)
        if not configured and (key in runtime_required_keys or not optional):
            all_ok = False

        field = {
            "key": key,
            "prompt": prompt,
            "default": default,
            "optional": optional,
            "configured": configured,
        }
        if include_values:
            field["value"] = raw_value if raw_value != "" else default
            field["runtime_required"] = key in runtime_required_keys
        fields.append(field)
    return {"configured": all_ok, "fields": fields}


def _reload_runtime_config_modules():
    import importlib

    import autoteam.config

    modules = [autoteam.config]
    for module_name in (
        "autoteam.cloudmail",
        "autoteam.cloudflare_temp_email",
        "autoteam.mail_provider",
        "autoteam.cpa_sync",
        "autoteam.sub2api_sync",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        modules.append(module)

    for module in modules:
        importlib.reload(module)


def _restore_runtime_env(previous_env: dict[str, str | None]):
    for key, previous in previous_env.items():
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def _runtime_env_file_signature():
    from autoteam.setup_wizard import ENV_FILE

    if not ENV_FILE.exists():
        return None
    stat = ENV_FILE.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _read_runtime_env_file_text():
    from autoteam.setup_wizard import ENV_FILE

    if not ENV_FILE.exists():
        return ""
    return read_text(ENV_FILE)


def _read_runtime_source_text():
    from autoteam.setup_wizard import ENV_EXAMPLE, ENV_FILE

    if ENV_FILE.exists():
        return read_text(ENV_FILE), str(ENV_FILE)
    if ENV_EXAMPLE.exists():
        return read_text(ENV_EXAMPLE), str(ENV_FILE)
    return "", str(ENV_FILE)


def _write_runtime_source_text(content: str):
    from autoteam.setup_wizard import ENV_FILE

    write_text(ENV_FILE, content)


def _restore_runtime_source_text(previous_exists: bool, previous_content: str):
    from autoteam.setup_wizard import ENV_FILE

    if previous_exists:
        write_text(ENV_FILE, previous_content)
        return
    if ENV_FILE.exists():
        ENV_FILE.unlink()


def _load_env_values_from_source(content: str, env_keys: list[str]):
    values = {key: "" for key in env_keys}
    for line in content.splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in values:
            values[key] = value
    return values


def _load_present_env_values_from_source(content: str, env_keys: list[str]):
    allowed = set(env_keys)
    values = {}
    for line in content.splitlines():
        parsed = parse_env_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in allowed:
            values[key] = value
    return values


def _validate_runtime_required_values(values: dict[str, str]):
    from autoteam.setup_wizard import STARTUP_REQUIRED_CONFIGS

    return [
        f"{key} ({prompt})"
        for key, prompt, _default, optional in STARTUP_REQUIRED_CONFIGS
        if not optional and not values.get(key)
    ]


def _parse_bool_text(value: object, *, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    lowered = text.lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"无效布尔值: {value}")


def _validate_runtime_optional_values(values: dict[str, str]):
    normalized = dict(values)

    def _normalize_positive_int(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是正整数") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是正整数")
        normalized[key] = str(value)

    def _normalize_int(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是整数") from exc
        normalized[key] = str(value)

    def _normalize_positive_float(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            return
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是大于 0 的数字") from exc
        if value <= 0:
            raise ValueError(f"{key} 必须是大于 0 的数字")
        normalized[key] = format(value, "g")

    def _normalize_bool(key: str):
        try:
            value = _parse_bool_text(normalized.get(key, ""), default=None)
        except ValueError as exc:
            raise ValueError(f"{key} 必须是 true 或 false") from exc
        if value is None:
            return
        normalized[key] = "true" if value else "false"

    def _normalize_sub2api_proxy(key: str):
        raw = str(normalized.get(key, "") or "").strip()
        if not raw:
            normalized[key] = ""
            return
        if raw.lstrip("+-").isdigit():
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{key} 必须是 Sub2API 代理 ID（正整数）或代理名称") from exc
            if value <= 0:
                raise ValueError(f"{key} 必须是 Sub2API 代理 ID（正整数）或代理名称")
            normalized[key] = str(value)
            return
        normalized[key] = raw

    _normalize_sub2api_proxy("SUB2API_PROXY")
    _normalize_positive_int("SUB2API_CONCURRENCY")
    _normalize_int("SUB2API_PRIORITY")
    _normalize_positive_float("SUB2API_RATE_MULTIPLIER")
    _normalize_bool("SUB2API_AUTO_PAUSE_ON_EXPIRED")
    _normalize_bool("SUB2API_OPENAI_PASSTHROUGH")
    _normalize_bool("SUB2API_OVERWRITE_ACCOUNT_SETTINGS")

    ws_mode = str(normalized.get("SUB2API_OPENAI_WS_MODE", "") or "").strip().lower()
    if ws_mode:
        if ws_mode not in {"off", "ctx_pool", "passthrough"}:
            raise ValueError("SUB2API_OPENAI_WS_MODE 必须是 off、ctx_pool 或 passthrough")
        normalized["SUB2API_OPENAI_WS_MODE"] = ws_mode

    whitelist = str(normalized.get("SUB2API_MODEL_WHITELIST", "") or "").strip()
    if whitelist:
        normalized["SUB2API_MODEL_WHITELIST"] = ",".join(part.strip() for part in whitelist.split(",") if part.strip())
    else:
        normalized["SUB2API_MODEL_WHITELIST"] = ""

    return normalized


def _sync_runtime_globals():
    global API_KEY

    API_KEY = os.environ.get("API_KEY", "")

    auto_check_config = globals().get("_auto_check_config")
    auto_check_restart = globals().get("_auto_check_restart")
    if auto_check_config is None:
        return

    try:
        from autoteam.config import (
            AUTO_CHECK_ADD_PHONE_MAX_RETRIES,
            AUTO_CHECK_INTERVAL,
            AUTO_CHECK_MIN_LOW,
            AUTO_CHECK_RETRY_ADD_PHONE,
            AUTO_CHECK_TARGET_SEATS,
            AUTO_CHECK_THRESHOLD,
        )

        auto_check_config["interval"] = AUTO_CHECK_INTERVAL
        auto_check_config["target_seats"] = AUTO_CHECK_TARGET_SEATS
        auto_check_config["threshold"] = AUTO_CHECK_THRESHOLD
        auto_check_config["min_low"] = AUTO_CHECK_MIN_LOW
        auto_check_config["retry_add_phone"] = AUTO_CHECK_RETRY_ADD_PHONE
        auto_check_config["add_phone_max_retries"] = AUTO_CHECK_ADD_PHONE_MAX_RETRIES
        if auto_check_restart is not None:
            auto_check_restart.set()
    except Exception:
        pass


def _apply_runtime_env_file_values(values: dict[str, str]):
    for key in _ALL_RUNTIME_ENV_KEYS:
        if key in values:
            value = values[key]
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
            continue

        base_value = _RUNTIME_ENV_BASE.get(key)
        if base_value:
            os.environ[key] = base_value
        else:
            os.environ.pop(key, None)


def _sync_runtime_env_reload_state():
    with _runtime_env_reload_lock:
        _runtime_env_reload_state["signature"] = _runtime_env_file_signature()


def _maybe_reload_runtime_config_from_env_file(*, force: bool = False):
    signature = _runtime_env_file_signature()

    with _runtime_env_reload_lock:
        previous_signature = _runtime_env_reload_state.get("signature")
        if not force and signature == previous_signature:
            return False

        previous_env = {key: os.environ.get(key) for key in _ALL_RUNTIME_ENV_KEYS}
        try:
            content = _read_runtime_env_file_text()
            values = _load_present_env_values_from_source(content, _ALL_RUNTIME_ENV_KEYS)
            _apply_runtime_env_file_values(values)
            _reload_runtime_config_modules()
            _sync_runtime_globals()
            _runtime_env_reload_state["signature"] = signature
        except Exception:
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            _sync_runtime_globals()
            raise

    if previous_signature is not None and signature != previous_signature:
        logger.info("[配置] 检测到 .env 变更，已自动热加载")
    return True


def _verify_runtime_integrations(
    previous_env: dict[str, str | None] | None = None, *, env: dict[str, object] | None = None
):
    from autoteam.mail_provider import get_mail_provider_name, get_mail_provider_prompt, get_mail_provider_required_keys
    from autoteam.setup_wizard import _verify_cpa, _verify_mail_provider, _verify_sub2api

    errors = []
    runtime_env = dict(env or os.environ)
    mail_provider = get_mail_provider_name(runtime_env)
    mail_keys = tuple(get_mail_provider_required_keys(mail_provider))
    cpa_keys = ("CPA_URL", "CPA_KEY")
    sub2api_keys = ("SUB2API_URL", "SUB2API_EMAIL", "SUB2API_PASSWORD")

    mail_values = [runtime_env.get(key, "") for key in mail_keys]
    cpa_values = [runtime_env.get(key, "") for key in cpa_keys]
    sub2api_values = [runtime_env.get(key, "") for key in sub2api_keys]
    sync_states = _effective_sync_target_states(runtime_env)

    if mail_keys and all(mail_values) and not _verify_mail_provider(mail_provider):
        errors.append(f"{get_mail_provider_prompt(mail_provider)} 连接失败")
    if sync_states.get("cpa") and all(cpa_values) and not _verify_cpa():
        errors.append("CPA 连接失败")
    if sync_states.get("sub2api") and all(sub2api_values) and not _verify_sub2api():
        errors.append("Sub2API 连接失败")
    if errors:
        api_key = ""
        if previous_env:
            api_key = previous_env.get("API_KEY", "") or ""
        return JSONResponse(status_code=400, content={"message": "、".join(errors), "api_key": api_key})
    return None


def _save_runtime_config(data: dict[str, str]):
    """保存运行时配置到 .env，并在当前进程立即生效。"""
    import secrets as _secrets

    from autoteam.mail_provider import normalize_mail_provider
    from autoteam.setup_wizard import REQUIRED_CONFIGS, _write_env

    env_keys = [key for key, _prompt, _default, _optional in REQUIRED_CONFIGS]
    existing = {key: os.environ.get(key, "") for key in env_keys}
    merged = {key: data.get(key, existing.get(key, "")) for key in env_keys}
    merged["MAIL_PROVIDER"] = normalize_mail_provider(merged.get("MAIL_PROVIDER") or existing.get("MAIL_PROVIDER"))

    if not merged.get("API_KEY"):
        merged["API_KEY"] = _secrets.token_urlsafe(24)

    missing = _validate_runtime_required_values(merged)
    if missing:
        return JSONResponse(
            status_code=400,
            content={"message": "缺少必填项: " + "、".join(missing)},
        )

    try:
        merged = _validate_runtime_optional_values(merged)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"message": str(exc)})

    previous_env = {key: os.environ.get(key) for key in env_keys}
    try:
        for key, value in merged.items():
            os.environ[key] = value
        _reload_runtime_config_modules()

        verify_result = _verify_runtime_integrations(previous_env, env=merged)
        if verify_result:
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return verify_result

        for key, value in merged.items():
            if value or key in _RUNTIME_CONFIG_CLEARABLE_FIELDS:
                _write_env(key, value)

        _sync_runtime_env_reload_state()
        _sync_runtime_globals()
        return {"message": "配置保存成功", "api_key": API_KEY, "configured": True}
    except Exception:
        _restore_runtime_env(previous_env)
        _reload_runtime_config_modules()
        raise


@app.get("/api/setup/status")
def get_setup_status():
    """检查配置是否完整"""
    from autoteam.setup_wizard import STARTUP_REQUIRED_CONFIGS

    return _collect_config_fields(configs=STARTUP_REQUIRED_CONFIGS)


@app.post("/api/setup/save")
def post_setup_save(config: SetupConfig):
    """保存配置到 .env 并验证连通性"""
    return _save_runtime_config(config.model_dump())


@app.get("/api/config/runtime")
def get_runtime_config():
    """获取当前运行时配置，供登录后的设置面板编辑。"""
    return _collect_config_fields(include_values=True)


@app.get("/api/config/source")
def get_runtime_config_source():
    """获取 .env 源文件内容。"""
    content, path = _read_runtime_source_text()
    return {"path": path, "content": content}


@app.put("/api/config/runtime")
def put_runtime_config(config: SetupConfig):
    """登录后修改 CloudMail / CPA / Sub2API / 代理等运行时配置。"""
    return _save_runtime_config(config.model_dump())


@app.put("/api/config/source")
def put_runtime_config_source(config: SourceConfig):
    """保存 .env 源文件内容，并立即应用到运行时。"""
    env_keys = list(_ALL_RUNTIME_ENV_KEYS)
    previous_env = {key: os.environ.get(key) for key in env_keys}
    source_path = None
    previous_exists = False
    previous_content = ""

    try:
        current_content, source_path = _read_runtime_source_text()
        previous_content = current_content
        from autoteam.setup_wizard import ENV_FILE

        previous_exists = ENV_FILE.exists()

        _write_runtime_source_text(config.content)

        loaded_values = _load_env_values_from_source(config.content, env_keys)
        missing = _validate_runtime_required_values(loaded_values)
        if missing:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return JSONResponse(status_code=400, content={"message": "缺少必填项: " + "、".join(missing)})

        try:
            loaded_values = _validate_runtime_optional_values(loaded_values)
        except ValueError as exc:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return JSONResponse(status_code=400, content={"message": str(exc)})

        for key in env_keys:
            if loaded_values.get(key):
                os.environ[key] = loaded_values[key]
            else:
                os.environ.pop(key, None)

        _reload_runtime_config_modules()
        verify_result = _verify_runtime_integrations(previous_env, env=loaded_values)
        if verify_result:
            _restore_runtime_source_text(previous_exists, previous_content)
            _restore_runtime_env(previous_env)
            _reload_runtime_config_modules()
            return verify_result

        _sync_runtime_env_reload_state()
        _sync_runtime_globals()
        return {
            "message": "源文件保存成功",
            "api_key": API_KEY,
            "configured": True,
            "path": source_path,
        }
    except Exception:
        _restore_runtime_source_text(previous_exists, previous_content)
        _restore_runtime_env(previous_env)
        _reload_runtime_config_modules()
        raise


# ---------------------------------------------------------------------------
# 后台任务管理
# ---------------------------------------------------------------------------

_tasks: dict[str, dict] = {}
_playwright_lock = threading.Lock()
_current_task_id: str | None = None
_admin_login_api = None
_admin_login_step: str | None = None
_main_codex_flow = None
_main_codex_step: str | None = None
_main_codex_action: str | None = None
_manual_account_flow = None
MAX_TASK_HISTORY = 50
_TASK_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class TaskCancelledError(RuntimeError):
    """后台任务被用户请求终止。"""


def _get_task_cancel_message(task: dict | None) -> str:
    if not task:
        return "任务已终止"
    return task.get("cancel_message") or "任务已终止"


def is_task_cancel_requested(task_id: str | None = None) -> bool:
    task = _tasks.get(task_id or _current_task_id or "")
    return bool(task and task.get("cancel_requested"))


def ensure_current_task_not_cancelled(task_id: str | None = None):
    task = _tasks.get(task_id or _current_task_id or "")
    if task and task.get("cancel_requested"):
        raise TaskCancelledError(_get_task_cancel_message(task))


# ---------------------------------------------------------------------------
# Playwright 专用线程执行器（解决跨线程调用问题）
# ---------------------------------------------------------------------------

import queue as _queue


class _PlaywrightExecutor:
    """将 Playwright 操作派发到专用线程执行，避免跨线程错误"""

    def __init__(self):
        self._queue: _queue.Queue = _queue.Queue()
        self._thread: threading.Thread | None = None
        self._broken_reason: str | None = None

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            func, args, kwargs, result_event, result_holder = item
            try:
                result_holder["result"] = func(*args, **kwargs)
            except Exception as e:
                result_holder["error"] = e
            finally:
                result_event.set()

    def ensure_started(self):
        if self._broken_reason:
            raise RuntimeError(self._broken_reason)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, daemon=True)
            self._thread.start()

    def run(self, func, *args, timeout_seconds=300, **kwargs):
        """在专用线程中执行函数，阻塞等待结果"""
        self.ensure_started()
        result_event = threading.Event()
        result_holder: dict = {}
        self._queue.put((func, args, kwargs, result_event, result_holder))
        if not result_event.wait(timeout=max(1, timeout_seconds)):
            func_name = getattr(func, "__name__", repr(func))
            self._broken_reason = (
                f"Playwright 专用线程执行超时（>{timeout_seconds}s）: {func_name}；"
                "为避免浏览器进程继续堆积，已拒绝后续专用线程任务，请重启服务"
            )
            logger.error("[API] %s", self._broken_reason)
            raise TimeoutError(self._broken_reason)
        if "error" in result_holder:
            raise result_holder["error"]
        return result_holder.get("result")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("[API] Playwright 专用线程在停止时仍未退出")
            else:
                self._thread = None
                self._queue = _queue.Queue()
                self._broken_reason = None


_pw_executor = _PlaywrightExecutor()


ADMIN_LOGIN_START_TIMEOUT_SECONDS = 600


def _stop_playwright_resource(resource):
    if not resource:
        return

    stop = getattr(resource, "stop", None)
    if not callable(stop):
        return

    try:
        stop()
    except Exception:
        pass


def _run_playwright_start(factory, starter, *args, **kwargs):
    resource = factory()
    try:
        result = starter(resource, *args, **kwargs)
        return resource, result
    except Exception:
        _stop_playwright_resource(resource)
        raise


def _run_with_chatgpt_session(callback):
    from autoteam.chatgpt_api import ChatGPTTeamAPI

    chatgpt = ChatGPTTeamAPI()
    try:
        chatgpt.start()
        return callback(chatgpt)
    finally:
        chatgpt.stop()


def _current_busy_detail(default_message: str):
    if _admin_login_api:
        return {
            "message": default_message,
            "running_task": {
                "task_id": "admin-login",
                "command": "admin-login",
                "started_at": None,
            },
        }

    if _main_codex_flow:
        return {
            "message": default_message,
            "running_task": {
                "task_id": "main-codex-sync",
                "command": "main-codex-sync",
                "started_at": None,
            },
        }

    running = _tasks.get(_current_task_id, {})
    return {
        "message": default_message,
        "running_task": {
            "task_id": _current_task_id,
            "command": running.get("command", "unknown"),
            "started_at": running.get("started_at"),
        },
    }


def _prune_tasks():
    """保留最近 MAX_TASK_HISTORY 个任务"""
    if len(_tasks) <= MAX_TASK_HISTORY:
        return
    sorted_ids = sorted(_tasks, key=lambda k: _tasks[k]["created_at"])
    for tid in sorted_ids[: len(_tasks) - MAX_TASK_HISTORY]:
        if _tasks[tid]["status"] in _TASK_TERMINAL_STATUSES:
            del _tasks[tid]


def _run_task(task_id: str, func, *args, **kwargs):
    """在后台线程中执行任务"""
    global _current_task_id
    task = _tasks[task_id]

    _playwright_lock.acquire()
    _current_task_id = task_id
    task["started_at"] = time.time()
    task["status"] = "cancelling" if task.get("cancel_requested") else "running"

    try:
        ensure_current_task_not_cancelled(task_id)
        result = func(*args, **kwargs)
        task["status"] = "completed"
        task["result"] = result
    except TaskCancelledError as e:
        task["status"] = "cancelled"
        task["error"] = str(e)
        logger.warning("[API] 任务 %s 已终止: %s", task_id[:8], e)
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        logger.error("[API] 任务 %s 失败: %s", task_id[:8], e)
    finally:
        task["finished_at"] = time.time()
        _current_task_id = None
        _playwright_lock.release()


def _start_task(command: str, func, params: dict, *args, **kwargs) -> dict:
    """创建并启动后台任务，返回任务信息"""
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再试"))
    _playwright_lock.release()

    task_id = uuid.uuid4().hex[:12]
    task = {
        "task_id": task_id,
        "command": command,
        "params": params,
        "status": "pending",
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "result": None,
        "error": None,
        "cancel_requested": False,
        "cancel_requested_at": None,
        "cancel_message": "任务已终止",
    }
    _tasks[task_id] = task
    _prune_tasks()

    thread = threading.Thread(target=_run_task, args=(task_id, func, *args), kwargs=kwargs, daemon=True)
    thread.start()

    return task


# ---------------------------------------------------------------------------
# 响应模型
# ---------------------------------------------------------------------------


class TaskParams(BaseModel):
    target: int = 5


class CleanupParams(BaseModel):
    max_seats: int | None = None


class AdminEmailParams(BaseModel):
    email: str


class AdminSessionParams(BaseModel):
    email: str
    session_token: str


class AdminPasswordParams(BaseModel):
    password: str


class AdminCodeParams(BaseModel):
    code: str


class AdminWorkspaceParams(BaseModel):
    option_id: str


class ManualAccountCallbackParams(BaseModel):
    redirect_url: str


class TeamMemberRemoveParams(BaseModel):
    email: str
    user_id: str
    type: str


def _normalized_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_main_account_email(email: str | None) -> bool:
    from autoteam.admin_state import get_admin_email

    return bool(_normalized_email(email)) and _normalized_email(email) == _normalized_email(get_admin_email())


def _quota_snapshot_status(quota_info: dict | None) -> str:
    if not isinstance(quota_info, dict):
        return ""

    values = []
    for key in ("primary_pct", "weekly_pct"):
        value = quota_info.get(key)
        if isinstance(value, (int, float)):
            values.append(value)

    if not values:
        return ""
    return "exhausted" if any(value >= 100 for value in values) else "active"


def _resolve_status_auth_file(acc: dict) -> str:
    auth_file = (acc.get("auth_file") or "").strip()
    if auth_file and Path(auth_file).exists():
        return auth_file

    if _is_main_account_email(acc.get("email")):
        from autoteam.codex_auth import get_saved_main_auth_file

        saved_auth_file = get_saved_main_auth_file()
        if saved_auth_file and Path(saved_auth_file).exists():
            return saved_auth_file

    return ""


def _display_account_status(acc: dict, quota_snapshot: dict | None = None) -> str:
    from autoteam.accounts import is_account_disabled

    status = acc.get("status", "")
    if not _is_main_account_email(acc.get("email")):
        if is_account_disabled(acc):
            return "disabled"
        return status

    quota_status = _quota_snapshot_status(quota_snapshot) or _quota_snapshot_status(acc.get("last_quota"))
    if quota_status:
        return quota_status

    return "active" if _resolve_status_auth_file(acc) else status


def _sanitize_account(acc: dict, quota_snapshot: dict | None = None) -> dict:
    """脱敏账号信息（去掉 password 等敏感字段）"""
    sanitized = {k: v for k, v in acc.items() if k not in ("password", "cloudmail_account_id", "mail_account_id")}
    sanitized["is_main_account"] = _is_main_account_email(acc.get("email"))
    sanitized["raw_status"] = acc.get("status", "")
    sanitized["status"] = _display_account_status(acc, quota_snapshot)
    return sanitized


def _admin_status():
    from autoteam.admin_state import get_admin_state_summary

    status = get_admin_state_summary()
    status["login_step"] = _admin_login_step
    status["login_in_progress"] = _admin_login_api is not None
    if _admin_login_api:
        status["login_email"] = getattr(_admin_login_api, "login_email", "") or status.get("email", "")
    if _admin_login_api and _admin_login_step == "workspace_required":
        status["workspace_options"] = getattr(_admin_login_api, "workspace_options_cache", []) or []
    else:
        status["workspace_options"] = []
    return status


def _main_codex_status():
    return {
        "in_progress": _main_codex_flow is not None,
        "step": _main_codex_step,
        "action": _main_codex_action,
    }


def _manual_account_status():
    status = {
        "in_progress": False,
        "status": "idle",
        "state": "",
        "auth_url": "",
        "started_at": None,
        "message": "",
        "error": "",
        "account": None,
        "callback_received": False,
        "callback_source": "",
        "auto_callback_available": False,
        "auto_callback_error": "",
    }
    if _manual_account_flow:
        status.update(_manual_account_flow.status())
    return status


def _finish_admin_login(completed: dict):
    global _admin_login_api, _admin_login_step
    api = _admin_login_api
    info = None
    try:
        info = _pw_executor.run(api.complete_admin_login)
    finally:
        if api:
            try:
                _pw_executor.run(api.stop)
            except Exception:
                pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"status": "completed", "admin": _admin_status(), "codex": _main_codex_status(), "info": info}


def _set_pending_admin_login(api, step):
    global _admin_login_api, _admin_login_step
    _admin_login_api = api
    _admin_login_step = step
    return {"status": step, "admin": _admin_status()}


def _screenshot_path(filename: str) -> Path:
    name = Path(filename or "").name
    if not name or name != filename:
        raise HTTPException(status_code=400, detail="无效截图文件名")
    if Path(name).suffix.lower() not in _SCREENSHOT_SUFFIXES:
        raise HTTPException(status_code=400, detail="不支持的截图文件类型")
    path = (SCREENSHOT_DIR / name).resolve()
    root = SCREENSHOT_DIR.resolve()
    if root != path.parent:
        raise HTTPException(status_code=400, detail="无效截图路径")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="截图不存在")
    return path


def _list_screenshots(limit: int = 60):
    if not SCREENSHOT_DIR.exists():
        return []
    files = []
    for path in SCREENSHOT_DIR.iterdir():
        if not path.is_file() or path.suffix.lower() not in _SCREENSHOT_SUFFIXES:
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "url": f"/api/screenshots/{path.name}",
            }
        )
    files.sort(key=lambda item: item["modified_at"], reverse=True)
    return files[: max(1, min(limit, 200))]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _desktop_enabled() -> bool:
    return _env_bool("ENABLE_NOVNC", True)


def _desktop_vnc_host() -> str:
    return os.environ.get("VNC_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _desktop_vnc_port() -> int:
    return _env_int("VNC_PORT", 5900)


def _novnc_web_dir() -> Path:
    return Path(os.environ.get("NOVNC_WEB_DIR", "/usr/share/novnc")).expanduser()


def _request_api_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.query_params.get("key", "")


def _request_public_scheme(request: Request) -> str:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        return forwarded_proto.split(",", 1)[0].strip().lower()
    return str(request.url.scheme or "").lower()


def _encode_desktop_token(token: str) -> str:
    encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def _decode_desktop_token(token: str) -> str:
    if not token:
        return ""
    padding = "=" * (-len(token) % 4)
    try:
        return base64.urlsafe_b64decode(f"{token}{padding}").decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return ""


def _desktop_url_for_request(request: Request) -> str:
    ws_path = "/api/desktop/ws"
    token = _request_api_token(request)
    if API_KEY and token:
        ws_path = f"{ws_path}/{quote(_encode_desktop_token(token), safe='')}"
    params = urlencode(
        {
            "autoconnect": "true",
            "encrypt": "1" if _request_public_scheme(request) == "https" else "0",
            "resize": "remote",
            "reconnect": "true",
            "path": ws_path,
        }
    )
    return f"/desktop/vnc.html?{params}"


def _vnc_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return True
    except OSError:
        return False


def _recent_login_logs(limit: int = 80):
    entries = globals().get("_log_buffer", [])[-limit:]
    keywords = ("[ChatGPT]", "[Codex]", "管理员登录", "admin-login", "login", "OAuth")
    return [entry for entry in entries if any(key in entry.get("message", "") for key in keywords)]


def _local_admin_login_analysis(context: dict):
    snapshot = context.get("snapshot") or {}
    logs = context.get("logs") or []
    body = str(snapshot.get("body") or "").lower()
    url = str(snapshot.get("url") or "").lower()
    step = snapshot.get("step") or context.get("admin_status", {}).get("login_step") or ""
    buttons = ((snapshot.get("dom") or {}).get("buttons") or [])
    inputs = ((snapshot.get("dom") or {}).get("inputs") or [])
    log_messages = [str(item.get("message") or "") for item in logs]

    findings = []
    recommendations = []

    if step == "email_required" and (
        url.endswith("/auth/login")
        or "/auth/login" in url
        or "auth.openai.com/log-in" in url
        or "auth.openai.com/u/login" in url
    ):
        findings.append("当前仍处于邮箱步骤，URL 仍是登录页，说明页面尚未推进到密码、验证码或 workspace。")
        recommendations.append("服务器部署通常看不到 Playwright 窗口；先在面板里打开页面快照确认按钮和输入框，再点“重新识别登录步骤”。")

    if any("clicked=False" in msg and "邮箱已提交" in msg for msg in log_messages):
        findings.append("日志显示邮箱提交时 clicked=False，程序没有确认点击到登录按钮，只是可能回退到按 Enter。")
        recommendations.append("优先检查按钮选择器或页面改版；这通常不是密码/验证码问题。")
    elif any("clicked=True" in msg and "邮箱已提交" in msg for msg in log_messages):
        findings.append("日志显示 clicked=True，程序认为已点击提交按钮，但页面状态仍未推进。")
        recommendations.append("如果手动点击可推进，继续收窄为自动化点击事件、前端校验或风控拦截问题。")

    submit_logs = [item for item in logs if "邮箱已提交" in str(item.get("message") or "")]
    stuck_logs = [item for item in logs if "仍停留在邮箱步骤" in str(item.get("message") or "")]
    if submit_logs and stuck_logs and abs(float(stuck_logs[-1].get("time", 0)) - float(submit_logs[-1].get("time", 0))) <= 2:
        findings.append("邮箱提交日志和停留日志时间非常接近，存在提交后过早检测的迹象。")
        recommendations.append("当前版本已改为等待登录步骤变化后再判断；如果仍复现，应看截图和按钮点击结果。")

    visible_email_inputs = [
        item for item in inputs if item.get("visible") and ("email" in str(item).lower() or "username" in str(item).lower())
    ]
    if step == "email_required" and not visible_email_inputs:
        findings.append("当前判断为邮箱步骤，但 DOM 摘要里没有明显可见邮箱输入框，可能是页面 A/B 改版或输入框被包装。")
        recommendations.append("用面板内页面快照确认是否需要展开邮箱登录入口；如果服务器无法交互，优先改用手动导入 session_token。")

    button_texts = [str(item.get("text") or item.get("ariaLabel") or "") for item in buttons if item.get("visible")]
    if button_texts and not any(text.strip().lower() in {"continue", "log in", "继续"} for text in button_texts):
        findings.append("可见按钮文本里没有精确的 Continue/Log in/继续，旧的精确按钮名匹配可能过严。")
        recommendations.append("当前版本已增加非社交登录按钮的宽松匹配；如果仍失败，查看按钮列表里的实际文本。")

    if "verify you are human" in body or "cloudflare" in body or "challenge" in url:
        findings.append("页面内容或 URL 出现 Cloudflare/challenge 特征，登录可能被人机验证拦截。")
        recommendations.append("服务器无桌面时无法直接点验证；优先在本地浏览器登录后导入 session_token，或给服务器配置 VNC/noVNC 后再人工处理。")

    if "continue with google" in body and "continue chatgpt" in body and step == "email_required":
        findings.append("body 仍是登录首页文案，页面没有进入下一个认证表单。")
        recommendations.append("如果快照显示有 Email address 和 Continue，后端会自动重试填写并点击；如果只有第三方入口，优先导入 session_token。")

    if step in {"password_required", "code_required", "workspace_required", "completed"}:
        findings.append(f"当前已推进到 {step}，不是邮箱提交卡住。")
        recommendations.append("继续按当前步骤提交密码、验证码或 workspace；如果已手动完成，点“重新识别登录步骤”。")

    if not findings:
        findings.append("当前证据不足以锁定单一原因。")
        recommendations.append("先刷新页面快照并查看最新截图，再手动触发一次 AI 分析。")

    return {
        "summary": findings[0],
        "findings": findings,
        "recommendations": recommendations,
    }


def _call_external_ai_login_analysis(context: dict):
    api_key = os.environ.get("AUTOTEAM_AI_ANALYSIS_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None

    import requests

    base_url = (os.environ.get("AUTOTEAM_AI_ANALYSIS_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("AUTOTEAM_AI_ANALYSIS_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    evidence = json.dumps(context, ensure_ascii=False)[:12000]
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": "你是 AutoTeam 登录排障分析器。只基于给定证据判断原因，区分已证实、可能原因和下一步验证，不要编造现场。",
            },
            {
                "role": "user",
                "content": f"分析管理员 ChatGPT 登录卡住原因，并给出优先级排序和下一步验证。证据 JSON：\n{evidence}",
            },
        ],
        "temperature": 0.1,
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def _finish_main_codex_flow():
    global _main_codex_flow, _main_codex_step, _main_codex_action
    flow = _main_codex_flow
    action = _main_codex_action or "sync"
    try:
        info = _pw_executor.run(flow.complete)
    finally:
        if flow:
            try:
                _pw_executor.run(flow.stop)
            except Exception:
                pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    message = "主号 Codex 已同步到已启用远端" if action == "sync" else "主号 Codex 已登录"
    return {
        "status": "completed",
        "message": message,
        "codex": _main_codex_status(),
        "info": info,
    }


def _set_pending_main_codex_flow(flow, step, action):
    global _main_codex_flow, _main_codex_step, _main_codex_action
    _main_codex_flow = flow
    _main_codex_step = step
    _main_codex_action = action
    return {"status": step, "codex": _main_codex_status()}


def _start_main_codex_flow(action="sync"):
    from autoteam.codex_auth import MainCodexLoginFlow, MainCodexSyncFlow

    flow_cls = MainCodexSyncFlow if action == "sync" else MainCodexLoginFlow

    def _do_start():
        return _run_playwright_start(flow_cls, lambda flow: flow.start())

    flow, result = _pw_executor.run(_do_start)
    step = result["step"]
    if step == "completed":
        _set_pending_main_codex_flow(flow, step, action)
        return step, _finish_main_codex_flow()
    if step in ("password_required", "code_required"):
        return step, _set_pending_main_codex_flow(flow, step, action)

    _pw_executor.run(flow.stop)
    raise RuntimeError(result.get("detail") or "无法识别主号 Codex 登录步骤")


def _finish_manual_account_flow(result: dict):
    return {**result, "manual_account": _manual_account_status()}


def _set_pending_manual_account_flow(flow, result):
    global _manual_account_flow
    _manual_account_flow = flow
    return {**result, "manual_account": _manual_account_status()}


# ---------------------------------------------------------------------------
# 同步端点
# ---------------------------------------------------------------------------


@app.get("/api/admin/status")
def get_admin_status():
    """获取管理员登录状态。"""
    return _admin_status()


@app.get("/api/main-codex/status")
def get_main_codex_status():
    """获取主号 Codex 同步状态。"""
    return _main_codex_status()


@app.get("/api/manual-account/status")
def get_manual_account_status():
    """获取手动添加账号状态。"""
    return _manual_account_status()


@app.get("/api/screenshots")
def get_screenshots(limit: int = 60):
    """列出 screenshots/ 目录下的截图。"""
    return {"items": _list_screenshots(limit=limit)}


@app.get("/api/screenshots/{filename}")
def get_screenshot(filename: str):
    """读取 screenshots/ 目录下的单个截图。"""
    return FileResponse(str(_screenshot_path(filename)))


@app.get("/api/desktop/status")
def get_desktop_status(request: Request):
    """返回服务器端 Playwright 远程窗口入口。"""
    configured = _desktop_enabled()
    web_dir = _novnc_web_dir()
    web_available = (web_dir / "vnc.html").is_file()
    public_url = os.environ.get("NOVNC_PUBLIC_URL", "").strip()
    host = _desktop_vnc_host()
    port = _desktop_vnc_port()
    vnc_ready = _vnc_ready(host, port) if configured else False
    embedded_url = _desktop_url_for_request(request) if web_available else ""
    enabled = configured and (web_available or bool(public_url))

    if not configured:
        detail = "远程浏览器窗口未启用，可设置 ENABLE_NOVNC=true 后重启服务"
    elif not (web_available or public_url):
        detail = f"未找到 noVNC 页面目录: {web_dir}"
    elif not vnc_ready:
        detail = "远程窗口服务正在启动或不可达，稍后重试"
    else:
        detail = "远程窗口可用"

    return {
        "enabled": enabled,
        "configured": configured,
        "display": os.environ.get("DISPLAY", ":99"),
        "web_available": web_available,
        "web_dir": str(web_dir),
        "vnc_ready": vnc_ready,
        "vnc_host": host,
        "vnc_port": port,
        "url": public_url or embedded_url,
        "embedded_url": embedded_url,
        "public_url": public_url,
        "password_required": bool(os.environ.get("NOVNC_PASSWORD")),
        "detail": detail,
    }


async def _accept_desktop_websocket(websocket: WebSocket, encoded_token: str = "") -> bool:
    if not API_KEY:
        await websocket.accept(subprotocol=_desktop_websocket_subprotocol(websocket))
        return True
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = _decode_desktop_token(encoded_token) or websocket.query_params.get("key", "")
    if token != API_KEY:
        await websocket.close(code=1008, reason="未授权")
        return False
    await websocket.accept(subprotocol=_desktop_websocket_subprotocol(websocket))
    return True


def _desktop_websocket_subprotocol(websocket: WebSocket) -> str | None:
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    requested = {item.strip() for item in protocols.split(",") if item.strip()}
    return "binary" if "binary" in requested else None


async def _pipe_vnc_to_websocket(reader: asyncio.StreamReader, websocket: WebSocket):
    while True:
        data = await reader.read(65536)
        if not data:
            return
        await websocket.send_bytes(data)


async def _pipe_websocket_to_vnc(websocket: WebSocket, writer: asyncio.StreamWriter):
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            writer.write(message["bytes"])
        elif message.get("text") is not None:
            writer.write(message["text"].encode("latin-1"))
        else:
            continue
        await writer.drain()


async def _desktop_websocket_proxy(websocket: WebSocket, encoded_token: str = ""):
    """把 noVNC WebSocket 流量桥接到容器内 x11vnc。"""
    if not await _accept_desktop_websocket(websocket, encoded_token):
        return
    if not _desktop_enabled():
        await websocket.close(code=1013, reason="远程窗口未启用")
        return

    host = _desktop_vnc_host()
    port = _desktop_vnc_port()
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as exc:
        logger.warning("[API] 远程窗口 VNC 服务不可达: %s:%s, %s", host, port, exc)
        await websocket.close(code=1013, reason="VNC 服务未就绪")
        return

    tasks = {
        asyncio.create_task(_pipe_vnc_to_websocket(reader, websocket)),
        asyncio.create_task(_pipe_websocket_to_vnc(websocket, writer)),
    }
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            with contextlib.suppress(WebSocketDisconnect, ConnectionError, OSError, asyncio.CancelledError):
                task.result()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        with contextlib.suppress(Exception):
            await websocket.close()


@app.websocket("/api/desktop/ws")
async def desktop_websocket(websocket: WebSocket):
    await _desktop_websocket_proxy(websocket)


@app.websocket("/api/desktop/ws/{encoded_token}")
async def desktop_websocket_with_token(websocket: WebSocket, encoded_token: str):
    await _desktop_websocket_proxy(websocket, encoded_token)


@app.post("/api/admin/login/inspect")
def post_admin_login_inspect():
    """手动刷新管理员登录页面快照，用于接管和排障。"""
    if not _admin_login_api:
        return {
            "available": False,
            "reason": "当前没有正在进行的管理员登录流程",
            "admin": _admin_status(),
            "screenshots": _list_screenshots(),
        }
    try:
        snapshot = _pw_executor.run(
            _admin_login_api.capture_login_debug_snapshot,
            "admin_login_debug",
            timeout_seconds=60,
        )
    except Exception as exc:
        logger.exception("[API] 管理员登录页面快照失败")
        raise HTTPException(status_code=500, detail=str(exc))
    return {**snapshot, "admin": _admin_status(), "screenshots": _list_screenshots()}


@app.post("/api/admin/login/refresh")
def post_admin_login_refresh():
    """用户手动接管 Playwright 页面后，重新识别当前登录步骤。"""
    global _admin_login_step
    if not _admin_login_api:
        raise HTTPException(status_code=409, detail="当前没有正在进行的管理员登录流程")
    try:
        result = _pw_executor.run(_admin_login_api.refresh_login_step, timeout_seconds=60)
    except Exception as exc:
        logger.exception("[API] 重新识别管理员登录步骤失败")
        raise HTTPException(status_code=500, detail=str(exc))

    step = result.get("step")
    if step == "completed":
        return _finish_admin_login(result)
    if step in ("email_required", "password_required", "code_required", "workspace_required"):
        _admin_login_step = step
        return _set_pending_admin_login(_admin_login_api, step)
    if step == "error":
        raise HTTPException(status_code=400, detail=result.get("detail") or "登录页进入错误状态")
    return {"status": step or "unknown", "detail": result.get("detail"), "admin": _admin_status()}


@app.post("/api/admin/login/analyze")
def post_admin_login_analyze():
    """手动触发一次管理员登录卡住原因分析；自动流程不会调用该接口。"""
    snapshot = {}
    external_error = ""
    if _admin_login_api:
        try:
            snapshot = _pw_executor.run(
                _admin_login_api.capture_login_debug_snapshot,
                "admin_login_ai_analysis",
                timeout_seconds=60,
            )
        except Exception as exc:
            logger.warning("[API] AI 分析前采集页面快照失败: %s", exc)
            snapshot = {"available": False, "reason": str(exc)}
    else:
        snapshot = {"available": False, "reason": "当前没有正在进行的管理员登录流程"}

    context = {
        "manual_trigger": True,
        "admin_status": _admin_status(),
        "snapshot": snapshot,
        "logs": _recent_login_logs(),
        "screenshots": _list_screenshots(limit=20),
    }
    local_analysis = _local_admin_login_analysis(context)
    try:
        external_text = _call_external_ai_login_analysis(context)
    except Exception as exc:
        logger.warning("[API] 外部 AI 登录分析失败，回退本地规则: %s", exc)
        external_text = ""
        external_error = str(exc)

    if external_text:
        return {
            "engine": "openai-compatible",
            "manual_trigger": True,
            "analysis": external_text,
            "local_analysis": local_analysis,
            "context": context,
        }

    return {
        "engine": "local-rules",
        "manual_trigger": True,
        "analysis": local_analysis,
        "external_error": external_error,
        "context": context,
    }


@app.post("/api/admin/login/start")
def post_admin_login_start(params: AdminEmailParams):
    """开始管理员登录流程。"""
    global _admin_login_api, _admin_login_step

    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再进行管理员登录")
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        login_email = params.email.strip()
        logger.info("[API] 开始管理员登录: %s", login_email)

        api = ChatGPTTeamAPI()
        api.login_email = login_email
        _admin_login_api = api
        _admin_login_step = "starting"

        def _do_start(email):
            try:
                return api.begin_admin_login(email)
            except Exception:
                _stop_playwright_resource(api)
                raise

        result = _pw_executor.run(
            _do_start,
            login_email,
            timeout_seconds=ADMIN_LOGIN_START_TIMEOUT_SECONDS,
        )
        step = result["step"]
        logger.info("[API] 管理员登录 start 返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            _admin_login_api = api
            return _finish_admin_login(result)
        if step in ("email_required", "password_required", "code_required", "workspace_required"):
            return _set_pending_admin_login(api, step)
        _pw_executor.run(api.stop)
        _admin_login_api = None
        _admin_login_step = None
        _playwright_lock.release()
        raise HTTPException(status_code=400, detail=result.get("detail") or "无法识别管理员登录步骤")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员登录 start 失败")
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/session")
def post_admin_login_session(params: AdminSessionParams):
    """手动导入管理员 session_token。"""
    global _admin_login_api, _admin_login_step

    if _admin_login_api:
        post_admin_login_cancel()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail=_current_busy_detail("有任务正在执行，请等待完成后再导入管理员 session_token"),
        )

    try:
        from autoteam.chatgpt_api import ChatGPTTeamAPI

        logger.info("[API] 导入管理员 session_token: %s", params.email.strip())

        def _do_import(email, session_token):
            api = ChatGPTTeamAPI()
            try:
                return api.import_admin_session(email, session_token)
            finally:
                api.stop()

        info = _pw_executor.run(_do_import, params.email.strip(), params.session_token.strip())
        _admin_login_api = None
        _admin_login_step = None
        return {"status": "completed", "admin": _admin_status(), "codex": _main_codex_status(), "info": info}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 导入管理员 session_token 失败")
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if _playwright_lock.locked():
            _playwright_lock.release()


@app.post("/api/admin/login/password")
def post_admin_login_password(params: AdminPasswordParams):
    """提交管理员密码。"""
    global _admin_login_api, _admin_login_step
    if not _admin_login_api or _admin_login_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的管理员登录流程")

    try:
        logger.info("[API] 提交管理员密码 | current_step=%s", _admin_login_step)
        result = _pw_executor.run(_admin_login_api.submit_admin_password, params.password)
        step = result["step"]
        logger.info("[API] 管理员密码提交返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员密码提交失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/code")
def post_admin_login_code(params: AdminCodeParams):
    """提交管理员验证码。"""
    global _admin_login_api, _admin_login_step
    if not _admin_login_api or _admin_login_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的管理员登录流程")

    try:
        logger.info("[API] 提交管理员验证码 | current_step=%s code_len=%d", _admin_login_step, len(params.code.strip()))
        result = _pw_executor.run(_admin_login_api.submit_admin_code, params.code.strip())
        step = result["step"]
        logger.info("[API] 管理员验证码提交返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员验证码提交失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/workspace")
def post_admin_login_workspace(params: AdminWorkspaceParams):
    """提交管理员 workspace 选择。"""
    global _admin_login_api, _admin_login_step
    if not _admin_login_api or _admin_login_step != "workspace_required":
        raise HTTPException(status_code=409, detail="当前没有等待组织选择的管理员登录流程")

    try:
        logger.info("[API] 提交管理员 workspace 选择 | option_id=%s", params.option_id)
        result = _pw_executor.run(_admin_login_api.select_workspace_option, params.option_id)
        step = result["step"]
        logger.info("[API] 管理员 workspace 选择返回: step=%s detail=%s", step, result.get("detail"))
        if step == "completed":
            return _finish_admin_login(result)
        if step in ("password_required", "code_required", "workspace_required"):
            _admin_login_step = step
            return {"status": step, "admin": _admin_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "管理员组织选择失败")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[API] 管理员 workspace 选择失败")
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/admin/login/cancel")
def post_admin_login_cancel():
    """取消管理员登录流程。"""
    global _admin_login_api, _admin_login_step
    if _admin_login_api:
        try:
            _pw_executor.run(_admin_login_api.stop)
        except Exception:
            pass
        _admin_login_api = None
        _admin_login_step = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"message": "管理员登录已取消", "admin": _admin_status()}


@app.post("/api/admin/logout")
def post_admin_logout():
    """清除已保存的管理员登录态。"""
    from autoteam.admin_state import clear_admin_state

    if _admin_login_api:
        post_admin_login_cancel()
    clear_admin_state()
    return {"message": "管理员登录态已清除", "admin": _admin_status()}


@app.post("/api/main-codex/start")
def post_main_codex_start():
    """开始主号 Codex 登录并同步到已启用远端。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    _require_sync_target_configs("同步主号 Codex")

    from autoteam.codex_auth import get_saved_main_auth_file
    from autoteam.sync_targets import sync_main_codex_to_configured_targets

    saved_auth_file = get_saved_main_auth_file()
    if saved_auth_file:
        sync_main_codex_to_configured_targets(saved_auth_file)
        return {
            "status": "completed",
            "message": "主号 Codex 已同步到已启用远端",
            "codex": _main_codex_status(),
            "info": {"auth_file": saved_auth_file},
        }

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步主号 Codex")
        )

    try:
        _step, result = _start_main_codex_flow(action="sync")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/login")
def post_main_codex_login():
    """开始主号 Codex 登录，仅保存本地认证文件。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action

    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再登录主号 Codex")
        )

    try:
        _step, result = _start_main_codex_flow(action="login")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/password")
def post_main_codex_password(params: AdminPasswordParams):
    """提交主号 Codex 登录密码。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if not _main_codex_flow or _main_codex_step != "password_required":
        raise HTTPException(status_code=409, detail="当前没有等待密码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_password, params.password)
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_flow()
        if step in ("password_required", "code_required"):
            _main_codex_step = step
            return {"status": step, "codex": _main_codex_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "主号 Codex 密码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/code")
def post_main_codex_code(params: AdminCodeParams):
    """提交主号 Codex 登录验证码。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if not _main_codex_flow or _main_codex_step != "code_required":
        raise HTTPException(status_code=409, detail="当前没有等待验证码的主号 Codex 登录流程")

    try:
        result = _pw_executor.run(_main_codex_flow.submit_code, params.code.strip())
        step = result["step"]
        if step == "completed":
            return _finish_main_codex_flow()
        if step in ("password_required", "code_required"):
            _main_codex_step = step
            return {"status": step, "codex": _main_codex_status()}
        raise HTTPException(status_code=400, detail=result.get("detail") or "主号 Codex 验证码登录失败")
    except HTTPException:
        raise
    except Exception as exc:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/main-codex/cancel")
def post_main_codex_cancel():
    """取消主号 Codex 登录流程。"""
    global _main_codex_flow, _main_codex_step, _main_codex_action
    if _main_codex_flow:
        try:
            _pw_executor.run(_main_codex_flow.stop)
        except Exception:
            pass
        _main_codex_flow = None
        _main_codex_step = None
        _main_codex_action = None
        if _playwright_lock.locked():
            _playwright_lock.release()
    return {"message": "主号 Codex 登录已取消", "codex": _main_codex_status()}


def _delete_main_codex_from_enabled_targets():
    from autoteam.sync_targets import (
        delete_main_codex_from_configured_targets,
        describe_sync_targets,
        get_enabled_sync_targets,
    )

    _require_sync_target_configs("删除主号 Codex 远端文件")

    env = _current_runtime_env()
    enabled_targets = get_enabled_sync_targets(env)
    results = delete_main_codex_from_configured_targets()
    deleted: list[str] = []
    total_count = 0
    failed_targets: list[str] = []

    for target in enabled_targets:
        target_result = results.get(target) or {}
        target_deleted = [str(item) for item in (target_result.get("deleted") or [])]
        deleted.extend(target_deleted)
        if str(target_result.get("error") or "").strip():
            failed_targets.append(target)

        try:
            target_count = int(target_result.get("count", len(target_deleted)) or 0)
        except (TypeError, ValueError):
            target_count = len(target_deleted)
        total_count += target_count

    target_label = describe_sync_targets(enabled_targets)
    message = f"已从 {target_label} 删除 {total_count} 个主号认证文件"
    if failed_targets:
        message += f"（{describe_sync_targets(failed_targets)} 清理失败，详情见 results）"
    return {
        "message": message,
        "deleted": deleted,
        "results": results,
    }


@app.post("/api/main-codex/delete-remote-files")
def post_main_codex_delete_remote_files():
    """删除已启用远端中已上传的主号 Codex 认证文件。"""
    return _delete_main_codex_from_enabled_targets()


@app.post("/api/main-codex/delete-cpa")
def post_main_codex_delete_cpa():
    """兼容旧接口：删除已启用远端中的主号 Codex 认证文件。"""
    return _delete_main_codex_from_enabled_targets()


@app.post("/api/manual-account/start")
def post_manual_account_start():
    """开始手动添加账号流程，返回 OAuth 链接。"""
    global _manual_account_flow

    if _manual_account_flow:
        try:
            _manual_account_flow.stop()
        except Exception:
            pass
        _manual_account_flow = None

    try:
        from autoteam.manual_account import ManualAccountFlow

        flow = ManualAccountFlow()
        result = flow.start()
        return _set_pending_manual_account_flow(flow, result)
    except HTTPException:
        raise
    except Exception as exc:
        if _manual_account_flow:
            try:
                _manual_account_flow.stop()
            except Exception:
                pass
            _manual_account_flow = None
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/manual-account/callback")
def post_manual_account_callback(params: ManualAccountCallbackParams):
    """提交 OAuth 回调 URL，完成手动添加账号。"""
    global _manual_account_flow
    if not _manual_account_flow:
        raise HTTPException(status_code=409, detail="当前没有等待回调的手动添加账号流程")

    try:
        result = _manual_account_flow.submit_callback(params.redirect_url)
        return _finish_manual_account_flow(result)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/manual-account/cancel")
def post_manual_account_cancel():
    """取消手动添加账号流程。"""
    global _manual_account_flow
    if _manual_account_flow:
        try:
            _manual_account_flow.stop()
        except Exception:
            pass
        _manual_account_flow = None
    return {"message": "手动添加账号流程已取消", "manual_account": _manual_account_status()}


@app.get("/api/accounts")
def get_accounts():
    """获取所有账号列表"""
    from autoteam.accounts import load_accounts

    accounts = load_accounts()
    return [_sanitize_account(a) for a in accounts]


@app.get("/api/accounts/{email}/codex-auth")
def get_codex_auth(email: str):
    """导出账号的 Codex CLI 格式认证文件（~/.codex/auth.json）"""
    from autoteam.accounts import find_account, load_accounts
    from autoteam.codex_auth import get_saved_main_auth_file

    email = email.strip().lower()
    auth_file = ""

    if _is_main_account_email(email):
        auth_file = get_saved_main_auth_file()
        if not auth_file or not Path(auth_file).exists():
            raise HTTPException(status_code=404, detail="主号没有可导出的认证文件")
    else:
        acc = find_account(load_accounts(), email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        auth_file = acc.get("auth_file") or ""
        if not auth_file or not Path(auth_file).exists():
            raise HTTPException(status_code=404, detail="该账号没有认证文件")

    auth_data = json.loads(Path(auth_file).read_text())

    # 转换为 Codex CLI 的 auth.json 格式
    codex_auth = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": auth_data.get("id_token", ""),
            "access_token": auth_data.get("access_token", ""),
            "refresh_token": auth_data.get("refresh_token", ""),
            "account_id": auth_data.get("account_id", ""),
        },
        "last_refresh": auth_data.get("last_refresh", ""),
    }

    return {
        "email": email,
        "codex_auth": codex_auth,
        "hint": "将内容保存到 ~/.codex/auth.json（Linux/macOS）或 %APPDATA%\\codex\\auth.json（Windows）",
    }


@app.get("/api/accounts/active")
def get_active():
    """获取活跃账号"""
    from autoteam.accounts import get_active_accounts

    return [_sanitize_account(a) for a in get_active_accounts()]


@app.get("/api/accounts/standby")
def get_standby():
    """获取待命账号"""
    from autoteam.accounts import get_standby_accounts

    accounts = get_standby_accounts()
    return [_sanitize_account(a) for a in accounts]


@app.delete("/api/accounts/{email}")
def delete_account(email: str):
    """删除本地管理账号及其关联资源。"""
    if not _playwright_lock.acquire(blocking=False):
        running = _tasks.get(_current_task_id, {})
        raise HTTPException(
            status_code=409,
            detail={
                "message": "有任务正在执行，请等待完成后再删除账号",
                "running_task": {
                    "task_id": _current_task_id,
                    "command": running.get("command", "unknown"),
                    "started_at": running.get("started_at"),
                },
            },
        )

    try:
        from autoteam.account_ops import delete_managed_account
        from autoteam.accounts import load_accounts

        if _is_main_account_email(email):
            raise HTTPException(status_code=400, detail="主号不允许删除")

        accounts = load_accounts()
        if not any(a["email"].lower() == email.lower() for a in accounts):
            raise HTTPException(status_code=404, detail="账号不存在")

        cleanup = _pw_executor.run(delete_managed_account, email)
        message = "账号删除完成"
        remote_errors = cleanup.get("remote_errors") or {}
        if remote_errors:
            from autoteam.sync_targets import describe_sync_targets

            message = (
                f"账号删除完成（{describe_sync_targets(list(remote_errors))} 远端清理失败，"
                "详情见 cleanup.remote_errors）"
            )
        return {
            "message": message,
            "deleted_email": email,
            "cleanup": cleanup,
        }
    finally:
        _playwright_lock.release()


def _toggle_account_disabled(email: str, disabled: bool):
    from autoteam.accounts import find_account, load_accounts, update_account

    email = email.strip().lower()
    if _is_main_account_email(email):
        raise HTTPException(status_code=400, detail="主号不允许禁用或启用")

    accounts = load_accounts()
    acc = find_account(accounts, email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")

    update_account(email, disabled=disabled)
    refreshed = find_account(load_accounts(), email)
    return {
        "message": f"已{'禁用' if disabled else '启用'} {email}",
        "email": email,
        "disabled": bool(disabled),
        "account": _sanitize_account(refreshed or {**acc, "disabled": disabled}),
    }


def _toggle_accounts_disabled(emails: list[str], disabled: bool):
    from autoteam.accounts import load_accounts, save_accounts

    normalized_emails = []
    seen = set()
    for value in emails or []:
        email = _normalized_email(value)
        if not email or email in seen:
            continue
        seen.add(email)
        normalized_emails.append(email)

    if not normalized_emails:
        raise HTTPException(status_code=400, detail="请至少提供一个有效邮箱")

    accounts = load_accounts()
    by_email = {(_normalized_email(acc.get("email"))): acc for acc in accounts if acc.get("email")}

    updated = []
    unchanged = []
    missing = []
    skipped_main = []

    for email in normalized_emails:
        if _is_main_account_email(email):
            skipped_main.append(email)
            continue
        acc = by_email.get(email)
        if not acc:
            missing.append(email)
            continue
        if bool(acc.get("disabled", False)) == bool(disabled):
            unchanged.append(email)
            continue
        acc["disabled"] = bool(disabled)
        updated.append(email)

    if updated:
        save_accounts(accounts)
        accounts = load_accounts()
        by_email = {(_normalized_email(acc.get("email"))): acc for acc in accounts if acc.get("email")}

    action = "禁用" if disabled else "启用"
    parts = [f"已{action} {len(updated)} 个账号"]
    if unchanged:
        parts.append(f"{len(unchanged)} 个已是目标状态")
    if skipped_main:
        parts.append(f"跳过主号 {len(skipped_main)} 个")
    if missing:
        parts.append(f"未找到 {len(missing)} 个")

    return {
        "message": "，".join(parts),
        "disabled": bool(disabled),
        "updated_count": len(updated),
        "updated_emails": updated,
        "unchanged_emails": unchanged,
        "skipped_main_accounts": skipped_main,
        "missing_emails": missing,
        "accounts": [_sanitize_account(by_email[email]) for email in updated if email in by_email],
    }


class BulkAccountDisableParams(BaseModel):
    emails: list[str]


@app.post("/api/accounts/bulk/disable")
def post_bulk_disable_accounts(params: BulkAccountDisableParams):
    """批量禁用账号：保留本地记录，但自动轮转/巡检/同步会跳过这些账号。"""
    return _toggle_accounts_disabled(params.emails, True)


@app.post("/api/accounts/bulk/enable")
def post_bulk_enable_accounts(params: BulkAccountDisableParams):
    """批量启用账号：恢复这些账号参与自动轮转/巡检/同步。"""
    return _toggle_accounts_disabled(params.emails, False)


@app.post("/api/accounts/{email}/disable")
def post_disable_account(email: str):
    """禁用账号：保留本地记录，但自动轮转/巡检/同步会跳过该账号。"""
    return _toggle_account_disabled(email, True)


@app.post("/api/accounts/{email}/enable")
def post_enable_account(email: str):
    """启用账号：恢复参与自动轮转/巡检/同步。"""
    return _toggle_account_disabled(email, False)


class PriorityParams(BaseModel):
    priority: int | None = None


@app.patch("/api/accounts/{email}/priority")
def patch_account_priority(email: str, params: PriorityParams):
    """更新账号同步优先级。priority 为 null 时清除自定义值，恢复自动计算。"""
    from autoteam.accounts import find_account, load_accounts, update_account

    email = email.strip().lower()
    if _is_main_account_email(email):
        raise HTTPException(status_code=400, detail="主号优先级由系统自动管理，不允许手动设置")
    accounts = load_accounts()
    acc = find_account(accounts, email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    update_account(email, priority=params.priority)
    label = f"priority={params.priority}" if params.priority is not None else "priority=auto"
    return {"message": f"已更新 {email} 优先级: {label}", "email": email, "priority": params.priority}


@app.post("/api/accounts/{email}/kick")
def post_kick_account(email: str):
    """将账号从 Team 中移出，状态变为 standby"""
    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account
        from autoteam.manager import remove_from_team

        email = email.strip().lower()
        if _is_main_account_email(email):
            raise HTTPException(status_code=400, detail="主号不允许移出 Team")
        accounts = load_accounts()
        acc = find_account(accounts, email)
        if not acc:
            raise HTTPException(status_code=404, detail="账号不存在")
        if acc["status"] not in ("active", "auth_pending", "exhausted"):
            raise HTTPException(status_code=400, detail=f"账号状态为 {acc['status']}，当前不在 Team 占位状态")

        def _do_kick():
            return _run_with_chatgpt_session(lambda chatgpt: remove_from_team(chatgpt, email))

        ok = _pw_executor.run(_do_kick)
        if ok:
            update_account(email, status="standby")
            return {"message": f"已将 {email} 移出 Team", "email": email, "status": "standby"}
        raise HTTPException(status_code=500, detail=f"移出 {email} 失败")
    finally:
        _playwright_lock.release()


class LoginAccountParams(BaseModel):
    email: str


@app.post("/api/accounts/login", status_code=202)
def post_account_login(params: LoginAccountParams):
    """触发单个账号的 Codex 登录（后台执行）"""
    from autoteam.accounts import find_account, load_accounts

    email = params.email.strip().lower()
    if _is_main_account_email(email):
        raise HTTPException(status_code=400, detail="主号不属于账号池登录对象")
    accounts = load_accounts()
    acc = find_account(accounts, email)
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    _require_account_mail_configs(acc, "登录账号")
    _require_sync_target_configs("登录账号")

    def _run():
        from autoteam.accounts import STATUS_ACTIVE, update_account
        from autoteam.codex_auth import (
            check_codex_quota,
            login_codex_via_browser,
            quota_result_quota_info,
            quota_result_resets_at,
            save_auth_file,
        )
        from autoteam.mail_provider import get_mail_client_for_account

        mail_client = get_mail_client_for_account(acc)
        mail_client.login()
        bundle = login_codex_via_browser(email, acc.get("password", ""), mail_client=mail_client)
        if bundle:
            plan_type = str(bundle.get("plan_type") or "").lower()
            if plan_type != "team":
                raise RuntimeError(f"登录后 plan={plan_type or 'unknown'}，未进入 Team workspace")
            auth_file = save_auth_file(bundle)
            update_account(email, auth_file=auth_file)
            # 登录成功且是 team plan，自动标记为 active
            if plan_type == "team":
                update_account(email, status=STATUS_ACTIVE, last_active_at=time.time())
                # 查一下额度并保存快照
                token = bundle.get("access_token")
                if token:
                    st, info = check_codex_quota(token)
                    if st == "ok" and isinstance(info, dict):
                        update_account(email, last_quota=info)
                    elif st == "exhausted":
                        quota_info = quota_result_quota_info(info)
                        if quota_info:
                            update_account(email, last_quota=quota_info)
                        update_account(
                            email,
                            status="exhausted",
                            quota_exhausted_at=time.time(),
                            quota_resets_at=quota_result_resets_at(info) or int(time.time() + 18000),
                        )
            # 同步到已启用远端
            from autoteam.sync_targets import sync_to_configured_targets as sync_to_cpa

            sync_to_cpa()
            return {"email": email, "plan": bundle.get("plan_type"), "auth_file": auth_file}
        raise RuntimeError(f"Codex 登录失败: {email}")

    task = _start_task(f"login:{email}", _run, {"email": email})
    return task


@app.get("/api/status")
def get_status():
    """获取所有账号状态 + active 账号实时额度"""
    from autoteam.accounts import (
        STATUS_ACTIVE,
        STATUS_AUTH_PENDING,
        STATUS_EXHAUSTED,
        STATUS_PENDING,
        STATUS_STANDBY,
        is_account_disabled,
        load_accounts,
    )
    from autoteam.codex_auth import check_codex_quota, quota_result_quota_info

    accounts = load_accounts()
    quota_cache = {}

    for acc in accounts:
        if not _is_main_account_email(acc.get("email")) and is_account_disabled(acc):
            continue
        if acc["status"] not in (STATUS_ACTIVE, STATUS_AUTH_PENDING) and not _is_main_account_email(acc.get("email")):
            continue

        auth_file = _resolve_status_auth_file(acc)
        if not auth_file:
            continue

        try:
            auth_data = json.loads(read_text(Path(auth_file)))
            access_token = auth_data.get("access_token")
            if access_token:
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    quota_cache[acc["email"]] = info
                elif status == "exhausted":
                    quota_info = quota_result_quota_info(info)
                    if quota_info:
                        quota_cache[acc["email"]] = quota_info
        except Exception:
            pass

    sanitized_accounts = [_sanitize_account(a, quota_cache.get(a.get("email"))) for a in accounts]

    summary = {
        "active": sum(1 for a in sanitized_accounts if a["status"] == STATUS_ACTIVE),
        "auth_pending": sum(1 for a in sanitized_accounts if a["status"] == STATUS_AUTH_PENDING),
        "standby": sum(1 for a in sanitized_accounts if a["status"] == STATUS_STANDBY),
        "exhausted": sum(1 for a in sanitized_accounts if a["status"] == STATUS_EXHAUSTED),
        "pending": sum(1 for a in sanitized_accounts if a["status"] == STATUS_PENDING),
        "disabled": sum(1 for a in sanitized_accounts if a["status"] == "disabled"),
        "total": len(sanitized_accounts),
    }

    return {
        "accounts": sanitized_accounts,
        "summary": summary,
        "quota_cache": quota_cache,
    }


@app.post("/api/sync")
def post_sync():
    """同步认证文件到已启用远端。"""
    from autoteam.sync_targets import describe_sync_targets, get_enabled_sync_targets, sync_to_configured_targets

    _require_sync_target_configs("同步远端")
    targets = get_enabled_sync_targets()
    result = sync_to_configured_targets()
    return {"message": f"已同步到 {describe_sync_targets(targets)}", "result": result}


@app.post("/api/sync/from-cpa")
def post_sync_from_cpa():
    """从 CPA 反向同步认证文件到本地。"""
    _require_cpa_configs("拉取 CPA")

    from autoteam.cpa_sync import sync_from_cpa

    result = sync_from_cpa()
    return {"message": "已从 CPA 同步到本地", "result": result}


@app.post("/api/sync/accounts")
def post_sync_accounts():
    """从 auths 目录和 Team 成员同步账号到 accounts.json"""
    from autoteam.manager import sync_account_states

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再同步"))

    try:
        _pw_executor.run(sync_account_states)
    finally:
        _playwright_lock.release()

    from autoteam.accounts import load_accounts

    accounts = load_accounts()
    return {"message": f"同步完成，共 {len(accounts)} 个账号", "total": len(accounts)}


@app.get("/api/team/members")
def get_team_members():
    """获取 Team 全部成员（包括手动添加的外部成员）"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not get_admin_session_token() or not get_chatgpt_account_id():
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再查询"))

    try:

        def _fetch_team_members():
            from autoteam.account_ops import fetch_team_state
            from autoteam.accounts import load_accounts

            def _collect(chatgpt):
                members, invites = fetch_team_state(chatgpt)
                local_emails = {a["email"].lower() for a in load_accounts()}

                result = []
                for m in members:
                    email = (m.get("email") or "").lower()
                    result.append(
                        {
                            "email": m.get("email", ""),
                            "role": m.get("role", ""),
                            "user_id": m.get("user_id") or m.get("id", ""),
                            "is_local": email in local_emails,
                            "type": "member",
                        }
                    )
                for inv in invites:
                    email = (inv.get("email_address") or inv.get("email") or "").lower()
                    result.append(
                        {
                            "email": email,
                            "role": inv.get("role", ""),
                            "user_id": inv.get("id", ""),
                            "is_local": email in local_emails,
                            "type": "invite",
                        }
                    )
                return {"members": result, "total": len(members), "invites": len(invites)}

            return _run_with_chatgpt_session(_collect)

        try:
            return _pw_executor.run(_fetch_team_members)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[API] 获取 Team 成员失败")
            raise HTTPException(status_code=502, detail=str(exc))
    finally:
        _playwright_lock.release()


@app.post("/api/team/members/remove")
def post_team_member_remove(params: TeamMemberRemoveParams):
    """移出 Team 成员或取消邀请。"""
    from autoteam.admin_state import get_admin_session_token, get_chatgpt_account_id

    if not get_admin_session_token() or not get_chatgpt_account_id():
        raise HTTPException(status_code=400, detail="请先完成管理员登录")

    if not _playwright_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_current_busy_detail("有任务正在执行，请等待完成后再操作"))

    try:
        from autoteam.accounts import find_account, load_accounts, update_account

        email = params.email.strip().lower()
        user_id = params.user_id.strip()
        member_type = params.type.strip().lower()

        if not email or not user_id:
            raise HTTPException(status_code=400, detail="缺少必要参数")
        if _is_main_account_email(email):
            raise HTTPException(status_code=400, detail="主号不允许从 Team 成员页移出")
        if member_type not in ("member", "invite"):
            raise HTTPException(status_code=400, detail="无效的成员类型")

        account_id = get_chatgpt_account_id()

        def _do_remove_team_member():
            def _remove(chatgpt):
                if member_type == "invite":
                    path = f"/backend-api/accounts/{account_id}/invites/{user_id}"
                    action_text = "取消邀请"
                else:
                    path = f"/backend-api/accounts/{account_id}/users/{user_id}"
                    action_text = "移出 Team"

                result = chatgpt._api_fetch("DELETE", path)
                return result, action_text

            return _run_with_chatgpt_session(_remove)

        try:
            result, action_text = _pw_executor.run(_do_remove_team_member)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("[API] Team 成员移除失败")
            raise HTTPException(status_code=502, detail=str(exc))
        if result["status"] not in (200, 204):
            raise HTTPException(status_code=500, detail=f"{action_text}失败: HTTP {result['status']}")

        accounts = load_accounts()
        acc = find_account(accounts, email)
        if acc:
            update_account(email, status="standby")

        return {
            "message": f"已{action_text}: {email}",
            "email": email,
            "type": member_type,
        }
    finally:
        _playwright_lock.release()


# ---------------------------------------------------------------------------
# 日志收集
# ---------------------------------------------------------------------------

_log_buffer: list[dict] = []
_LOG_BUFFER_MAX = 500


class _LogCollector(logging.Handler):
    """收集日志到内存 buffer，供前端查询"""

    def emit(self, record):
        entry = {
            "time": record.created,
            "level": record.levelname,
            "message": self.format(record),
        }
        _log_buffer.append(entry)
        if len(_log_buffer) > _LOG_BUFFER_MAX:
            del _log_buffer[: len(_log_buffer) - _LOG_BUFFER_MAX]


_log_collector = _LogCollector()
_log_collector.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_collector)


@app.get("/api/logs")
def get_logs(limit: int = 100, since: float = 0):
    """获取最近的日志"""
    if since > 0:
        entries = [e for e in _log_buffer if e["time"] > since]
    else:
        entries = _log_buffer[-limit:]
    return {"logs": entries, "total": len(_log_buffer)}


@app.post("/api/sync/main-codex")
def post_sync_main_codex():
    """兼容旧接口：开始主号 Codex 登录并同步到已启用远端。"""
    return post_main_codex_start()


@app.get("/api/cpa/files")
def get_cpa_files():
    """获取 CPA 中的认证文件列表"""
    _require_cpa_configs("查看 CPA 文件")

    from autoteam.cpa_sync import list_cpa_files

    return list_cpa_files()


# ---------------------------------------------------------------------------
# 后台任务端点
# ---------------------------------------------------------------------------


@app.post("/api/tasks/check", status_code=202)
def post_check():
    """检查所有 active 账号额度（后台执行）"""
    from autoteam.manager import cmd_check

    def _run():
        exhausted = cmd_check(force_auth_repair=True)
        return {"exhausted": [a["email"] for a in exhausted]}

    task = _start_task("check", _run, {})
    return task


@app.post("/api/tasks/rotate", status_code=202)
def post_rotate(params: TaskParams = TaskParams()):
    """智能轮转（后台执行）"""
    _require_pool_operation_configs("智能轮转")

    from autoteam.manager import cmd_rotate

    task = _start_task(
        "rotate",
        lambda target: cmd_rotate(target, force_auth_repair=True),
        {"target": params.target},
        params.target,
    )
    return task


@app.post("/api/tasks/add", status_code=202)
def post_add():
    """添加新账号（后台执行）"""
    _require_pool_operation_configs("添加新账号")

    from autoteam.manager import cmd_add

    task = _start_task("add", cmd_add, {})
    return task


@app.post("/api/tasks/fill", status_code=202)
def post_fill(params: TaskParams = TaskParams()):
    """补满 Team 成员（后台执行）"""
    _require_pool_operation_configs("补满 Team 成员")

    from autoteam.manager import cmd_fill

    task = _start_task("fill", cmd_fill, {"target": params.target}, params.target)
    return task


@app.post("/api/tasks/cleanup", status_code=202)
def post_cleanup(params: CleanupParams = CleanupParams()):
    """清理多余成员（后台执行）"""
    from autoteam.manager import cmd_cleanup

    task = _start_task("cleanup", cmd_cleanup, {"max_seats": params.max_seats}, params.max_seats)
    return task


@app.post("/api/tasks/reset-quota", status_code=202)
def post_reset_quota():
    """清空本地额度恢复记录，并恢复 exhausted 账号为可检查状态（后台执行）"""
    from autoteam.manager import cmd_reset_quota_recovery

    task = _start_task("reset-quota", cmd_reset_quota_recovery, {})
    return task


@app.get("/api/tasks")
def get_tasks():
    """查看所有任务"""
    sorted_tasks = sorted(_tasks.values(), key=lambda t: t["created_at"], reverse=True)
    return sorted_tasks


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    """查看任务状态"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """请求终止后台任务。"""
    task = _tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] in _TASK_TERMINAL_STATUSES:
        return {
            "task_id": task_id,
            "status": task["status"],
            "message": "任务已结束，无需终止",
            "task": task,
        }

    if not task.get("cancel_requested"):
        task["cancel_requested"] = True
        task["cancel_requested_at"] = time.time()
        if task["status"] in ("pending", "running"):
            task["status"] = "cancelling"
        task["error"] = "任务终止中"
        logger.warning("[API] 已请求终止任务 %s (%s)", task_id[:8], task.get("command", "unknown"))

    return {
        "task_id": task_id,
        "status": task["status"],
        "message": "已发送终止请求，任务会在安全检查点停止",
        "task": task,
    }


# ---------------------------------------------------------------------------
# 后台自动巡检
# ---------------------------------------------------------------------------

from autoteam.config import (
    AUTO_CHECK_ADD_PHONE_MAX_RETRIES as _DEFAULT_ADD_PHONE_MAX_RETRIES,
)
from autoteam.config import (
    AUTO_CHECK_INTERVAL as _DEFAULT_INTERVAL,
)
from autoteam.config import (
    AUTO_CHECK_MIN_LOW as _DEFAULT_MIN_LOW,
)
from autoteam.config import (
    AUTO_CHECK_RETRY_ADD_PHONE as _DEFAULT_RETRY_ADD_PHONE,
)
from autoteam.config import (
    AUTO_CHECK_TARGET_SEATS as _DEFAULT_TARGET_SEATS,
)
from autoteam.config import (
    AUTO_CHECK_THRESHOLD as _DEFAULT_THRESHOLD,
)

# 运行时可修改的巡检配置
_auto_check_config = {
    "interval": _DEFAULT_INTERVAL,
    "target_seats": _DEFAULT_TARGET_SEATS,
    "threshold": _DEFAULT_THRESHOLD,
    "min_low": _DEFAULT_MIN_LOW,
    "retry_add_phone": _DEFAULT_RETRY_ADD_PHONE,
    "add_phone_max_retries": _DEFAULT_ADD_PHONE_MAX_RETRIES,
}
_auto_check_stop = threading.Event()
_auto_check_restart = threading.Event()  # 配置变更时通知线程重启


def _playwright_probe_command(*args: str) -> list[str]:
    return [sys.executable, "-m", "autoteam.playwright_probe", *args]


def _kill_subprocess_group(proc: subprocess.Popen):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_playwright_probe(*args: str, timeout_seconds: float = 30):
    cmd = _playwright_probe_command(*args)
    env = os.environ.copy()
    env["AUTOTEAM_PROBE_MODE"] = "1"
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(1.0, float(timeout_seconds)))
    except subprocess.TimeoutExpired as exc:
        _kill_subprocess_group(proc)
        try:
            proc.communicate(timeout=1)
        except Exception:
            pass
        raise TimeoutError(f"Playwright probe timeout: {' '.join(args)}") from exc

    stdout = (stdout or "").strip()
    stderr = (stderr or "").strip()
    if proc.returncode != 0:
        detail = stderr or stdout or f"exit={proc.returncode}"
        raise RuntimeError(detail)

    if not stdout:
        return {}
    return _parse_playwright_probe_stdout(stdout)


def _parse_playwright_probe_stdout(stdout: str):
    text = (stdout or "").strip()
    if not text:
        return {}

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if not line.startswith(("{", "[")):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue

    return json.loads(text)


def _auto_check_team_member_count(timeout_seconds=30, retries=3):
    """查询 Team 实际成员数，供自动巡检的人数兜底判断使用。"""
    for attempt in range(1, max(1, retries) + 1):
        try:
            result = _run_playwright_probe("team-member-count", timeout_seconds=timeout_seconds)
        except TimeoutError:
            if attempt < retries:
                logger.warning(
                    "[巡检] 查询 Team 实际成员数超时（>%ss），准备重试第 %d/%d 次",
                    timeout_seconds,
                    attempt + 1,
                    retries,
                )
                continue
            logger.warning(
                "[巡检] 查询 Team 实际成员数超时（>%ss，已重试 %d 次），跳过本轮人数校验",
                timeout_seconds,
                retries,
            )
            return -1
        except Exception as exc:
            logger.warning("[巡检] 查询 Team 实际成员数失败: %s", exc)
            return -1

        try:
            return int(result.get("count", -1))
        except Exception:
            return -1

    return -1


def _auto_check_wait(interval_seconds, poll_seconds=0.2):
    """等待下一轮巡检，同时允许 stop / restart 尽快生效。"""
    interval = max(0.0, float(interval_seconds))
    poll = max(0.05, float(poll_seconds))
    deadline = time.monotonic() + interval

    while True:
        try:
            _maybe_reload_runtime_config_from_env_file()
        except Exception as exc:
            logger.warning("[配置] 自动热加载失败: %s", exc)

        if _auto_check_restart.is_set():
            _auto_check_restart.clear()
            return "restart"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if _auto_check_stop.wait(0):
                return "stop"
            return "timeout"

        step = min(remaining, poll)
        if _auto_check_stop.wait(step):
            return "stop"


def _auto_check_loop():
    """后台巡检线程：定期检查额度，多个账号低于阈值时自动轮转"""
    from autoteam.accounts import STATUS_ACTIVE, STATUS_AUTH_PENDING, is_account_disabled, load_accounts
    from autoteam.codex_auth import check_codex_quota
    from autoteam.manager import (
        _auth_repair_skip_reason,
        _count_pool_active_accounts,
        _pool_active_target,
        sync_account_states,
    )

    def _collect_auto_check_state(accounts, cfg):
        account_by_email = {
            (a.get("email") or "").strip().lower(): a for a in accounts if (a.get("email") or "").strip()
        }
        local_active_count = _count_pool_active_accounts(accounts, require_auth=True)
        auth_pending_accounts = [
            a
            for a in accounts
            if a["status"] == STATUS_AUTH_PENDING
            and not _is_main_account_email(a.get("email"))
            and not is_account_disabled(a)
        ]
        missing_auth_accounts = [
            a
            for a in accounts
            if a["status"] == STATUS_ACTIVE
            and not _is_main_account_email(a.get("email"))
            and not is_account_disabled(a)
            and not (a.get("auth_file") and Path(a["auth_file"]).exists())
        ]
        active = [
            a
            for a in accounts
            if a["status"] == STATUS_ACTIVE
            and not _is_main_account_email(a.get("email"))
            and not is_account_disabled(a)
            and a.get("auth_file")
            and Path(a["auth_file"]).exists()
        ]

        low_accounts = []
        auth_problem_accounts = []
        for acc in active:
            try:
                auth_data = json.loads(read_text(Path(acc["auth_file"])))
                access_token = auth_data.get("access_token")
                if not access_token:
                    continue
                status, info = check_codex_quota(access_token)
                if status == "ok" and isinstance(info, dict):
                    remaining = 100 - info.get("primary_pct", 0)
                    if remaining < cfg["threshold"]:
                        low_accounts.append((acc["email"], remaining, status, info))
                elif status == "exhausted":
                    low_accounts.append((acc["email"], 0, status, info))
                elif status == "auth_error":
                    auth_problem_accounts.append(acc["email"])
            except Exception:
                pass

        repair_candidates = list(auth_problem_accounts)
        if auth_pending_accounts:
            repair_candidates.extend(a["email"] for a in auth_pending_accounts)
        if missing_auth_accounts:
            repair_candidates.extend(a["email"] for a in missing_auth_accounts)
        repair_candidates = list(dict.fromkeys(repair_candidates))

        actionable_repair_candidates = []
        throttled_repair_candidates = []
        for candidate_email in repair_candidates:
            acc = account_by_email.get(candidate_email.lower())
            skip_reason = _auth_repair_skip_reason(acc, force=False)
            if skip_reason:
                throttled_repair_candidates.append((candidate_email, skip_reason))
            else:
                actionable_repair_candidates.append(candidate_email)

        cpa_missing_accounts: list[dict] = []
        try:
            from autoteam.config import CPA_KEY, CPA_URL
            from autoteam.cpa_sync import list_cpa_files

            if CPA_URL and CPA_KEY:
                cpa_files = list_cpa_files()
                cpa_emails = {
                    (item.get("email") or "").lower() for item in cpa_files if (item.get("email") or "").strip()
                }
                for acc in active:
                    if acc["email"].lower() not in cpa_emails:
                        cpa_missing_accounts.append(acc)
                if cpa_missing_accounts:
                    cpa_file_count = len(active) - len(cpa_missing_accounts)
                else:
                    cpa_file_count = len(active)
                cpa_mismatch = len(cpa_missing_accounts) > 0
        except Exception as exc:
            logger.warning("[巡检] CPA 文件数校验失败，跳过本轮 CPA 对账: %s", exc)

        return {
            "accounts": accounts,
            "account_by_email": account_by_email,
            "local_active_count": local_active_count,
            "auth_pending_accounts": auth_pending_accounts,
            "missing_auth_accounts": missing_auth_accounts,
            "active": active,
            "low_accounts": low_accounts,
            "auth_problem_accounts": auth_problem_accounts,
            "repair_candidates": repair_candidates,
            "actionable_repair_candidates": actionable_repair_candidates,
            "throttled_repair_candidates": throttled_repair_candidates,
            "cpa_file_count": cpa_file_count,
            "cpa_mismatch": cpa_mismatch,
            "cpa_missing_accounts": cpa_missing_accounts,
        }

    while not _auto_check_stop.is_set():
        try:
            _maybe_reload_runtime_config_from_env_file()
        except Exception as exc:
            logger.warning("[配置] 自动热加载失败: %s", exc)

        cfg = _auto_check_config
        target_seats = max(1, int(cfg.get("target_seats", _DEFAULT_TARGET_SEATS)))
        pool_active_target = _pool_active_target(target_seats)
        logger.info(
            "[巡检] 等待 %d 分钟后执行下一轮检查（目标 seat: %d, 阈值: %d%%, 触发: >=%d 个）",
            cfg["interval"] // 60,
            target_seats,
            cfg["threshold"],
            cfg["min_low"],
        )

        # 等待 interval 秒，期间可被 restart 或 stop 唤醒
        wait_result = _auto_check_wait(cfg["interval"])
        if wait_result == "stop":
            break
        if wait_result == "restart":
            continue  # 配置变更，跳到下一轮重新读取配置

        try:
            cfg = _auto_check_config  # 重新读取
            target_seats = max(1, int(cfg.get("target_seats", _DEFAULT_TARGET_SEATS)))
            pool_active_target = _pool_active_target(target_seats)
            accounts = load_accounts()
            state = _collect_auto_check_state(accounts, cfg)
            local_active_count = state["local_active_count"]
            low_accounts = state["low_accounts"]
            auth_problem_accounts = state["auth_problem_accounts"]

            if low_accounts:
                logger.info(
                    "[巡检] %d 个账号额度不足: %s",
                    len(low_accounts),
                    ", ".join(f"{e}({r}%)" for e, r, _status, _info in low_accounts),
                )
            if auth_problem_accounts:
                logger.info(
                    "[巡检] %d 个账号认证待修复: %s",
                    len(auth_problem_accounts),
                    ", ".join(auth_problem_accounts),
                )

            if state.get("cpa_mismatch"):
                missing = state.get("cpa_missing_accounts") or []
                logger.warning(
                    "[巡检] CPA 远端缺少 %d/%d 个本地 active 账号的认证文件，开始补上传: %s",
                    len(missing),
                    len(state["active"]),
                    ", ".join(a["email"] for a in missing),
                )
                uploaded = 0
                failed = 0
                for acc in missing:
                    auth_file = acc.get("auth_file")
                    if not auth_file or not Path(auth_file).exists():
                        logger.warning("[巡检] 账号 %s 无本地认证文件，跳过 CPA 上传", acc["email"])
                        failed += 1
                        continue
                    try:
                        from autoteam.accounts import compute_cpa_priority
                        from autoteam.cpa_sync import patch_cpa_priority, upload_to_cpa

                        if upload_to_cpa(auth_file):
                            priority = compute_cpa_priority(acc)
                            patch_cpa_priority(Path(auth_file).name, priority)
                            uploaded += 1
                        else:
                            failed += 1
                    except Exception as exc:
                        logger.warning("[巡检] 上传 %s 到 CPA 失败: %s", acc["email"], exc)
                        failed += 1
                logger.info("[巡检] CPA 补上传完成: 成功 %d, 失败 %d", uploaded, failed)

            seat_shortage = max(0, target_seats - 1 - local_active_count)
            actual_team_count = -1
            team_count_check_failed = False
            trigger_rotate = len(low_accounts) >= cfg["min_low"]
            trigger_cleanup = False
            trigger_auth_repair = False
            actionable_repair_candidates = state["actionable_repair_candidates"]
            throttled_repair_candidates = state["throttled_repair_candidates"]

            if not trigger_rotate:
                actual_team_count = _auto_check_team_member_count()
                if actual_team_count < 0:
                    team_count_check_failed = True
                elif actual_team_count > target_seats:
                    trigger_cleanup = True
                    seat_shortage = 0
                else:
                    team_shortage = max(0, target_seats - actual_team_count)
                    trigger_rotate = team_shortage > 0
                    if not trigger_rotate and actual_team_count >= target_seats and actionable_repair_candidates:
                        trigger_auth_repair = True

                if (
                    not trigger_rotate
                    and not trigger_cleanup
                    and not trigger_auth_repair
                    and actual_team_count >= target_seats
                    and local_active_count < pool_active_target
                    and not throttled_repair_candidates
                ):
                    logger.info(
                        "[巡检] Team 实际成员数已满足（%d/%d），但本地可用 active 仅 %d/%d，先同步本地 Team 状态后重试判断...",
                        actual_team_count,
                        target_seats,
                        local_active_count,
                        pool_active_target,
                    )
                    try:
                        sync_account_states()
                    except Exception as exc:
                        logger.warning("[巡检] 同步本地 Team 状态失败，继续使用当前本地状态: %s", exc)
                    else:
                        accounts = load_accounts()
                        state = _collect_auto_check_state(accounts, cfg)
                        local_active_count = state["local_active_count"]
                        low_accounts = state["low_accounts"]
                        actionable_repair_candidates = state["actionable_repair_candidates"]
                        throttled_repair_candidates = state["throttled_repair_candidates"]
                        seat_shortage = max(0, target_seats - 1 - local_active_count)
                        trigger_rotate = len(low_accounts) >= cfg["min_low"]
                        if not trigger_rotate and actionable_repair_candidates:
                            trigger_auth_repair = True

            if trigger_rotate or trigger_cleanup or trigger_auth_repair:
                # 检查是否有任务在跑
                if not _playwright_lock.acquire(blocking=False):
                    logger.info("[巡检] 有任务正在执行，跳过本轮自动轮转/补位/清理/认证修复")
                    continue
                _playwright_lock.release()

                if trigger_rotate:
                    try:
                        _require_pool_operation_configs("自动轮转/补位")
                    except HTTPException as exc:
                        logger.warning("[巡检] 跳过自动轮转/补位: %s", exc.detail)
                        continue

                    # 将低于阈值的账号标记为 exhausted，rotate 会自动移出并补充
                    from autoteam.accounts import STATUS_EXHAUSTED, update_account
                    from autoteam.codex_auth import quota_result_quota_info, quota_result_resets_at

                    for email, remaining, status, info in low_accounts:
                        if target_seats == 2 and status == "ok":
                            logger.info(
                                "[巡检] %s 剩余 %d%% < %d%%，seat=2 预切换模式暂不预标记 exhausted",
                                email,
                                remaining,
                                cfg["threshold"],
                            )
                            continue
                        logger.info("[巡检] %s 剩余 %d%%，标记为 exhausted", email, remaining)
                        status_kwargs = {
                            "status": STATUS_EXHAUSTED,
                            "quota_exhausted_at": time.time(),
                        }
                        if status == "ok":
                            status_kwargs["last_quota"] = info if isinstance(info, dict) else None
                            status_kwargs["quota_resets_at"] = (
                                info.get("primary_resets_at") if isinstance(info, dict) else None
                            ) or int(time.time() + 18000)
                        else:
                            status_kwargs["last_quota"] = quota_result_quota_info(info)
                            status_kwargs["quota_resets_at"] = quota_result_resets_at(info) or int(time.time() + 18000)
                        update_account(email, **status_kwargs)

                    if seat_shortage > 0 and len(low_accounts) >= cfg["min_low"]:
                        logger.info(
                            "[巡检] 当前可用 active 数不足: %d/%d，且检测到低额度账号，触发自动轮转...",
                            local_active_count,
                            pool_active_target,
                        )
                    elif actual_team_count >= 0 and actual_team_count < target_seats:
                        logger.info(
                            "[巡检] Team 实际成员数不足（%d/%d），触发自动补位...",
                            actual_team_count,
                            target_seats,
                        )
                    else:
                        logger.info("[巡检] 触发自动轮转...")
                    from autoteam.manager import cmd_rotate

                    try:
                        _start_task(
                            "auto-rotate",
                            cmd_rotate,
                            {
                                "target": target_seats,
                                "trigger": "auto-check",
                                "shortage": max(0, target_seats - actual_team_count)
                                if actual_team_count >= 0
                                else seat_shortage,
                                "low_accounts": len(low_accounts),
                            },
                            target_seats,
                        )
                    except Exception as e:
                        logger.error("[巡检] 自动轮转失败: %s", e)
                elif trigger_auth_repair:
                    try:
                        _require_pool_operation_configs("自动认证修复")
                    except HTTPException as exc:
                        logger.warning("[巡检] 跳过自动认证修复: %s", exc.detail)
                        continue

                    logger.info(
                        "[巡检] Team 实际成员数已满足（%d/%d），但可用 Codex active 仅 %d/%d，触发自动认证修复...",
                        actual_team_count,
                        target_seats,
                        local_active_count,
                        pool_active_target,
                    )
                    from autoteam.manager import cmd_check

                    try:
                        _start_task(
                            "auto-auth-repair",
                            cmd_check,
                            {
                                "trigger": "auto-check",
                                "team_count": actual_team_count,
                                "pool_active": local_active_count,
                                "pool_active_target": pool_active_target,
                                "repair_candidates": actionable_repair_candidates,
                            },
                        )
                    except Exception as e:
                        logger.error("[巡检] 自动认证修复失败: %s", e)
                else:
                    logger.info(
                        "[巡检] Team 实际成员数超出目标（%d/%d），触发自动清理...",
                        actual_team_count,
                        target_seats,
                    )
                    from autoteam.manager import cmd_cleanup

                    try:
                        _start_task(
                            "auto-cleanup",
                            cmd_cleanup,
                            {
                                "max_seats": target_seats,
                                "trigger": "auto-check",
                                "team_count": actual_team_count,
                            },
                            target_seats,
                        )
                    except Exception as e:
                        logger.error("[巡检] 自动清理失败: %s", e)
            else:
                if low_accounts and actual_team_count >= target_seats:
                    logger.info(
                        "[巡检] 低额度账号未达到触发阈值（%d/%d），且 Team 实际成员数已满足（%d/%d），无需轮转",
                        len(low_accounts),
                        cfg["min_low"],
                        actual_team_count,
                        target_seats,
                    )
                elif low_accounts:
                    logger.info(
                        "[巡检] 低额度账号未达到触发阈值（%d/%d），无需轮转",
                        len(low_accounts),
                        cfg["min_low"],
                    )
                elif team_count_check_failed:
                    logger.info("[巡检] Team 成员数校验失败，且未达到低额度触发阈值，跳过本轮自动动作")
                elif actual_team_count >= target_seats and actionable_repair_candidates:
                    logger.info(
                        "[巡检] Team 实际成员数已满足（%d/%d），但存在 %d 个待修复账号，等待下一轮自动认证修复",
                        actual_team_count,
                        target_seats,
                        len(actionable_repair_candidates),
                    )
                elif actual_team_count >= target_seats and throttled_repair_candidates:
                    logger.info(
                        "[巡检] Team 实际成员数已满足（%d/%d），但 %d 个待修复账号仍在冷却/暂停中，暂不自动重试",
                        actual_team_count,
                        target_seats,
                        len(throttled_repair_candidates),
                    )
                elif actual_team_count >= target_seats and local_active_count < pool_active_target:
                    logger.info(
                        "[巡检] Team 实际成员数已满足（%d/%d），但本地可用 active 仅 %d/%d，且未发现可自动修复的本地账号",
                        actual_team_count,
                        target_seats,
                        local_active_count,
                        pool_active_target,
                    )
                else:
                    logger.info(
                        "[巡检] 额度正常且 active 数充足（%d/%d），无需轮转", local_active_count, pool_active_target
                    )

        except Exception as e:
            logger.error("[巡检] 巡检异常: %s", e)


class AutoCheckConfig(BaseModel):
    interval: int = 300  # 巡检间隔（秒）
    target_seats: int = 5  # 自动巡检目标 Team seat 数
    threshold: int = 10  # 额度阈值（%）
    min_low: int = 2  # 触发轮转的最少账号数
    retry_add_phone: bool = True  # 是否自动重试 add_phone
    add_phone_max_retries: int = 3  # add_phone 最大自动重试次数


def _normalized_auto_check_config(cfg: AutoCheckConfig | dict[str, object]) -> dict[str, int | bool]:
    if isinstance(cfg, AutoCheckConfig):
        interval = cfg.interval
        target_seats = cfg.target_seats
        threshold = cfg.threshold
        min_low = cfg.min_low
        retry_add_phone = cfg.retry_add_phone
        add_phone_max_retries = cfg.add_phone_max_retries
    else:
        interval = cfg.get("interval", _auto_check_config.get("interval", _DEFAULT_INTERVAL))
        target_seats = cfg.get("target_seats", _auto_check_config.get("target_seats", _DEFAULT_TARGET_SEATS))
        threshold = cfg.get("threshold", _auto_check_config.get("threshold", _DEFAULT_THRESHOLD))
        min_low = cfg.get("min_low", _auto_check_config.get("min_low", _DEFAULT_MIN_LOW))
        retry_add_phone = cfg.get(
            "retry_add_phone", _auto_check_config.get("retry_add_phone", _DEFAULT_RETRY_ADD_PHONE)
        )
        add_phone_max_retries = cfg.get(
            "add_phone_max_retries",
            _auto_check_config.get("add_phone_max_retries", _DEFAULT_ADD_PHONE_MAX_RETRIES),
        )

    return {
        "interval": max(60, int(interval)),
        "target_seats": max(1, int(target_seats)),
        "threshold": max(1, min(100, int(threshold))),
        "min_low": max(1, int(min_low)),
        "retry_add_phone": bool(retry_add_phone),
        "add_phone_max_retries": max(1, int(add_phone_max_retries)),
    }


@app.get("/api/config/auto-check")
def get_auto_check_config():
    """获取巡检配置"""
    cfg = _auto_check_config.copy()
    cfg.setdefault("target_seats", _DEFAULT_TARGET_SEATS)
    return cfg


@app.put("/api/config/auto-check")
def set_auto_check_config(cfg: AutoCheckConfig):
    """修改巡检配置（运行时生效，并持久化到 .env）"""
    from autoteam.setup_wizard import _write_env

    normalized = _normalized_auto_check_config(cfg)
    _auto_check_config.update(normalized)

    persisted = {
        "AUTO_CHECK_INTERVAL": str(normalized["interval"]),
        "AUTO_CHECK_TARGET_SEATS": str(normalized["target_seats"]),
        "AUTO_CHECK_THRESHOLD": str(normalized["threshold"]),
        "AUTO_CHECK_MIN_LOW": str(normalized["min_low"]),
        "AUTO_CHECK_RETRY_ADD_PHONE": "true" if normalized["retry_add_phone"] else "false",
        "AUTO_CHECK_ADD_PHONE_MAX_RETRIES": str(normalized["add_phone_max_retries"]),
    }
    for key, value in persisted.items():
        os.environ[key] = value
        _write_env(key, value)

    _sync_runtime_env_reload_state()
    _auto_check_restart.set()  # 唤醒巡检线程，立即应用新配置
    logger.info(
        "[巡检] 配置已更新并持久化: 间隔=%ds 目标seat=%d 阈值=%d%% 触发=%d个 add_phone自动重试=%s 最大重试=%d",
        _auto_check_config["interval"],
        _auto_check_config["target_seats"],
        _auto_check_config["threshold"],
        _auto_check_config["min_low"],
        "开" if _auto_check_config["retry_add_phone"] else "关",
        _auto_check_config["add_phone_max_retries"],
    )
    return _auto_check_config.copy()


@app.on_event("startup")
def _start_auto_check():
    try:
        from autoteam.auth_storage import ensure_auth_file_permissions

        fixed = ensure_auth_file_permissions()
        if fixed:
            logger.info("[启动] 已修复 %d 个 auths 认证文件权限", fixed)
    except Exception as exc:
        logger.warning("[启动] 修复 auths 认证文件权限失败: %s", exc)

    _sync_runtime_env_reload_state()
    thread = threading.Thread(target=_auto_check_loop, daemon=True)
    thread.start()
    backup_thread = threading.Thread(target=_auto_backup_loop, daemon=True)
    backup_thread.start()


@app.on_event("shutdown")
def _stop_auto_check():
    _auto_check_stop.set()
    try:
        _pw_executor.stop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# WebDAV 备份
# ---------------------------------------------------------------------------

_backup_stop = threading.Event()


def _auto_backup_loop():
    """后台自动备份线程"""
    from autoteam.webdav_backup import _get_webdav_config
    from autoteam.webdav_backup import backup as do_backup

    while True:
        try:
            cfg = _get_webdav_config()
        except Exception:
            cfg = {"enabled": False}

        if cfg.get("enabled"):
            interval = max(30, int(cfg.get("interval", 3600)))
        else:
            interval = 30

        if _backup_stop.wait(interval):
            return

        if not cfg.get("enabled"):
            continue

        try:
            do_backup()
        except Exception as exc:
            logger.warning("[WebDAV] 自动备份失败: %s", exc)


@app.get("/api/backup/status")
def get_backup_status():
    """获取 WebDAV 备份状态和备份列表"""
    try:
        from autoteam.webdav_backup import _get_webdav_config, list_backups

        cfg = _get_webdav_config()
        backups = list_backups() if cfg["enabled"] else []
        return {
            "enabled": cfg["enabled"],
            "configured": bool(cfg["url"]),
            "backups": backups,
        }
    except Exception as exc:
        return {"enabled": False, "configured": False, "backups": [], "error": str(exc)}


@app.post("/api/backup/run")
def run_backup():
    """手动触发一次备份"""
    try:
        from autoteam.webdav_backup import _get_webdav_config
        from autoteam.webdav_backup import backup as do_backup

        cfg = _get_webdav_config()
        if not cfg["enabled"] or not cfg["url"]:
            raise HTTPException(status_code=400, detail="WebDAV 备份未启用或未配置地址")

        result = do_backup()
        if result:
            return {"success": True, "filename": result}
        raise HTTPException(status_code=500, detail="备份失败，请查看服务端日志")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/backup/restore")
def restore_backup(payload: dict | None = None):
    """从 WebDAV 恢复备份"""
    try:
        from autoteam.webdav_backup import restore as do_restore

        name = ""
        if isinstance(payload, dict):
            name = payload.get("name", "")
        result = do_restore(name or None)
        if result:
            return {"success": True, "message": f"已从 {name or '最新备份'} 恢复"}
        raise HTTPException(status_code=500, detail="恢复失败，请查看服务端日志")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# 前端静态文件
# ---------------------------------------------------------------------------

NOVNC_WEB_DIR = _novnc_web_dir()
if (NOVNC_WEB_DIR / "vnc.html").is_file():
    app.mount("/desktop", StaticFiles(directory=str(NOVNC_WEB_DIR), html=True), name="desktop")

DIST_DIR = Path(__file__).parent / "web" / "dist"

if DIST_DIR.exists():
    # Vite 构建的 assets 目录
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{path:path}")
    def serve_frontend(path: str):
        """兜底路由：serve 前端 SPA"""
        file = DIST_DIR / path
        if file.is_file() and ".." not in path:
            return FileResponse(str(file))
        return FileResponse(str(DIST_DIR / "index.html"))


class _QuietAccessLog(logging.Filter):
    """过滤前端轮询产生的高频访问日志"""

    _quiet_paths = (
        "/api/status",
        "/api/tasks",
        "/api/config/auto-check",
        "/api/config/runtime",
        "/api/admin/status",
        "/api/main-codex/status",
        "/api/manual-account/status",
        "/api/auth/check",
        "/api/setup/status",
    )

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self._quiet_paths)


def start_server(host: str = "0.0.0.0", port: int = 8787):
    """启动 API 服务器"""
    import uvicorn

    # 过滤轮询日志，避免刷屏
    logging.getLogger("uvicorn.access").addFilter(_QuietAccessLog())
    # 首次启动检查配置
    from autoteam.setup_wizard import check_and_setup

    check_and_setup(interactive=True)

    # 重新读取 API_KEY（可能刚刚被向导写入）
    global API_KEY
    from autoteam.config import API_KEY as _fresh_key

    API_KEY = _fresh_key or os.environ.get("API_KEY", "")
    if API_KEY:
        logger.info("[API] API Key 鉴权已启用")
    else:
        logger.warning("[API] 未设置 API_KEY，所有接口无需认证")
    logger.info("[API] 启动 AutoTeam API 服务器 http://%s:%d", host, port)
    if DIST_DIR.exists():
        logger.info("[API] 前端面板 http://%s:%d", host, port)
    logger.info("[API] API 文档 http://%s:%d/docs", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
