"""
App Review用テスト投稿スクリプト
スクリーンキャスト録画時に使用。threads_content_publish の動作確認。
実行後はテスト投稿を手動で削除すること。
"""
import json
import os
import urllib.request
import urllib.parse
import sys

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

TEST_TEXT = "【動作確認】とうこさん自動投稿サービスのテスト投稿です。"


def get_token_from_supabase(username: str) -> str:
    url = f"{SUPABASE_URL}/rest/v1/salons?salon_name=eq.{urllib.parse.quote(username)}&select=access_token"
    req = urllib.request.Request(
        url,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if not data:
        raise Exception(f"サロン '{username}' がSupabaseに見つかりません")
    return data[0]["access_token"]


def create_post_container(token: str, text: str) -> str:
    """Step1: メディアコンテナ作成"""
    data = urllib.parse.urlencode({
        "media_type": "TEXT",
        "text": text,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(
        "https://graph.threads.net/v1.0/me/threads",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        print(f"✅ コンテナ作成: {result}")
        return result["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"コンテナ作成失敗 HTTP {e.code}: {body}")


def publish_post(token: str, creation_id: str) -> str:
    """Step2: 投稿公開"""
    data = urllib.parse.urlencode({
        "creation_id": creation_id,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(
        "https://graph.threads.net/v1.0/me/threads_publish",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        print(f"✅ 投稿公開成功: post_id={result['id']}")
        return result["id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"投稿公開失敗 HTTP {e.code}: {body}")


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "bemolle_diet"
    print(f"テスト投稿開始: @{username}")
    token = get_token_from_supabase(username)
    creation_id = create_post_container(token, TEST_TEXT)
    post_id = publish_post(token, creation_id)
    print(f"\n投稿URL: https://www.threads.net/@{username}/post/{post_id}")
    print("録画後は上記投稿を削除してください")
