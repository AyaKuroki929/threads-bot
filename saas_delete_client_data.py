"""
とうこさんSaaS クライアントデータ削除スクリプト（データ削除依頼・契約終了時用）
data-deletion.html で公約した削除（トークン無効化・Meta由来データ・フォーム由来データ）を実行する。

使い方（GitHub Actions saas_delete_client_data.yml から、または手動）:
  python3 saas_delete_client_data.py <Threadsユーザー名/salon_name または stripe_customer_id> [--dry-run]
  （@は付いていても外す。salon_name＝Threadsユーザー名）

削除対象:
  1. salons 行（access_token・threads_user_id を含む。トークンは当社システムから削除し以後不使用。
     Threads側の連携許可の取り消しはお客様自身の操作で完結＝data-deletion.htmlに手順明記）
  2. post_logs（当サービスが公開した投稿の記録）
  3. line_users 行（stripe_customer_id / expected_threads_id / line_user_id の3経路で削除）
  ※ Stripe上の決済・会計記録は法令保存のため削除しない（data-deletion.htmlに明記済み）
  ※ 投稿プールファイル（private リポ）は workflow(saas_delete_client_data.yml) が続けて削除する
"""
import json
import os
import sys
import urllib.parse
import urllib.request

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")


def _h(extra=None):
    h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _req(method, path_qs, body=None, prefer=None):
    url = f"{SUPABASE_URL}/rest/v1/{path_qs}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=_h({"Prefer": prefer} if prefer else None))
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    ident = sys.argv[1].strip()
    # 利用者は「@ユーザー名」で依頼してくる（data-deletion.html）ため@や空白を正規化
    while ident and ident[0] in ("@", "＠"):
        ident = ident[1:]
    ident = ident.strip()
    dry = "--dry-run" in sys.argv

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[ERROR] SUPABASE_URL/SUPABASE_SERVICE_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    # salons を salon_name / stripe_customer_id の両方で探す
    salons = []
    for col in ("salon_name", "stripe_customer_id"):
        salons += _req("GET", f"salons?{col}=eq.{urllib.parse.quote(ident)}&select=id,salon_name,stripe_customer_id")
    # 重複除去
    salons = list({s["id"]: s for s in salons}.values())

    customer_ids = {s.get("stripe_customer_id") for s in salons if s.get("stripe_customer_id")}
    if ident.startswith("cus_"):
        customer_ids.add(ident)

    # line_users を expected_threads_id でも探す（customer_id紐付けが無い行の取り残し防止）
    lu_extra = _req("GET", f"line_users?expected_threads_id=eq.{urllib.parse.quote(ident.lower())}&select=line_user_id,stripe_customer_id")
    for r in lu_extra:
        if r.get("stripe_customer_id"):
            customer_ids.add(r["stripe_customer_id"])
    lu_uids = {r["line_user_id"] for r in lu_extra}

    print(f"[delete] 対象識別子: {ident} / salons {len(salons)}件 / customer_ids {sorted(customer_ids)} / line_users(tid一致) {len(lu_uids)}件")
    if not salons and not customer_ids and not lu_uids:
        print("[delete] 該当データなし")
        return

    if dry:
        for s in salons:
            logs = _req("GET", f"post_logs?salon_id=eq.{s['id']}&select=id&limit=1000")
            print(f"  [DRY] salons id={s['id']} ({s['salon_name']}) / post_logs {len(logs)}件 を削除予定")
        for cid in customer_ids:
            rows = _req("GET", f"line_users?stripe_customer_id=eq.{urllib.parse.quote(cid)}&select=line_user_id")
            print(f"  [DRY] line_users {len(rows)}件（{cid}）を削除予定")
        print("[DRY] 実削除は --dry-run を外して実行")
        return

    for s in salons:
        _req("DELETE", f"post_logs?salon_id=eq.{s['id']}", prefer="return=minimal")
        print(f"  [OK] post_logs 削除（salon_id={s['id']}）")
        _req("DELETE", f"salons?id=eq.{s['id']}", prefer="return=minimal")
        print(f"  [OK] salons 削除（{s['salon_name']}・トークン含む）")
    for cid in customer_ids:
        _req("DELETE", f"line_users?stripe_customer_id=eq.{urllib.parse.quote(cid)}", prefer="return=minimal")
        print(f"  [OK] line_users 削除（{cid}）")
    for uid in lu_uids:
        _req("DELETE", f"line_users?line_user_id=eq.{urllib.parse.quote(uid)}", prefer="return=minimal")
        print(f"  [OK] line_users 削除（line_user_id={uid[:8]}…）")

    # ===== 削除後検証：残存があれば失敗させる（公約どおり消えたことを保証） =====
    leftovers = []
    for s2 in salons:
        leftovers += _req("GET", f"post_logs?salon_id=eq.{s2['id']}&select=id&limit=1")
    for col in ("salon_name", "stripe_customer_id"):
        leftovers += _req("GET", f"salons?{col}=eq.{urllib.parse.quote(ident)}&select=id")
    for cid in customer_ids:
        leftovers += _req("GET", f"line_users?stripe_customer_id=eq.{urllib.parse.quote(cid)}&select=line_user_id")
    leftovers += _req("GET", f"line_users?expected_threads_id=eq.{urllib.parse.quote(ident.lower())}&select=line_user_id")
    if leftovers:
        print(f"[ERROR] 削除後も {len(leftovers)} 件残存。手動確認が必要", file=sys.stderr)
        sys.exit(1)
    print("[verify] 削除後の再検索: 残存0件 ✅")

    # workflowがprivateリポの投稿プールを削除できるよう、対象サロン名を出力
    import re as _re
    with open("deleted_salons.txt", "w") as f:
        for s2 in salons:
            f.write(_re.sub(r"[^\w\-]", "_", s2["salon_name"]) + "\n")
    print("[delete] DB削除完了。投稿プールファイルはworkflowが続けて削除する。")


if __name__ == "__main__":
    main()
