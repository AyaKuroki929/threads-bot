"""
SaaS版 Threads自動投稿スクリプト
Supabaseから全アクティブサロンのトークンを読み込み、
公式 Threads API で投稿する。
使い方: python3 post_saas.py morning / python3 post_saas.py evening
"""
import sys
import json
import os
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
POSTS_DIR = os.path.join(os.path.dirname(__file__), "posts_saas")

SLOT = sys.argv[1] if len(sys.argv) > 1 else "morning"  # morning / evening
THREADS_API = "https://graph.threads.net/v1.0"


def supabase_get(path, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def supabase_post(path, data):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status


def get_active_salons():
    return supabase_get("salons", {"is_active": "eq.true", "select": "id,salon_name,threads_user_id,access_token"})


def get_used_posts(salon_id, slot):
    rows = supabase_get("post_logs", {
        "salon_id": f"eq.{salon_id}",
        "slot": f"eq.{slot}",
        "select": "post_content",
    })
    return {r["post_content"] for r in rows}


def pick_post(salon_name, slot, used_texts):
    posts_file = os.path.join(POSTS_DIR, f"posts_{salon_name}.json")
    if not os.path.exists(posts_file):
        raise FileNotFoundError(f"投稿ファイルが見つかりません: {posts_file}")

    with open(posts_file) as f:
        data = json.load(f)

    candidates = data.get(slot, [])
    if not candidates:
        raise ValueError(f"{salon_name}: {slot} スロットの投稿がありません")

    unused = [p for p in candidates if p not in used_texts]
    if not unused:
        # 全部使い切ったらリセット
        unused = candidates

    return random.choice(unused)


class TokenExpiredError(Exception):
    pass


def get_user_id_from_token(token):
    """トークンから実際のuser_idを取得（/me エンドポイント）"""
    url = f"{THREADS_API}/me?fields=id,username&access_token={token}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        d = json.loads(resp.read())
    return str(d["id"]), d.get("username", "")


def supabase_patch(path, data, params):
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
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def threads_post(user_id, token, text):
    # Step 1: コンテナ作成
    create_url = f"{THREADS_API}/{user_id}/threads"
    create_data = urllib.parse.urlencode({
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(create_url, data=create_data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            creation_id = json.loads(resp.read())["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code in (401, 403) or "token" in body.lower():
            raise TokenExpiredError(f"トークン切れ HTTP {e.code}: {body[:150]}")
        raise RuntimeError(f"コンテナ作成失敗 HTTP {e.code}: {body[:150]}")

    time.sleep(3)

    # Step 2: 公開
    publish_url = f"{THREADS_API}/{user_id}/threads_publish"
    publish_data = urllib.parse.urlencode({
        "creation_id": creation_id,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(publish_url, data=publish_data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"公開失敗 HTTP {e.code}: {body[:150]}")


def log_post(salon_id, slot, text):
    supabase_post("post_logs", {
        "salon_id": salon_id,
        "slot": slot,
        "post_content": text,
        "posted_at": datetime.now(timezone.utc).isoformat(),
    })


def main():
    salons = get_active_salons()
    if not salons:
        print("アクティブなサロンがありません")
        return

    results = {"ok": [], "error": [], "token_expired": []}

    for salon in salons:
        salon_id = salon["id"]
        salon_name = salon["salon_name"]
        user_id = salon["threads_user_id"]
        token = salon["access_token"]

        try:
            # threads_user_id が未設定なら /me で取得して Supabase に保存
            if not user_id:
                print(f"[{salon_name}] threads_user_id 未設定 → /me で取得中...")
                user_id, uname = get_user_id_from_token(token)
                supabase_patch("salons", {"threads_user_id": user_id}, {"id": f"eq.{salon_id}"})
                print(f"[{salon_name}] user_id={user_id} (@{uname}) を Supabase に保存")

            used = get_used_posts(salon_id, SLOT)
            text = pick_post(salon_name, SLOT, used)
            post_id = threads_post(user_id, token, text)
            log_post(salon_id, SLOT, text)
            print(f"[OK] {salon_name}: post_id={post_id}")
            results["ok"].append(salon_name)
        except TokenExpiredError as e:
            print(f"[TOKEN_EXPIRED] {salon_name}: {e}")
            results["token_expired"].append(salon_name)
        except Exception as e:
            print(f"[ERROR] {salon_name}: {e}")
            results["error"].append(f"{salon_name}: {e}")

    print(f"\n完了: 成功={len(results['ok'])} 失敗={len(results['error'])} トークン切れ={len(results['token_expired'])}")
    if results["token_expired"]:
        sys.exit(3)  # token expiry → workflow側で専用通知
    if results["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
