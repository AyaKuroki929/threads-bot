from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import os
import urllib.request
import urllib.parse
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")

# とうこさんSaaSの商品ID（同じメールで「うらかたさん」等の別サブスクがあっても
# 確実にとうこさんの顧客を選ぶための判定に使う）
TOUKOSAN_PRODUCT_ID = "prod_UWa5BZv291uQts"


def _headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _stripe_get(path: str) -> dict:
    url = f"https://api.stripe.com/v1/{path}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {STRIPE_SECRET_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _has_toukosan_active_sub(customer_id: str) -> bool:
    """その顧客が とうこさん商品のアクティブなサブスクを持つか。"""
    try:
        sd = _stripe_get("subscriptions?" + urllib.parse.urlencode(
            {"customer": customer_id, "status": "active", "limit": 10}))
        for s in sd.get("data", []):
            for item in (s.get("items", {}).get("data") or []):
                price = item.get("price") or {}
                if price.get("product") == TOUKOSAN_PRODUCT_ID:
                    return True
    except Exception:
        pass
    return False


def find_customer_id_by_email(email: str) -> str:
    """
    フォールバック：customer_id が空のとき、回答メールでStripe顧客を逆引きする。
    フォームの「フォームをクリア」や別リンク経由でプリフィルが消えても、
    メールが決済時と一致していれば customer_id を自動復元できる。

    ⚠️ 同一メールで複数顧客（例：とうこさん¥2,750 と うらかたさん¥12,100）が
       存在しうるので、まず「とうこさん商品のアクティブサブスクを持つ顧客」を選ぶ。
       見つからない場合：候補が1件だけならそれを採用、複数なら曖昧なので '' を返す。
    """
    if not STRIPE_SECRET_KEY or not email:
        return ""
    try:
        data = _stripe_get("customers?" + urllib.parse.urlencode(
            {"email": email, "limit": 10}))
    except Exception:
        return ""
    candidates = [c.get("id") for c in data.get("data", []) if c.get("id")]
    if not candidates:
        return ""
    # 1) とうこさん商品のアクティブ顧客を優先
    for cid in candidates:
        if _has_toukosan_active_sub(cid):
            return cid
    # 2) とうこさん該当なし → 1件だけなら採用、複数なら曖昧なので返さない
    return candidates[0] if len(candidates) == 1 else ""


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
