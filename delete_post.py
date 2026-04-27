"""指定URLの自分の投稿を削除する。

使い方:
    python3 delete_post.py "<post_url>" ["<post_url>" ...]
"""
import os
import sys
from playwright.sync_api import sync_playwright
from post import SESSION_FILE


def delete_one(page, post_url):
    print(f"[delete] {post_url}")
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)

    main_article = page.locator('div[id="barcelona-page-layout"] [role="article"]').first
    if main_article.count() == 0:
        main_article = page.locator('[role="article"]').first

    menu_btn = main_article.locator('[aria-label*="その他"], [aria-label*="More"]').first
    if menu_btn.count() == 0:
        menu_btn = page.locator('svg[aria-label*="その他"], svg[aria-label*="More"]').first
    if menu_btn.count() == 0:
        raise RuntimeError("メニューボタン(...)が見つかりません")
    menu_btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    menu_btn.click()
    page.wait_for_timeout(1500)

    # メニュー内の「削除」を探す
    delete_item = page.get_by_text("削除", exact=True).first
    if delete_item.count() == 0:
        delete_item = page.get_by_text("Delete", exact=True).first
    if delete_item.count() == 0:
        raise RuntimeError("削除メニュー項目が見つかりません")
    delete_item.click()
    page.wait_for_timeout(1500)

    # 確認ダイアログの「削除」ボタン
    confirm = page.locator('div[role="dialog"]').last.get_by_text("削除").last
    if confirm.count() == 0:
        confirm = page.locator('div[role="dialog"]').last.get_by_text("Delete").last
    if confirm.count() == 0:
        raise RuntimeError("確認ダイアログの削除ボタンが見つかりません")
    confirm.click()
    page.wait_for_timeout(3000)
    print(f"[ok] 削除完了: {post_url}")


def main():
    if len(sys.argv) < 2:
        print('使い方: python3 delete_post.py "<post_url>" ["<post_url>" ...]')
        sys.exit(1)
    urls = sys.argv[1:]
    headed = os.environ.get("HEADED") == "1"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(
            storage_state=SESSION_FILE,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        try:
            for u in urls:
                try:
                    delete_one(page, u)
                except Exception as e:
                    print(f"[err] {u}: {e}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
