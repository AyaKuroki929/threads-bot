"""
SaaSクライアントのThreadsセッションを取得してSupabaseに保存する
使い方: python3 playwright_login_saas.py <salon_name> [threads_username]

例: python3 playwright_login_saas.py "ネイルサロンABC" nail_abc_official
"""
import sys
import json
import os
import urllib.request
import urllib.parse

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def upsert_salon(salon_name, threads_username, session_data):
    data = {
        "salon_name": salon_name,
        "threads_username": threads_username,
        "session_data": json.dumps(session_data),
        "is_active": True,
    }
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/salons",
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def main():
    if len(sys.argv) < 2:
        print("使い方: python3 playwright_login_saas.py <salon_name> [threads_username]")
        sys.exit(1)

    salon_name = sys.argv[1]
    threads_username = sys.argv[2] if len(sys.argv) > 2 else salon_name

    print(f"\n=== [{salon_name}] のセッション取得 ===")
    print(f"Threadsアカウント: @{threads_username}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.goto("https://www.threads.com")
        print("\nブラウザが開きました。")
        print(f"「Continue with Instagram」から【{salon_name}（@{threads_username}）】でログインしてください。")
        print("Threadsのフィードが表示されたらEnterを押してください。")
        input()

        # ログイン中のアカウントを確認してから保存
        print("\nログイン中のアカウントを確認中...")
        try:
            cookies = ctx.cookies()
            actual_username = ""
            # Threadsのクッキーからユーザー名を取得
            for cookie in cookies:
                if cookie.get("name") == "sessionid" and "instagram" in cookie.get("domain", ""):
                    break
            # プロフィールページからユーザー名を確認
            page.goto("https://www.threads.com/", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            # URLまたはページ内容からユーザー名を取得
            profile_links = page.locator('a[href^="/@"]').all()
            for link in profile_links:
                href = link.get_attribute("href") or ""
                if href.startswith("/@") and len(href) > 2:
                    actual_username = href[2:].split("/")[0]
                    break
        except Exception as e:
            actual_username = ""
            print(f"  （自動確認失敗: {e}）")

        print("\n" + "="*50)
        if actual_username:
            print(f"  ✅ 検出されたアカウント: @{actual_username}")
            print(f"  📋 保存先サロン名: {salon_name}")
            print(f"  📋 登録ユーザー名: @{threads_username}")
            if actual_username.lower() != threads_username.lower():
                print(f"\n  ⚠️  警告：アカウントが異なります！")
                print(f"     ログイン中: @{actual_username}")
                print(f"     期待値:     @{threads_username}")
                print("\n  このまま保存しますか？(yes/no): ", end="")
                answer = input().strip().lower()
                if answer != "yes":
                    print("キャンセルしました。ブラウザを再度使ってログインし直してください。")
                    browser.close()
                    return
            else:
                print(f"\n  ✅ アカウント確認OK。保存します。(Enterで続行)")
                input()
        else:
            print(f"  ⚠️  アカウントを自動検出できませんでした。")
            print(f"  ブラウザで @{threads_username} でログイン中か確認してください。")
            print(f"\n  正しいアカウントでログイン中ですか？(yes/no): ", end="")
            answer = input().strip().lower()
            if answer != "yes":
                print("キャンセルしました。")
                browser.close()
                return
        print("="*50)

        storage_state = ctx.storage_state()
        browser.close()

    print("セッション取得完了。Supabaseに保存中...")
    status = upsert_salon(salon_name, threads_username, storage_state)
    print(f"✅ [{salon_name}] Supabaseに保存しました (HTTP {status})")
    print("\n次のステップ:")
    print(f"  投稿ファイル: posts_saas/posts_{salon_name}.json を作成")
    print(f"  投稿開始: python3 post_saas_playwright.py morning")


if __name__ == "__main__":
    main()
