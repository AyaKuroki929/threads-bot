"""
とうこさんSaaS OAuth未完了リマインダー
- 1回目：step_sent_at から24時間以上経過してもOAuth未完了のクライアントに自動送信
         → oauth_reminded_at（Supabase）に記録して二重送信を防止
- 2回目：1回目から72時間（3日）経過してもなおOAuth未完了なら、もう1回だけ送信
         → oauth_reminder_state.json（privateリポ saas-posts に保管・workflowが往復コピー）に記録して二重送信を防止
- 彩さん（管理者）にも送信状況を通知
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

import botlib
from botlib import line_broadcast, line_push

SUPABASE_URL        = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY        = os.environ.get("SUPABASE_SERVICE_KEY", "")
CLIENT_LINE_TOKEN   = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")   # とうこさんLINE（クライアント宛て）
ADMIN_LINE_TOKEN    = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")     # Claude通知Bot（管理者宛て）

SHEET_ID = "1Af6ZnH7Ghzn1APpVrFy5nFlftNaIX5YOeSvfFjSdU-U"  # サロン情報フォーム回答シート

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
    return botlib.json_list_retry(req, timeout=15, label="Supabase(連携待ち一覧)")


def _load_sheet_identity_map():
    """フォーム回答シートから customer_id → {name, tid, insta} の対応表を作る。
    line_users の display_name/expected_threads_id が空（GAS不調時の回答など）でも
    通知に必ず名前が出せるようにするため。読めない環境では空dictを返して従来動作。"""
    try:
        import gspread
        ws = gspread.service_account(filename="google_service_account.json") \
                    .open_by_key(SHEET_ID).get_worksheet(0)
        m = {}
        for r in ws.get_all_records():
            cid = str(r.get("_customer_id", "") or "").strip()
            if not cid:
                continue
            tid = str(r.get("Threadsのアカウント名（@から始まるID）", "") or "").strip()
            while tid and tid[0] in ("@", "＠", " ", "　"):
                tid = tid[1:]
            m[cid] = {
                "name":  str(r.get("オーナー名（投稿で使うお名前）", "") or "").strip(),
                "tid":   tid.strip().lower(),
                "insta": str(r.get("インスタグラムのURL", "") or "").strip(),
            }
        return m
    except Exception as e:
        print(f"[sheet] 対応表の読み込みスキップ: {e}")
        return {}


def _backfill_line_user(line_uid: str, name: str, tid: str,
                        cur_name: str, cur_tid: str):
    """line_users の空フィールドだけをシート値で自動補完（自己修復・既存値は上書きしない）"""
    payload = {}
    if name and not cur_name:
        payload["display_name"] = name
    if tid and not cur_tid:
        payload["expected_threads_id"] = tid
    if not payload:
        return
    url = (f"{SUPABASE_URL}/rest/v1/line_users"
           f"?line_user_id=eq.{urllib.parse.quote(line_uid)}")
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={**_supabase_headers(), "Prefer": "return=minimal"},
        method="PATCH",
    )
    try:
        botlib.urlopen_retry(req, timeout=10, label="Supabase(補完)")
        print(f"[backfill] {line_uid[:8]}…: {list(payload.keys())} をシートから補完")
    except Exception as e:
        print(f"[backfill] 失敗（続行）: {e}", file=sys.stderr)


def fetch_reminded_users():
    """1回目リマインド済み（oauth_reminded_at あり）のline_users（2回目の候補）"""
    url = (
        f"{SUPABASE_URL}/rest/v1/line_users"
        f"?oauth_reminded_at=not.is.null"
        f"&select=line_user_id,stripe_customer_id,oauth_reminded_at,display_name,expected_threads_id"
    )
    req = urllib.request.Request(url, headers=_supabase_headers())
    return botlib.json_list_retry(req, timeout=15, label="Supabase(リマインド済み一覧)")


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
    rows = botlib.json_list_retry(req, timeout=10, label="Supabase(連携確認)")
    return len(rows) > 0


def send_client_reminder(line_uid: str, customer_id: str):
    """クライアントにリマインドLINEを送信"""
    connect_url = f"https://saas.shikisai.work/api/connect?customer_id={urllib.parse.quote(customer_id)}"
    text = (
        "こんにちは！😊\n\n"
        "Threadsアカウントとの連携がまだ完了していないようです。\n"
        "設定が完了するまで自動配信が始まりません💦\n\n"
        "やることは1つだけです👇\n\n"
        "下記の連携URLをスマホの「シークレットタブ」に貼って開き、\n"
        "Threads用のInstagramアカウントでログイン →「許可」を押してください。\n"
        "「✅接続が完了しました」と表示されたら完了です🎉\n\n"
        f"{connect_url}\n\n"
        "ご不明な点があればお気軽にこのLINEへご返信ください🙏"
    )
    return line_push(line_uid, text, token=CLIENT_LINE_TOKEN)


def send_client_reminder2(line_uid: str, customer_id: str):
    """2回目（最終）のリマインドLINE。詰まりやすいポイントを添えて送る"""
    connect_url = f"https://saas.shikisai.work/api/connect?customer_id={urllib.parse.quote(customer_id)}"
    text = (
        "こんにちは😊 その後、Threadsの連携はいかがですか？\n\n"
        "連携が完了するまで自動投稿が始まらないため、\n"
        "改めてご案内を送らせていただきます🙏\n\n"
        "よく詰まりやすいポイントは2つです👇\n\n"
        "❶「シークレットモード」で開いていない\n"
        f"{connect_url}\n"
        "↑ ブラウザのシークレットタブ（プライベートタブ）に貼って開いてください。\n"
        "　📍Safari：右下のタブアイコン → 左下「プライベート」\n"
        "　📍Chrome：右下の「︙」→「新しいシークレットタブ」\n\n"
        "❷ 別のInstagramアカウントでログインしている\n"
        "Threads用のアカウントでログインし直して「許可」を押してください。\n"
        "「✅接続が完了しました」と表示されたら完了です🎉\n\n"
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
    # ⚠️ ここは「リマインドを送った」印。失敗を握りつぶすと同じ人に何度も届く
    botlib.urlopen_retry(req, timeout=10, label="Supabase(送信済みの記録)")


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


# 1件送ったあと「送信済み」を記録し終えるのに要る余裕（秒）。
# これを割ったら、新しく送らずに次回へ回す＝送ったのに記録できない状態を作らない
RESERVE_FOR_RECORD = 40


def main():
    # ステップ上限360秒より短くする。さらに「送信→記録」の途中で切れないよう、
    # 送信前に RESERVE_FOR_RECORD 秒の余裕を確認する（2026-09-15 Sol指摘①）
    botlib.start_run(280)
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[ERROR] SUPABASE_URL/SUPABASE_SERVICE_KEY が未設定", file=sys.stderr)
        sys.exit(1)

    print(f"[oauth_reminder] 開始 {datetime.now(timezone.utc).isoformat()}")

    sheet_map = _load_sheet_identity_map()

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
        # 空ならフォーム回答シートから補完（通知に必ず名前を出す＋Supabaseも自己修復）
        _sheet = sheet_map.get(customer_id, {})
        if (not name or not threads_id) and _sheet:
            if line_uid:
                _backfill_line_user(line_uid, _sheet.get("name", ""), _sheet.get("tid", ""), name, threads_id)
            name       = name or _sheet.get("name", "")
            threads_id = threads_id or _sheet.get("tid", "")
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
        _sheet = sheet_map.get(customer_id, {})
        if (not name or not threads_id) and _sheet:
            if line_uid:
                _backfill_line_user(line_uid, _sheet.get("name", ""), _sheet.get("tid", ""), name, threads_id)
            name       = name or _sheet.get("name", "")
            threads_id = threads_id or _sheet.get("tid", "")
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

        if botlib.run_time_left() < RESERVE_FOR_RECORD:
            print(f"[skip] {display}: 残り時間が足りないので次回に回します（送信途中で切れないため）")
            continue
        try:
            send_client_reminder2(line_uid, customer_id)
            reminded2_map[line_uid] = datetime.now(timezone.utc).isoformat()
            # ⚠️ 送った直後に記録する。ループの最後にまとめて保存すると、途中で落ちた回に
            # 記録が丸ごと消え、次回この人へもう一度送ってしまう（クライアント宛の二重送信）
            state["reminded2"] = reminded2_map
            _save_state(state)
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

    if sent2_list:      # 念のためもう一度（送信直後に保存済み）
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
        )

    print(f"[oauth_reminder] 完了: 1回目{len(sent_list)}件 / 2回目{len(sent2_list)}件 / スキップ{len(skipped_list)}件")


if __name__ == "__main__":
    main()
