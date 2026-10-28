"""User-run, headed login for the collector's own private session; never copy profiles."""

import argparse

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.browser_login import save_private_state
from app.config import get_settings


def main():
    parser = argparse.ArgumentParser(description="为后台独立浏览器保存人工登录状态")
    parser.add_argument("platform", choices=["taobao", "jd"])
    args = parser.parse_args()
    settings = get_settings()
    directory = settings.browser_session_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = directory / f"{args.platform}.json"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            context = browser.new_context(
                locale="zh-CN",
                accept_downloads=False,
                storage_state=str(state_path) if state_path.is_file() else None,
            )
            try:
                page = context.new_page()
                page.goto(
                    f"https://www.{'taobao.com' if args.platform == 'taobao' else 'jd.com'}/",
                    wait_until="domcontentloaded",
                )
                print("请在弹出的独立浏览器中自行登录并处理验证。不会读取日常浏览器资料。")
                input("确认登录成功后回到此终端按 Enter 保存；Ctrl+C 取消：")
                save_private_state(context, state_path, args.platform)
                print("已保存私有会话。后台下次采集自动使用；状态过期时重新执行此命令。")
            finally:
                context.close()
                browser.close()
    except (PlaywrightError, EOFError, KeyboardInterrupt):
        raise SystemExit(
            "未保存新会话。请确认有可用图形桌面，或改用已登录商品页的扩展接管。"
        ) from None


if __name__ == "__main__":
    main()
