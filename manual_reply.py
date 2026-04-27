"""特定の投稿URLに対して単発で返信を1つ投稿する（手動補完用）。

使い方:
    python3 manual_reply.py "<post_url>" "<本文>"
"""
import sys
from playwright.sync_api import sync_playwright
from post import (
    SESSION_FILE,
    _open_reply_modal,
    _input_text,
    _click_submit,
)


def main():
    if len(sys.argv) < 3:
        print('使い方: python3 manual_reply.py "<post_url>" "<本文>"')
        sys.exit(1)
    post_url = sys.argv[1]
    text = sys.argv[2]

    import os as _os
    headed = _os.environ.get("HEADED") == "1"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(
            storage_state=SESSION_FILE,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        try:
            print(f"[manual_reply] 親URL: {post_url}")
            _open_reply_modal(page, post_url)
            _input_text(page, text)
            page.screenshot(path="manual_reply_before_submit.png", full_page=False)
            _click_submit(page)
            page.wait_for_timeout(5000)
            page.screenshot(path="manual_reply_after_submit.png", full_page=False)
            # 投稿後にチェーンを再読み込みして3部目本文が見えるかチェック
            page.goto(post_url, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            body = page.locator("body").inner_text()
            marker = text.split("\n")[0][:15]
            print(f"[verify] チェーン詳細に '{marker}' が見える: {marker in body}")
            print("返信投稿完了！" if marker in body else "[warn] 投稿は反映されていません")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
