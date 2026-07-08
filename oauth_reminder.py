"""
とうこさんSaaS OAuth未完了リマインダー
- 1回目：step_sent_at から24時間以上経過してもOAuth未完了のクライアントに自動送信
         → oauth_reminded_at（Supabase）に記録して二重送信を防止
- 2回目：1回目から72時間（3日）経過してもなおOAuth未完了なら、もう1回だけ送信
         → oauth_reminder_state.json（リポジトリにコミット）に記録して二重送信を防止
- 彩さん（管理者）にも送信状況を通知
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

from botlib import line_broadcast, line_push

SUPABASE_URL        = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY        = os.environ.get("SUPABASE_SERVICE_KEY", "")
CLIENT_LINE_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")   # とうこさんLINE（クライアント宛て）
ADMIN_LINE_TOKEN    = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")     # Claude通知Bot（管理者宛て）

REMINDER_HOURS  = 24  # 1回目：STEP送信からこの時間以上経過したら対象
REMINDER2_HOURS = 72  # 2回目：1回目リマインドからこの時間以上経過したら対象（3日）
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oauth_reminder_state.json")


def _load_state():
    """2回目リマインドの送信済み記録 {"reminded2": {line_user_id: iso_ts}}"""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"reminded2": {}}


def _save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


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


def fetch_reminded_users():
    """1回目リマインド済み（oauth_reminded_at あり）のline_users（2回目の候補）"""
    url = (
        f"{SUPABASE_URL}/rest/v1/line_users"
        f"?oauth_reminded_at=not.is.null"
        f"&select=line_user_id,stripe_customer_id,oauth_reminded_at,display_name,expected_threads_id"
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
    return line_push(line_uid, text, token=CLIENT_LINE_TOKEN)


def send_client_reminder2(line_uid: str, customer_id: str):
    """2回目（最終）のリマインドLINE。詰まりやすいポイントを添えて送る"""
    connect_url = f"https://saas.shikisai.work/api/connect?customer_id={urllib.parse.quote(customer_id)}"
    text = (
        "こんにちは😊 その後、Threadsの連携はいかがですか？\n\n"
        "連携が完了するまで自動投稿が始まらないため、\n"
        "改めてご案内をお送りしますね🙏\n\n"
        "よく詰まりやすいポイントは2つです👇\n\n"
        "❶ STEP1の承認がまだ\n"
        "Threadsアプリ → プロフィール右上≡ → その他の設定 → "
        "ウェブサイトのアクセス許可 →「Invites」タブ →"
        "「Threads Auto Post」の【同意する】\n\n"
        "❷ STEP2を「シークレットモード」で開いていない\n"
        f"{connect_url}\n"
        "↑ ブラウザのシークレットタブ（プライベートタブ）に貼って開く → "
        "Threads用のInstagramアカウントでログイン → 許可\n\n"
        "「途中でエラーが出る」「やり方がわからない」など、\n"
        "どんなことでもこのLINEにご返信ください😊\n"
        "スクリーンショットを送っていただければ、こちらで確認してご案内します！"
    )
    return line_push(line_uid, text, token=CLIENT_LINE_TOKEN)


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
    """管理者LINE（Claude通知Bot）に通知（実装は botlib.line_broadcast・失敗は握って継続）"""
    if not ADMIN_LINE_TOKEN:
        return
    line_broadcast(text, token=ADMIN_LINE_TOKEN)


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
        threads_id  = (user.get("expected_threads_id") or "").strip()
        name        = (user.get("display_name") or "").strip()
        # 通知に出す識別子：名前 / @Threads ID / customer_id のうち有るものを並べる（何かは必ず出す）
        _bits = [b for b in [name, (f"@{threads_id}" if threads_id else ""), customer_id] if b]
        who         = " / ".join(_bits) if _bits else "（ID不明）"
        display     = name or threads_id or customer_id or "（ID不明）"

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
            sent_list.append(f"{who}・経過{hours}h")
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
                f"クライアント: {who}\n"
                f"エラー: {detail}\n\n"
                f"手動でフォロー検討してください。"
            )

    # ============ 2回目リマインド（1回目から72時間後・1回だけ） ============
    state = _load_state()
    reminded2_map = state.get("reminded2", {})
    sent2_list = []

    for user in fetch_reminded_users():
        customer_id = user.get("stripe_customer_id", "")
        line_uid    = user.get("line_user_id", "")
        reminded_at = user.get("oauth_reminded_at", "")
        threads_id  = (user.get("expected_threads_id") or "").strip()
        name        = (user.get("display_name") or "").strip()
        _bits = [b for b in [name, (f"@{threads_id}" if threads_id else ""), customer_id] if b]
        who     = " / ".join(_bits) if _bits else "（ID不明）"
        display = name or threads_id or customer_id or "（ID不明）"

        if not line_uid or not customer_id:
            continue
        if line_uid in reminded2_map:
            continue  # 2回目送信済み
        if _hours_since(reminded_at) < REMINDER2_HOURS:
            continue  # まだ3日経っていない
        if salon_exists(customer_id):
            continue  # 連携完了済み

        try:
            send_client_reminder2(line_uid, customer_id)
            reminded2_map[line_uid] = datetime.now(timezone.utc).isoformat()
            print(f"[sent2] {display}: 2回目リマインド送信完了（1回目から{_hours_since(reminded_at)}時間）")
            sent2_list.append(f"{who}・1回目から{_hours_since(reminded_at)}h")
        except Exception as e:
            detail = str(e)
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = f"{e} | {e.read().decode('utf-8', 'replace')[:400]}"
                except Exception:
                    pass
            print(f"[ERROR] {display}: 2回目リマインド送信失敗 → {detail}", file=sys.stderr)
            notify_admin(
                f"⚠️ OAuth 2回目リマインド送信失敗\n\n"
                f"クライアント: {who}\n"
                f"エラー: {detail}\n\n"
                f"手動でフォロー検討してください。"
            )

    if sent2_list:
        state["reminded2"] = reminded2_map
        _save_state(state)

    if sent_list or sent2_list:
        parts = []
        if sent_list:
            parts.append(f"■ 1回目（24時間経過）{len(sent_list)}件\n" + "\n".join(f"・{s}" for s in sent_list))
        if sent2_list:
            parts.append(f"■ 2回目・最終（1回目から3日）{len(sent2_list)}件\n" + "\n".join(f"・{s}" for s in sent2_list))
        notify_admin(
            "⏰ とうこさん OAuth未完了リマインド送信\n\n"
            + "\n\n".join(parts)
            + "\n\n自動リマインドはここまで。2回目でも動きがなければ個別フォローしてください。"
        )

    print(f"[oauth_reminder] 完了: 1回目{len(sent_list)}件 / 2回目{len(sent2_list)}件 / スキップ{len(skipped_list)}件")


if __name__ == "__main__":
    main()
