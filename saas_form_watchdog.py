"""
とうこさんSaaS フォーム回答 安全網（watchdog）
------------------------------------------------------------
目的：GASのonFormSubmit通知が死んでいても取りこぼさないためのバックアップ検知。
      「フォーム回答済み（シートに行あり）なのに STEP1/2 未送信（line_users.step_sent_at が空）
       かつ 未連携（salons に無い）」のクライアントを検出し、管理者(彩さん)LINEに通知する。

判定は Stripe 不要：フォームの隠しプリフィル列 `_customer_id` を直接使う。

環境変数：
  SUPABASE_URL / SUPABASE_SERVICE_KEY   … Supabase
  ADMIN_NOTIFY_LINE_TOKEN               … Claude通知Bot（彩さん宛て）※未設定ならDRYRUNで表示のみ
ファイル：
  google_service_account.json           … シート読み取り用

シートの回答は「フォームが直接書き込む」ためGASトリガーが死んでいても残る。
そのシートと line_users / salons を突き合わせるので、GAS通知に依存しない。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error

import gspread

SHEET_ID = "1Af6ZnH7Ghzn1APpVrFy5nFlftNaIX5YOeSvfFjSdU-U"

SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY     = os.environ.get("SUPABASE_SERVICE_KEY", "")
ADMIN_LINE_TOKEN = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")

COL_TS    = "タイムスタンプ"
COL_EMAIL = "メールアドレス"
COL_SALON = "サロン名"
COL_OWNER = "オーナー名（投稿で使うお名前）"
COL_TID   = "Threadsのアカウント名（@から始まるID）"
COL_CID   = "_customer_id"


def _supabase_headers():
    return {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


def _supabase_get(path_qs):
    url = f"{SUPABASE_URL}/rest/v1/{path_qs}"
    req = urllib.request.Request(url, headers=_supabase_headers())
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def fetch_line_users_step_map():
    """stripe_customer_id -> step_sent_at（Noneあり）"""
    rows = _supabase_get("line_users?select=stripe_customer_id,step_sent_at")
    m = {}
    for x in rows:
        cid = (x.get("stripe_customer_id") or "").strip()
        if cid:
            m[cid] = x.get("step_sent_at")
    return m


def _normalize_tid(raw):
    """Threads ID正規化：@/＠除去・前後空白除去・小文字化（salon_nameと突合するため）"""
    s = str(raw or "").strip()
    while s and s[0] in ("@", "＠", " ", "　"):
        s = s[1:]
    return s.strip().lower()


def fetch_connected():
    """salons に登録済み（＝連携完了）の customer_id集合 と salon_name(正規化)集合を返す。
    旧クライアントは stripe_customer_id が空のこともあるので salon_name(=Threadsユーザー名)でも突合する。"""
    rows = _supabase_get("salons?select=stripe_customer_id,salon_name")
    cids = {(x.get("stripe_customer_id") or "").strip()
            for x in rows if (x.get("stripe_customer_id") or "").strip()}
    names = {_normalize_tid(x.get("salon_name")) for x in rows if (x.get("salon_name") or "").strip()}
    return cids, names


def read_form_rows():
    # Google Sheets APIは時々5xx（503 The service is currently unavailable等）や429を返す。
    # リトライ無しだと一瞬の一時障害でwatchdogが落ち、誤アラーム＋その回の検知スキップになる。
    # 一時障害は指数バックオフで最大3回リトライ。それでもダメなら例外を上げてLINE🚨（本物の障害は今まで通り気づける）。
    TRANSIENT = {429, 500, 502, 503, 504}
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            gc = gspread.service_account(filename="google_service_account.json")
            ws = gc.open_by_key(SHEET_ID).get_worksheet(0)
            return ws.get_all_records()
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code in TRANSIENT and attempt < max_attempts:
                wait = 5 * (2 ** (attempt - 1))  # 5s → 10s → 20s
                print(f"[watchdog] Sheets API {code} 一時障害。{wait}s後にリトライ ({attempt}/{max_attempts - 1})",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def notify_admin(text):
    """管理者LINE（Claude通知Bot）へ通知。未設定ならDRYRUNで表示のみ。"""
    if not ADMIN_LINE_TOKEN:
        print("=== [DRYRUN] ADMIN_NOTIFY_LINE_TOKEN未設定のため送信せず表示のみ ===")
        print(text)
        return
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=json.dumps({"messages": [{"type": "text", "text": text}]}).encode(),
        headers={"Authorization": f"Bearer {ADMIN_LINE_TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"[notify] admin LINE HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"[notify] admin LINE 失敗 HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[ERROR] SUPABASE_URL/SUPABASE_SERVICE_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    rows = read_form_rows()
    step_map = fetch_line_users_step_map()
    connected_cids, connected_names = fetch_connected()

    def is_connected(cid, tid):
        return (cid and cid in connected_cids) or (_normalize_tid(tid) in connected_names)

    pending = []       # フォーム回答済みだが STEP 未送信 かつ 未連携
    unknown_cid = []   # _customer_id が空 かつ Threads IDでも連携確認できない（手動確認が必要）

    seen_cids = set()
    for r in rows:
        cid   = str(r.get(COL_CID, "") or "").strip()
        salon = str(r.get(COL_SALON, "") or "").strip()
        owner = str(r.get(COL_OWNER, "") or "").strip()
        tid   = str(r.get(COL_TID, "") or "").strip()
        email = str(r.get(COL_EMAIL, "") or "").strip()
        ts    = str(r.get(COL_TS, "") or "").strip()

        if not salon and not email:
            continue  # 空行スキップ

        # 連携完了なら cid有無に関わらず除外（旧クライアントは cid空でも salon_nameで一致する）
        if is_connected(cid, tid):
            continue

        if not cid:
            unknown_cid.append({"salon": salon, "owner": owner, "tid": tid, "email": email, "ts": ts})
            continue

        if cid in seen_cids:
            continue
        seen_cids.add(cid)

        step_sent = step_map.get(cid)
        if step_sent:
            continue  # STEP送信済み → OAuth待ち（oauth_reminder が担当）

        # ここに来る＝フォーム回答済み・STEP未送信・未連携
        pending.append({
            "salon": salon, "owner": owner, "tid": tid, "email": email, "ts": ts,
            "cid": cid, "in_line_users": cid in step_map,
        })

    print(f"[watchdog] 回答行={len(rows)} / 未送信検知={len(pending)} / customer_id不明={len(unknown_cid)}")

    if not pending and not unknown_cid:
        print("[watchdog] 取りこぼしなし。通知不要。")
        return

    lines = ["🛟 SaaS安全網：フォーム回答済みなのに未対応の方を検知しました",
             "（GASの通知が届かなくてもこの安全網が拾います）", ""]

    if pending:
        lines.append(f"■ STEP1/2 未送信（{len(pending)}件）— テスター追加＋下記URLをタップで案内送信")
        for p in pending:
            ntid = _normalize_tid(p["tid"])
            who = " / ".join([b for b in [p["owner"], (f"@{ntid}" if ntid else ""), p["salon"]] if b]) or p["cid"]
            lines.append(f"・{who}")
            lines.append(f"  回答日:{p['ts']}")
            lines.append(f"  https://saas.shikisai.work/api/send-step?customer_id={p['cid']}")
            if not p["in_line_users"]:
                lines.append("  ⚠️ LINE紐付けなし→このURLはエラーになります。先に map_line で紐付けが必要（Claudeに依頼でOK）")
        lines.append("")

    if unknown_cid:
        lines.append(f"■ customer_id不明・手動確認（{len(unknown_cid)}件）")
        for u in unknown_cid:
            ntid = _normalize_tid(u["tid"])
            who = " / ".join([b for b in [u["owner"], (f"@{ntid}" if ntid else ""), u["salon"]] if b]) or u["email"]
            lines.append(f"・{who}（{u['email']}）回答日:{u['ts']}")

    notify_admin("\n".join(lines))


if __name__ == "__main__":
    main()
