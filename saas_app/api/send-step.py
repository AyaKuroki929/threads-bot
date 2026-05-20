from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import json
import os
import sys
import urllib.request
import urllib.parse
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
LINE_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_line_user(customer_id: str) -> dict:
    url = (f"{SUPABASE_URL}/rest/v1/line_users"
           f"?stripe_customer_id=eq.{urllib.parse.quote(customer_id)}"
           f"&select=line_user_id,step_sent_at&limit=1")
    req = urllib.request.Request(url, headers=_supabase_headers())
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.loads(r.read())
    if not rows:
        raise Exception(f"LINEユーザーIDが見つかりません（customer_id={customer_id}）")
    return rows[0]


def mark_step_sent(line_uid: str):
    now_str = datetime.now(timezone.utc).isoformat()
    url = (f"{SUPABASE_URL}/rest/v1/line_users"
           f"?line_user_id=eq.{urllib.parse.quote(line_uid)}")
    data = json.dumps({"step_sent_at": now_str}).encode()
    print(f"[mark_step_sent] PATCH url={url}", file=sys.stderr, flush=True)
    print(f"[mark_step_sent] data={data}", file=sys.stderr, flush=True)
    req = urllib.request.Request(
        url, data=data,
        headers={**_supabase_headers(), "Prefer": "return=representation"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            print(f"[mark_step_sent] status={r.status} body={body[:300]}", file=sys.stderr, flush=True)
    except urllib.error.HTTPError as e:
        body = e.read()
        print(f"[mark_step_sent] HTTPError {e.code}: {body[:300]}", file=sys.stderr, flush=True)
        raise


def send_step_message(line_uid: str, customer_id: str):
    connect_url = f"https://saas.shikisai.work/api/connect?customer_id={urllib.parse.quote(customer_id)}"
    text = (
        "Threadsとの連携手順をお送りします📱\n\n"
        "⚠️ 必ずSTEP1→STEP2の順番で行ってください。\n"
        "順番を守らないと連携できません。\n\n"
        "🔴STEP 1（先にこちらから）\n"
        "Threadsアプリ → プロフィール右上≡ → 設定 → アカウント → "
        "ウェブサイトのアクセス許可 →「Invites」タブ →「Threads Auto Post」の【同意する】を押してください\n\n"
        "🔴STEP 2（STEP1完了後に）\n"
        "下記URLを開いて、Instagramアカウントでログインし\n"
        "連携を完了してください。\n"
        f"{connect_url}\n\n"
        "ご不明な点はいつでもお気軽にご連絡ください😊"
    )
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": line_uid, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


ALREADY_SENT_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>送信済み</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;}
h1{color:#e65100;}p{color:#555;}</style>
</head>
<body>
<h1>⚠️ 送信済みです</h1>
<p>このクライアントにはすでにSTEP1/2を送信しています。<br>
再送する場合は Supabase で step_sent_at を NULL に更新してください。</p>
</body></html>"""

SUCCESS_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>送信完了</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;}
h1{color:#2e7d32;}p{color:#555;}</style>
</head>
<body>
<h1>✅ 送信完了！</h1>
<p>STEP1/2の手順をクライアントのLINEに送信しました。<br>
クライアントが手順を完了すると、自動で投稿設定が開始されます。</p>
</body></html>"""

ERROR_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>エラー</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;">
<h1>❌ エラーが発生しました</h1><p>{message}</p></body></html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        customer_id = query.get("customer_id", [None])[0]

        if not customer_id:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ERROR_HTML.format(message="customer_id が指定されていません").encode())
            return

        try:
            user = get_line_user(customer_id)
            if user.get("step_sent_at"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(ALREADY_SENT_HTML.encode())
                return

            send_step_message(user["line_user_id"], customer_id)
            mark_step_sent(user["line_user_id"])

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_HTML.encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            detail = f"{type(e).__name__}: {e}"
            self.wfile.write(ERROR_HTML.format(message=f"エラーが発生しました。<br><br><code>{detail}</code>").encode())
