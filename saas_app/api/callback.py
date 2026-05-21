from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import urllib.request
import urllib.parse

APP_ID = os.environ.get("THREADS_APP_ID", "985270787180212")
APP_SECRET = os.environ.get("META_APP_SECRET", "")
CALLBACK_URL = os.environ.get("CALLBACK_URL", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
LINE_TOKEN = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")
GITHUB_PAT = os.environ.get("GITHUB_PAT", "")


def trigger_saas_generate(username: str):
    if not GITHUB_PAT:
        return
    payload = json.dumps({
        "ref": "main",
        "inputs": {"salon_name": username},
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/repos/AyaKuroki929/threads-bot/actions/workflows/saas_generate.yml/dispatches",
        data=payload,
        headers={
            "Authorization": f"Bearer {GITHUB_PAT}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def line_notify_oauth_complete(username: str):
    if not LINE_TOKEN:
        return
    text = (
        f"✅ とうこさん OAuth完了！\n\n"
        f"Threads ID：@{username}\n\n"
        f"投稿生成を自動実行しました。"
    )
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def exchange_code(code):
    data = urllib.parse.urlencode({
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "redirect_uri": CALLBACK_URL,
        "code": code,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        "https://graph.threads.net/oauth/access_token",
        data=data,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"exchange_code HTTP {e.code}: {body} | redirect_uri={CALLBACK_URL!r} | app_id={APP_ID!r}")


def get_long_lived_token(short_token):
    params = urllib.parse.urlencode({
        "grant_type": "th_exchange_token",
        "client_secret": APP_SECRET,
        "access_token": short_token,
    })
    url = f"https://graph.threads.net/access_token?{params}"
    try:
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"get_long_lived_token HTTP {e.code}: {body}")


def get_user_info(token):
    url = f"https://graph.threads.net/me?fields=id,username&access_token={urllib.parse.quote(token)}"
    try:
        with urllib.request.urlopen(url) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise Exception(f"get_user_info HTTP {e.code}: {body}")


def get_instagram_url(customer_id: str) -> str:
    """line_usersテーブルからフォーム回答時に保存したinstagram_urlを取得。"""
    if not customer_id:
        return ""
    try:
        url = (f"{SUPABASE_URL}/rest/v1/line_users"
               f"?stripe_customer_id=eq.{urllib.parse.quote(customer_id)}"
               f"&select=instagram_url&limit=1")
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            rows = json.loads(resp.read())
        return (rows[0].get("instagram_url") or "").strip() if rows else ""
    except Exception:
        return ""


def save_to_supabase(user_id, username, access_token, stripe_customer_id=""):
    base_headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    # 既存レコードを確認
    check_url = f"{SUPABASE_URL}/rest/v1/salons?threads_user_id=eq.{urllib.parse.quote(str(user_id))}&select=id"
    check_req = urllib.request.Request(check_url, headers={k: v for k, v in base_headers.items() if k != "Prefer"})
    try:
        with urllib.request.urlopen(check_req) as resp:
            existing = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Exception(f"Supabase check failed HTTP {e.code}: {e.read().decode()}")

    instagram_url = get_instagram_url(stripe_customer_id)

    payload = {
        "salon_name": username,
        "access_token": access_token,
        "is_active": True,
    }
    if stripe_customer_id:
        payload["stripe_customer_id"] = stripe_customer_id
    if instagram_url:
        payload["instagram_url"] = instagram_url

    if existing:
        # 再認証：既存レコードをPATCH
        salon_id = existing[0]["id"]
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/salons?id=eq.{salon_id}",
            data=json.dumps(payload).encode(),
            headers=base_headers,
            method="PATCH"
        )
    else:
        # 新規登録：INSERT
        payload["threads_user_id"] = str(user_id)
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/salons",
            data=json.dumps(payload).encode(),
            headers=base_headers,
            method="POST"
        )

    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        raise Exception(f"Supabase save failed HTTP {e.code}: {e.read().decode()}")


SUCCESS_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>接続完了</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;}
h1{color:#333;}p{color:#666;}</style>
</head>
<body>
<h1>✅ 接続が完了しました</h1>
<p>Threadsアカウントの自動投稿設定が完了しました。<br>
初回の投稿は設定完了後に自動的に開始されます。</p>
</body></html>"""

ERROR_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>エラー</title></head>
<body><h1>❌ エラーが発生しました</h1><p>{message}</p></body></html>"""


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [None])[0]

        if not code:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ERROR_HTML.format(message="認証コードがありません").encode())
            return

        try:
            # state に customer_id が入っている
            customer_id = query.get("state", [""])[0]

            token_data = exchange_code(code)
            short_token = token_data["access_token"]

            long_token_data = get_long_lived_token(short_token)
            access_token = long_token_data["access_token"]

            user_info = get_user_info(access_token)
            username = user_info.get("username", "")
            save_to_supabase(
                user_id=user_info["id"],
                username=username,
                access_token=access_token,
                stripe_customer_id=customer_id,
            )
            trigger_saas_generate(username)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_HTML.encode())

        except Exception as e:
            import logging, traceback
            logging.error("callback error: %s", e)
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            detail = f"{type(e).__name__}: {e}"
            self.wfile.write(ERROR_HTML.format(message=f"認証処理中にエラーが発生しました。<br><br><code>{detail}</code>").encode())
