"""
とうこさんSaaS OAuth未完了リマインダー
- step_sent_at から24時間以上経過してもOAuth未完了のクライアントを検出
- クライアントにリマインドLINEを自動送信
- 彩さん（管理者）にも未完了状況を通知
- oauth_reminded_at に記録して二重送信を防止
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

SUPABASE_URL        = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY        = os.environ.get("SUPABASE_SERVICE_KEY", "")
CLIENT_LINE_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")   # とうこさんLINE（クライアント宛て）
ADMIN_LINE_TOKEN    = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")     # Claude通知Bot（管理者宛て）

REMINDER_HOURS = 24  # この時間以上経過したら対象


def _supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def fetch_pending_users():
    """step_sent_at から24時間以上経過＋oauth_reminded_at が NULL のline_users"""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=REMINDER_HOURS)).isoformat()
    url = (
        f"{SUPABASE_URL}/rest/v1/line_users"
        f"?step_sent_at=lt.{urllib.parse.quote(cutoff)}"
        f"&oauth_reminded_at=is.null"
        f"&select=line_user_id,stripe_customer_id,step_sent_at,display_name,expected_threads_id"
    )
    req = urllib.request.Request(url, headers=_supabase_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def salon_exists(customer_id: str) -> bool:
    """このcustomer_idに対応するsalonがSupabaseに存在するか（OAuth完了済みか）"""
    if not customer_id:
        return False
    url = (
        f"{SUPABASE_URL}/rest/v1/salons"
        f"?stripe_customer_id=eq.{urllib.parse.quote(customer_id)}"
        f"&select=id&limit=1"
    )
    req = urllib.request.Request(url, headers=_supabase_headers())
    with urllib.request.urlopen(req, timeout=10) as r:
        rows = json.loads(r.read())
    return len(rows) > 0


def send_client_reminder(line_uid: str, customer_id: str):
    """クライアントにリマインドLINEを送信"""
    connect_url = f"https://saas.shikisai.work/api/connect?customer_id={urllib.parse.quote(customer_id)}"
    text = (
        "こんにちは！😊\n\n"
        "Threadsアカウントとの連携がまだ完了していないようです。\n"
        "設定が完了するまで自動配信が始まりません💦\n\n"
        "お手数ですが、以下の手順をお試しください👇\n\n"
        "🔴 STEP 1\n"
        "Threadsアプリ → プロフィール右上≡ → その他の設定 → "
        "ウェブサイトのアクセス許可 →「Invites」タブ →"
        "「Threads Auto Post」の【同意する】を押す\n\n"
        "🔴 STEP 2\n"
        f"{connect_url}\n"
        "↑ こちらをタップ → Instagramでログイン → 連携完了！\n\n"
        "ご不明な点があればお気軽にこのLINEへご返信ください🙏"
    )
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/push",
        data=json.dumps({"to": line_uid, "messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {CLIENT_LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status


def mark_reminded(line_uid: str):
    """oauth_reminded_at を記録"""
    url = (f"{SUPABASE_URL}/rest/v1/line_users"
           f"?line_user_id=eq.{urllib.parse.quote(line_uid)}")
    data = json.dumps({"oauth_reminded_at": datetime.now(timezone.utc).isoformat()}).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={**_supabase_headers(), "Prefer": "return=minimal"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass


def notify_admin(text: str):
    """管理者LINE（Claude通知Bot）に通知"""
    if not ADMIN_LINE_TOKEN:
        return
    try:
        req = urllib.request.Request(
            "https://api.line.me/v2/bot/message/broadcast",
            data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
            headers={"Authorization": f"Bearer {ADMIN_LINE_TOKEN}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[admin notify failed] {e}", file=sys.stderr)


def _hours_since(iso: str) -> int:
    try:
        sent = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - sent
        return int(delta.total_seconds() / 3600)
    except Exception:
        return 0


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[ERROR] SUPABASE_URL/SUPABASE_SERVICE_KEY が未設定", file=sys.stderr)
        sys.exit(1)

    print(f"[oauth_reminder] 開始 {datetime.now(timezone.utc).isoformat()}")

    candidates = fetch_pending_users()
    print(f"[oauth_reminder] step_sent_at から{REMINDER_HOURS}時間超のリマインド未送信ユーザー: {len(candidates)}件")

    sent_list = []
    skipped_list = []

    for user in candidates:
        customer_id = user.get("stripe_customer_id", "")
        line_uid    = user.get("line_user_id", "")
        step_sent   = user.get("step_sent_at", "")
        threads_id  = user.get("expected_threads_id") or "（未登録）"
        display     = user.get("display_name") or threads_id or customer_id

        if not line_uid or not customer_id:
            print(f"[skip] line_user_id/customer_id 不足: {user}")
            continue

        # OAuth完了済みなら何もしない（リマインド不要）
        if salon_exists(customer_id):
            print(f"[skip] {display}: OAuth完了済み → リマインド不要")
            skipped_list.append(display)
            continue

        hours = _hours_since(step_sent)

        try:
            send_client_reminder(line_uid, customer_id)
            mark_reminded(line_uid)
            print(f"[sent] {display}: リマインド送信完了（経過{hours}時間）")
            sent_list.append(f"{display}（@{threads_id}・経過{hours}h）")
        except Exception as e:
            detail = str(e)
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = f"{e} | {e.read().decode('utf-8', 'replace')[:400]}"
                except Exception:
                    pass
            print(f"[ERROR] {display}: リマインド送信失敗 → {detail}", file=sys.stderr)
            notify_admin(
                f"⚠️ OAuthリマインド送信失敗\n\n"
                f"クライアント: {display}\n"
                f"Threads ID: {threads_id}\n"
                f"エラー: {detail}\n\n"
                f"手動でフォロー検討してください。"
            )

    if sent_list:
        notify_admin(
            f"⏰ とうこさん OAuth未完了リマインド送信（{len(sent_list)}件）\n\n"
            + "\n".join(f"・{s}" for s in sent_list)
            + "\n\nクライアントにLINEで自動リマインドしました。\n"
            "3日以上完了しない場合は個別フォロー検討してください。"
        )

    print(f"[oauth_reminder] 完了: 送信{len(sent_list)}件 / スキップ{len(skipped_list)}件")


if __name__ == "__main__":
    main()
