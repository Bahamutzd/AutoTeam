"""统一的 Playwright 运行时入口（软依赖 + 自动回退）。

优先使用 patchright —— 一个对 Playwright 做了反检测修补的 drop-in 替代品，
修掉了 navigator.webdriver、Runtime.enable 等会被 Cloudflare Turnstile 风控
识别的自动化痕迹。未安装 patchright 时自动回退官方 playwright，二者 API 完全兼容。

USING_PATCHRIGHT 标志供调用方判断是否仍需手动注入反检测脚本：patchright 已在
内核层处理 webdriver，官方文档建议此时不要再 add_init_script；回退到官方
playwright 时则仍需手动注入。
"""

try:
    from patchright.sync_api import sync_playwright

    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright

    USING_PATCHRIGHT = False

__all__ = ["USING_PATCHRIGHT", "sync_playwright"]
