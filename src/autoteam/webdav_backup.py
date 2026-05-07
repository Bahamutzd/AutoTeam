"""WebDAV 远程备份模块 — 打包/上传/下载/恢复/版本管理"""

import io
import logging
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
BACKUP_FILES = [".env", "accounts.json", "state.json"]
BACKUP_DIRS = ["auths"]

_DATE_FMT = "%Y%m%d-%H%M%S"


def _get_webdav_config():
    from autoteam.config import (
        WEBDAV_BACKUP_ENABLED,
        WEBDAV_BACKUP_INTERVAL,
        WEBDAV_BACKUP_KEEP_VERSIONS,
        WEBDAV_PASSWORD,
        WEBDAV_URL,
        WEBDAV_USERNAME,
    )

    return {
        "url": WEBDAV_URL,
        "username": WEBDAV_USERNAME,
        "password": WEBDAV_PASSWORD,
        "enabled": WEBDAV_BACKUP_ENABLED,
        "interval": WEBDAV_BACKUP_INTERVAL,
        "keep_versions": WEBDAV_BACKUP_KEEP_VERSIONS,
    }


def _webdav_request(method: str, path: str, data: bytes | None = None, timeout: int = 60):
    cfg = _get_webdav_config()
    url = cfg["url"].rstrip("/") + "/" + path.lstrip("/")
    auth = HTTPBasicAuth(cfg["username"], cfg["password"]) if cfg["username"] else None
    resp = requests.request(method, url, auth=auth, data=data, timeout=timeout)
    return resp


def list_backups() -> list[dict]:
    """列出 WebDAV 上所有备份文件，按时间倒序"""
    cfg = _get_webdav_config()
    if not cfg["enabled"] or not cfg["url"]:
        return []

    try:
        resp = _webdav_request("PROPFIND", "/", timeout=30)
    except Exception as e:
        logger.warning("[WebDAV] 列出备份失败: %s", e)
        return []

    if resp.status_code == 404:
        return []

    if resp.status_code not in (200, 207):
        logger.warning("[WebDAV] 列出备份返回 %d", resp.status_code)
        return []

    backups = []
    # 简单解析 XML 提取文件名
    import re

    for match in re.finditer(r"<D:href>([^<]*autoteam-backup-[^<]*\.tar\.gz)</D:href>", resp.text):
        filename = match.group(1).rsplit("/", 1)[-1]
        # 从文件名提取时间: autoteam-backup-20260507-120000.tar.gz
        ts_match = re.search(r"(\d{8}-\d{6})", filename)
        ts = ts_match.group(1) if ts_match else ""
        backups.append({"name": filename, "ts": ts})

    backups.sort(key=lambda b: b["ts"], reverse=True)
    return backups


def backup() -> str | None:
    """打包数据文件并上传到 WebDAV，保留最近 N 个版本"""
    cfg = _get_webdav_config()
    if not cfg["enabled"] or not cfg["url"]:
        logger.info("[WebDAV] 备份未启用，跳过")
        return None

    now = datetime.now(timezone.utc)
    ts = now.strftime(_DATE_FMT)
    filename = f"autoteam-backup-{ts}.tar.gz"

    # 打包
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w:gz") as tar:
        for f in BACKUP_FILES:
            fp = PROJECT_ROOT / f
            if fp.exists():
                tar.add(str(fp), arcname=f)
        for d in BACKUP_DIRS:
            dp = PROJECT_ROOT / d
            if dp.is_dir():
                tar.add(str(dp), arcname=d)

    tar_data = tar_buffer.getvalue()

    # 上传
    try:
        resp = _webdav_request("PUT", f"/{filename}", data=tar_data, timeout=120)
    except Exception as e:
        logger.error("[WebDAV] 上传备份失败: %s", e)
        return None

    if resp.status_code not in (200, 201, 204):
        logger.error("[WebDAV] 上传备份返回 %d: %s", resp.status_code, resp.text[:200])
        return None

    size_kb = len(tar_data) / 1024
    logger.info("[WebDAV] 备份上传成功: %s (%.1f KB)", filename, size_kb)

    # 清理旧版本
    all_backups = list_backups()
    keep = cfg["keep_versions"]
    for old in all_backups[keep:]:
        try:
            _webdav_request("DELETE", f"/{old['name']}", timeout=30)
            logger.info("[WebDAV] 清理旧备份: %s", old["name"])
        except Exception as e:
            logger.warning("[WebDAV] 清理旧备份失败 %s: %s", old["name"], e)

    return filename


def restore(backup_name: str | None = None) -> bool:
    """从 WebDAV 下载备份并恢复到本地"""
    cfg = _get_webdav_config()
    if not cfg["enabled"] or not cfg["url"]:
        logger.error("[WebDAV] 备份未启用，无法恢复")
        return False

    # 未指定则用最新
    if not backup_name:
        backups = list_backups()
        if not backups:
            logger.error("[WebDAV] 没有可用的备份")
            return False
        backup_name = backups[0]["name"]

    # 下载
    try:
        resp = _webdav_request("GET", f"/{backup_name}", timeout=120)
    except Exception as e:
        logger.error("[WebDAV] 下载备份失败: %s", e)
        return False

    if resp.status_code != 200:
        logger.error("[WebDAV] 下载备份返回 %d", resp.status_code)
        return False

    # 解压恢复
    try:
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            tar.extractall(path=str(PROJECT_ROOT))
    except Exception as e:
        logger.error("[WebDAV] 解压恢复失败: %s", e)
        return False

    logger.info("[WebDAV] 恢复成功: %s", backup_name)
    return True
