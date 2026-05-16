"""
SaaSクライアントのセッションを週次で自動更新する
Supabaseから全アクティブサロンのセッションを読み込み、
Threadsにアクセスして延命→更新済みセッションを保存する。

使い方: python3 refresh_sessions_saas.py
戻り値: 失敗したサロン名を標準出力に出力して exit 1
"""
import json
import os
import sys
import tempfile
import urllib.request
import urllib.parse

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def supabase_get(path, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def supabase_patch(path, params, data):
    url = f"{SUPABASE_URL}/rest/v1/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def refresh_session(salon_name, session_data_str):
    """セッションを使ってThreadsにアクセスし、更新されたセッションを返す"""
    session_data = json.loads(session_data_str) if isinstance(session_data_str, str) else session_data_str

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(session_data, f)
        session_file = f.name

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(storage_state=session_file)
            page = context.new_page()

            page.goto("https://www.threads.com", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)

            # ログイン済みか確認
            logged_in_selectors = [
                '[aria-label*="Create"]',
                '[aria-label*="新しい"]',
                'a[href="/intent/post"]',
                'a[href^="/@"]',
            ]
            is_logged_in = any(
                page.locator(sel).first.is_visible()
                for sel in logged_in_selectors
            )

            if is_logged_in:
                # 更新されたセッションを取得
                updated = context.storage_state()
                browser.close()
                os.unlink(session_file)
                return True, json.dumps(updated)
            else:
                browser.close()
                os.unlink(session_file)
                return False, None

    except Exception as e:
        print(f"  [{salon_name}] Playwrightエラー: {e}")
        try:
            os.unlink(session_file)
        except Exception:
            pass
        return False, None


def main():
    salons = supabase_get("salons", {
        "is_active": "eq.true",
        "session_data": "not.is.null",
        "select": "id,salon_name,threads_username,session_data",
    })

    if not salons:
        print("更新対象のサロンがありません")
        return

    print(f"=== セッション自動更新 {len(salons)}件 ===")
    failed = []

    for salon in salons:
        salon_name = salon["salon_name"]
        print(f"\n[{salon_name}] 更新中...")

        ok, updated_session = refresh_session(salon_name, salon["session_data"])

        if ok:
            # Supabaseに更新済みセッションを保存
            supabase_patch(
                "salons",
                {"id": f"eq.{salon['id']}"},
                {"session_data": updated_session},
            )
            print(f"  ✅ セッション更新完了")
        else:
            print(f"  ❌ セッション切れ → 再ログインが必要")
            failed.append(salon_name)

    print(f"\n完了: 成功={len(salons) - len(failed)} 失敗={len(failed)}")

    if failed:
        print("要対応サロン:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
