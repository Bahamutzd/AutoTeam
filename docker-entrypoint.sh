#!/bin/bash
set -e

# 清理残留锁文件并启动虚拟显示器
export DISPLAY="${DISPLAY:-:99}"
XVFB_SCREEN="${XVFB_SCREEN:-1280x800x24}"
display_number="${DISPLAY#:}"
display_number="${display_number%%.*}"
if [ -n "$display_number" ]; then
    rm -f "/tmp/.X${display_number}-lock"
fi
Xvfb "$DISPLAY" -screen 0 "$XVFB_SCREEN" -ac +extension RANDR &

is_enabled() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|on|enabled) return 0 ;;
        *) return 1 ;;
    esac
}

sleep "${XVFB_STARTUP_DELAY:-1}"

if is_enabled "${ENABLE_NOVNC:-true}"; then
    if command -v fluxbox >/dev/null 2>&1; then
        fluxbox >/tmp/fluxbox.log 2>&1 &
    fi

    VNC_HOST="${VNC_HOST:-127.0.0.1}"
    VNC_PORT="${VNC_PORT:-5900}"
    vnc_auth_args=(-nopw)
    if [ -n "${NOVNC_PASSWORD:-}" ]; then
        mkdir -p /tmp/vnc
        x11vnc -storepasswd "$NOVNC_PASSWORD" /tmp/vnc/passwd >/tmp/x11vnc-passwd.log 2>&1
        vnc_auth_args=(-rfbauth /tmp/vnc/passwd)
    fi
    x11vnc -display "$DISPLAY" -forever -shared -xkb "${vnc_auth_args[@]}" \
        -rfbport "$VNC_PORT" -listen "$VNC_HOST" >/tmp/x11vnc.log 2>&1 &
fi

# 确保数据目录存在且可写
mkdir -p /app/data /app/data/auths /app/data/screenshots
chmod -R 777 /app/data

# 数据文件：无条件软链到 data/（确保所有写入都持久化）
for f in .env accounts.json state.json; do
    # data 里没有就创建空文件
    [ -f "/app/data/$f" ] || touch "/app/data/$f"
    # 删除容器内的真实文件（如果不是软链），然后建软链
    rm -f "/app/$f"
    ln -s "/app/data/$f" "/app/$f"
done

# 目录软链
for d in auths screenshots; do
    rm -rf "/app/$d"
    ln -s "/app/data/$d" "/app/$d"
done

# 执行命令
exec uv run autoteam "$@"
