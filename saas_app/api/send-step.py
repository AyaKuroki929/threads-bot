from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
import json
import os
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


def _seconds_since(iso: str):
    """step_sent_at からの経過秒。パース不可/未設定なら None。"""
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:
        return None


# 直近この秒数以内に送信済みなら、force が付いていても再送しない（プリフェッチ/ダブルタップ対策）
DEBOUNCE_SECONDS = 120


def mark_step_sent(line_uid: str):
    url = (f"{SUPABASE_URL}/rest/v1/line_users"
           f"?line_user_id=eq.{urllib.parse.quote(line_uid)}")
    data = json.dumps({"step_sent_at": datetime.now(timezone.utc).isoformat()}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={**_supabase_headers(), "Prefer": "return=minimal"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except urllib.error.HTTPError as e:
        raise Exception(f"step_sent_at 更新失敗: {e.code} {e.read()}")


def send_step_message(line_uid: str, customer_id: str):
    connect_url = f"https://saas.shikisai.work/api/connect?customer_id={urllib.parse.quote(customer_id)}"
    text = (
        "Threadsとの連携手順をお送りします📱\n"
        "やることは1つだけ、リンクを開いて「許可」を押すだけです✨\n\n"
        "📱 手順：\n"
        "① スマホのブラウザアプリを開く\n"
        "②「シークレットタブ」または\n"
        "  「プライベートタブ」を開く\n\n"
        "  📍Safari：右下のタブアイコン → 左下「プライベート」\n"
        "  📍Chrome：右下の「︙」→「新しいシークレットタブ」\n\n"
        "③ そのタブに下記の連携URLを貼り付けて開く\n"
        "④ Threads用のInstagramアカウントでログイン →「許可」を押す\n"
        "⑤「✅接続が完了しました」と表示されたら完了です🎉\n\n"
        "⚠️ 必ず「シークレットモード」で開いてください！\n"
        "（別のInstagramアカウントにログイン中だと、\n"
        "違うアカウントが連携されてしまうためです）\n\n"
        "🚫 InstagramアプリやThreadsアプリは\n"
        "  ログアウトしないでください！\n"
        "  普段の利用に影響が出ます。\n\n"
        "🔗 連携URL（コピー用）：\n"
        f"{connect_url}\n\n"
        "うまくいかない場合は、画面のスクリーンショットと一緒に\n"
        "このLINEへお気軽にご返信ください😊"
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

RECENTLY_SENT_HTML = """<!DOCTYPE html>
<html lang="ja">
<head><meta charset="UTF-8"><title>送信済み（重複防止）</title>
<style>body{font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;}
h1{color:#1565c0;}p{color:#555;}</style>
</head>
<body>
<h1>✅ たった今、送信しました</h1>
<p>数秒前にこのクライアントへSTEP1/2を送信済みです。<br>
重複送信を防ぐため、この操作はスキップしました（クライアントに追加のLINEは届きません）。<br>
再送が必要な場合は2分ほど置いてから、もう一度お試しください。</p>
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
        force = query.get("force", ["0"])[0] == "1"  # ?force=1 で送信済みでも強制再送

        if not customer_id:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ERROR_HTML.format(message="customer_id が指定されていません").encode())
            return

        try:
            user = get_line_user(customer_id)

            # 二重送信防止：直近120秒以内に送信済みなら force でもスキップ
            # （URLタップ時のブラウザのプリフェッチ/ダブルタップで2通届くのを防ぐ）
            secs = _seconds_since(user.get("step_sent_at"))
            if secs is not None and secs < DEBOUNCE_SECONDS:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(RECENTLY_SENT_HTML.encode())
                return

            if user.get("step_sent_at") and not force:
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
