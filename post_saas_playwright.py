"""
SaaS版 Threads自動投稿（Playwright方式）
Supabaseから全クライアントのセッションを読み込み、Playwright で投稿する。

使い方: python3 post_saas_playwright.py morning / evening
"""
import sys
import json
import os
import re
import random
import tempfile
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
POSTS_DIR = os.path.join(os.path.dirname(__file__), "posts_saas")
BASE_DIR = os.path.dirname(__file__)

SLOT = sys.argv[1] if len(sys.argv) > 1 else "morning"


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


def supabase_post(path, data, method="POST"):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method=method,
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def get_active_salons_playwright():
    """session_dataが設定されているサロンを取得"""
    return supabase_get("salons", {
        "is_active": "eq.true",
        "session_data": "not.is.null",
        "select": "id,salon_name,threads_username,session_data",
    })


def get_used_posts(salon_id, slot):
    rows = supabase_get("post_logs", {
        "salon_id": f"eq.{salon_id}",
        "slot": f"eq.{slot}",
        "select": "post_content",
    })
    return {r["post_content"] for r in rows}


def pick_post(salon_name, slot, used_texts):
    safe_name = re.sub(r'[^\w\-]', '_', salon_name)
    posts_file = os.path.join(POSTS_DIR, f"posts_{safe_name}.json")
    if not os.path.exists(posts_file):
        raise FileNotFoundError(f"投稿ファイルが見つかりません: {posts_file}")
    with open(posts_file) as f:
        data = json.load(f)
    candidates = data.get(slot, [])
    if not candidates:
        raise ValueError(f"{salon_name}: {slot} スロットの投稿がありません")
    unused = [p for p in candidates if p not in used_texts]
    if not unused:
        unused = candidates
    return random.choice(unused)


def log_post(salon_id, slot, text):
    supabase_post("post_logs", {
        "salon_id": salon_id,
        "slot": slot,
        "post_content": text,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    })


def post_with_playwright(session_data_str, threads_username, text):
    """Playwrightを使ってThreadsに投稿する"""
    session_data = json.loads(session_data_str) if isinstance(session_data_str, str) else session_data_str

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(session_data, f)
        session_file = f.name

    # 一時的なused_fileとposts_fileを用意（post.pyが必要とするため）
    dummy_posts = {slot: [text] for slot in ["morning", "morning2", "noon", "evening2", "evening"]}
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(dummy_posts, f)
        posts_file = f.name

    used_file = tempfile.mktemp(suffix='.json')
    with open(used_file, 'w') as f:
        json.dump([], f)

    last_run_file = tempfile.mktemp(suffix='.json')

    try:
        env = os.environ.copy()
        env.update({
            "SESSION_FILE": session_file,
            "POSTS_FILE": posts_file,
            "USED_FILE": used_file,
            "LAST_RUN_FILE": last_run_file,
            "THREADS_USERNAME": threads_username,
            "AUTO_COMMENT": "0",
            "THREADS_TOPIC": "",
        })
        result = subprocess.run(
            ["python3", os.path.join(BASE_DIR, "post.py"), SLOT],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        print(result.stdout)
        if result.returncode not in (0, 3):  # 3=cookie expired
            print(f"  stderr: {result.stderr[:500]}")
        return result.returncode
    finally:
        for f in [session_file, posts_file, used_file, last_run_file]:
            try:
                os.unlink(f)
            except Exception:
                pass


def main():
    salons = get_active_salons_playwright()
    if not salons:
        print("Playwright方式のアクティブなサロンがありません")
        return

    print(f"=== SaaS Playwright投稿 [{SLOT}] {len(salons)}件 ===")
    results = {"ok": [], "error": []}

    for salon in salons:
        salon_id = salon["id"]
        salon_name = salon["salon_name"]
        threads_username = salon.get("threads_username") or salon_name

        print(f"\n[{salon_name}] 投稿開始...")

        try:
            used = get_used_posts(salon_id, SLOT)
            text = pick_post(salon_name, SLOT, used)
            print(f"  投稿内容: {text[:50]}...")

            rc = post_with_playwright(salon["session_data"], threads_username, text)

            if rc == 0:
                log_post(salon_id, SLOT, text)
                print(f"  ✅ 投稿完了")
                results["ok"].append(salon_name)
            elif rc == 3:
                print(f"  ⚠️  セッション切れ → 再ログインが必要")
                results["error"].append(f"{salon_name}: セッション切れ")
            else:
                print(f"  ❌ 投稿失敗 (exit={rc})")
                results["error"].append(f"{salon_name}: 投稿失敗")

        except Exception as e:
            print(f"  ❌ エラー: {e}")
            results["error"].append(f"{salon_name}: {e}")

    print(f"\n完了: 成功={len(results['ok'])} 失敗={len(results['error'])}")
    if results["error"]:
        for e in results["error"]:
            print(f"  ✗ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
