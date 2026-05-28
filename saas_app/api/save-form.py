from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import urllib.request
import urllib.parse

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


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
        customer_id = body.get("customer_id", "")
        instagram_url = body.get("instagram_url", "").strip()
        expected_threads_id = body.get("expected_threads_id", "").strip()

        if not customer_id:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"customer_id required"}')
            return

        try:
            save_form_data(customer_id, instagram_url, expected_threads_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())
