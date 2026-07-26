#!/usr/bin/env python3
"""手動で1本だけThreadsに投稿する（画像付き対応）。

定期投稿（post_api.py）とは別系統。告知・宣伝など、その場で決めた1本を
GitHub Actions の workflow_dispatch から流すために使う。

使い方:
  THREADS_ACCESS_TOKEN=xxx python3 manual_post.py --text "本文" [--image-url https://...]

画像は「Threadsサーバーから取得できる公開URL」でなければならない（JPEG/PNG・8MB以下）。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

THREADS_API = "https://graph.threads.net/v1.0"


def _get(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _post(url, payload):
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code}: {body[:400]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--image-url", default="")
    ap.add_argument("--dry-run", action="store_true")
    # コンテナ作成までで止める。Threads側が画像URLを取得できるかだけを、
    # 実際には投稿せずに確かめるための検証モード。
    ap.add_argument("--container-only", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        print("[fatal] THREADS_ACCESS_TOKEN が未設定です")
        return 1

    text = args.text.strip()
    if len(text) > 500:
        print(f"[fatal] 本文が{len(text)}字（上限500字）です。短くしてください")
        return 1

    me = _get(f"{THREADS_API}/me?fields=id,username&access_token={urllib.parse.quote(token)}")
    user_id, username = str(me.get("id", "")), me.get("username", "")
    print(f"[token] /me → id={user_id} username={username}")
    if not user_id:
        print("[fatal] user_id が取得できませんでした")
        return 1

    payload = {"access_token": token, "text": text}
    if args.image_url:
        payload["media_type"] = "IMAGE"
        payload["image_url"] = args.image_url
    else:
        payload["media_type"] = "TEXT"

    print(f"[plan] account=@{username} media={payload['media_type']} len={len(text)}")
    print("---- 本文 ----")
    print(text)
    print("--------------")
    if args.dry_run:
        print("[dry-run] 投稿しませんでした")
        return 0

    creation_id = _post(f"{THREADS_API}/{user_id}/threads", payload)["id"]
    if args.container_only:
        print(f"[ok] コンテナ作成に成功しました creation_id={creation_id}（公開はしていません）")
        return 0
    print(f"[create] creation_id={creation_id} → 30秒待機")
    time.sleep(30)

    post_id = _post(f"{THREADS_API}/{user_id}/threads_publish",
                    {"access_token": token, "creation_id": creation_id})["id"]
    print(f"[ok] 投稿しました post_id={post_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
