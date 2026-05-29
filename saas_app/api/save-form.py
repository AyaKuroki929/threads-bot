from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import urllib.request
import urllib.parse

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")


def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def find_customer_id_by_email(email: str) -> str:
    """
    フォールバック：customer_id が空のとき、回答メールでStripe顧客を逆引きする。
    フォームの「フォームをクリア」や別リンク経由でプリフィルが消えても、
    メールが決済時と一致していれば customer_id を自動復元できる。
    完全一致のみ。見つからなければ '' を返す。
    """
    if not STRIPE_SECRET_KEY or not email:
        return ""
    url = "https://api.stripe.com/v1/customers?" + urllib.parse.urlencode(
        {"email": email, "limit": 1}
    )
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        rows = data.get("data", [])
        return rows[0]["id"] if rows else ""
    except Exception:
        return ""


def save_form_data(customer_id: str, instagram_url: str, expected_threads_id: str):
    """フォーム回答時にline_usersへ Instagram URL と Threads ID を保存。"""
    url = (f"{SUPABASE_URL}/rest/v1/line_users"
           f"?stripe_customer_id=eq.{urllib.parse.quote(customer_id)}")
    payload = {}
    if instagram_url:
        payload["instagram_url"] = instagram_url
    if expected_threads_id:
        payload["expected_threads_id"] = expected_threads_id
    if not payload:
        return 204
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(), method="PATCH")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        customer_id = (body.get("customer_id") or "").strip()
        email = (body.get("email") or "").strip()
        instagram_url = (body.get("instagram_url") or "").strip()
        expected_threads_id = (body.get("expected_threads_id") or "").strip()

        # ⭐ フォールバック：customer_id が空でも email でStripe逆引きして復元
        recovered = False
        if not customer_id and email:
            customer_id = find_customer_id_by_email(email)
            recovered = bool(customer_id)

        # line_users へ保存（行が無ければ0件更新になるだけ。customer_idは返す）
        if customer_id:
            try:
                save_form_data(customer_id, instagram_url, expected_threads_id)
            except Exception:
                pass

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "ok": True,
            "customer_id": customer_id,
            "recovered": recovered,
        }).encode())
