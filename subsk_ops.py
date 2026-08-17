"""
サブスク個数把握 Google Sheets 月次運用スクリプト（GitHub Actions 版）

元は ~/projects/subsk-tracker/monthly_ops.py（launchd 実行）。
Mac スリープでジョブが動かない問題を避けるため 2026-07-10 にクラウド移行。

使い方:
  python3 subsk_ops.py create   # 翌月シート作成（毎月25日 9:00 JST）
  python3 subsk_ops.py check    # 翌月 Square×Sheets 整合性チェック（毎月28日 20:00 JST）

認証:
  - Google Sheets: google_service_account.json（SA）優先。無ければ ~/.google_drive_token.json（ローカル互換）
  - Square/LINE: 環境変数 SQUARE_ACCESS_TOKEN / SQUARE_LOCATION_BEMOLLE / LINE_CHANNEL_ACCESS_TOKEN
"""
import json
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from googleapiclient.discovery import build

# ── 設定 ──────────────────────────────────────────────────────────────────
SID = "1vxJIQvEI-VllkxL09lAj-NbfZcP9kKKwKYz_tTkH5u0"
SA_PATH = Path(__file__).parent / "google_service_account.json"
TOKEN_PATH = Path.home() / ".google_drive_token.json"
SQUARE_ENV_PATH = Path.home() / "projects/square-reader/.env"
# Sheets上の列定義（0-indexed）
COL_NAME = 1       # B: 顧客名
COL_AMT  = 14      # 金額列のフォールバック（実際はヘッダー「金額」で動的検出）


# ── 振込対応の調整 ──────────────────────────────────────────────────────
# シートには載せる（発送業務・売上報告に使うため）が、Square課金ではなく
# 銀行振込で支払われる分。翌月チェック時にシート側からこの金額（税抜）を
# 差し引いて照合し、差分の誤検知を防ぐ。
# 形式: {"YYYY-MM": {"苗字さん": (税抜金額, "メモ")}}
TRANSFER_ADJUSTMENTS = {
    "2026-08": {"古谷さん": (6300, "ベーシック×1は振込対応（8/1振込¥13,284の一部・Square全スキップ済み）")},
}


# ── 消費税率 ──────────────────────────────────────────────────────────────
# サプリ等の食品は軽減税率8%だが、食品でない商品は10%（例: マックスボディー）。
# ヘッダー行の商品名（改行無視・部分一致）がここに載っていれば税込換算を10%で行う。
TAX10_PRODUCTS = ("マックスボディー",)


# ── シート作成直後に自動で入れる数量 ─────────────────────────────────────
# スキップは指示がない限り「その月だけ」（2026-07-29 彩さん明言）。翌月シートは
# 前月コピーで作られるため、放置するとスキップの空欄がそのまま翌月に引き継がれる。
# スキップをシートに反映したら、必ずここに翌月の復元エントリを追加すること。
# ワコナル（3/6/9/12月課金）のような隔月・四半期商品の課金月もここで入れる。
# 形式: {"作成するシートの月": [(セル, 数量, メモ), ...]}（セル位置は前月シート基準＝コピー後も同じ）
CREATE_AUTO_QTY = {
    "2026-09": [
        ("G6", 1, "古谷さん プロテイン（彩担当行）8月スキップ分を再開"),
        ("G7", 1, "古谷さん プロテイン（有加担当行）8月スキップ分を再開"),
        ("J8", 1, "古谷さん バーン 8月スキップ分を再開"),
        ("G14", 2, "長原さん HRプロテイン2袋 8月スキップ分を再開"),
        ("I14", 1, "長原さん ベーシック 8月スキップ分を再開"),
        ("K14", 1, "長原さん フォーウーマン 8月スキップ分を再開"),
        ("M11", 1, "桑原さん ワコナル 課金月（3/6/9/12月）"),
    ],
    # セル位置は2026-09シート基準（古谷さん空行削除後のレイアウト）。
    # 桑原さん: HRプロテイン=G8 / ワコナル=M10
    "2026-10": [
        ("G8", "", "桑原さん HRプロテイン 10月スキップ（2026-08-17指示・1ヶ月のみ）"),
    ],
    "2026-11": [
        ("G8", 1, "桑原さん HRプロテイン 10月スキップ分を再開"),
    ],
    "2026-12": [
        ("M10", 1, "桑原さん ワコナル 課金月（3/6/9/12月）"),
    ],
}


# ── Google Sheets 認証 ────────────────────────────────────────────────────
def _sheets():
    if SA_PATH.exists():
        from google.oauth2.service_account import Credentials as SACredentials
        creds = SACredentials.from_service_account_file(
            str(SA_PATH), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    else:
        from google.oauth2.credentials import Credentials
        token = json.loads(TOKEN_PATH.read_text())
        creds = Credentials(**{k: v for k, v in token.items()
                               if k in ["token", "refresh_token", "token_uri",
                                        "client_id", "client_secret", "scopes"]})
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_all_sheets(svc):
    """シート一覧 {name: sheetId} を返す"""
    meta = svc.spreadsheets().get(spreadsheetId=SID).execute()
    return {s["properties"]["title"]: s["properties"]["sheetId"]
            for s in meta["sheets"]}


def _read_sheet(svc, sheet_name, range_="A1:Z60"):
    """シートの値を2次元リストで返す。行が足りない場合は空リスト"""
    r = svc.spreadsheets().values().get(
        spreadsheetId=SID,
        range=f"'{sheet_name}'!{range_}"
    ).execute()
    return r.get("values", [])


def _batch_update(svc, requests):
    svc.spreadsheets().batchUpdate(
        spreadsheetId=SID,
        body={"requests": requests}
    ).execute()


# ── Square API ────────────────────────────────────────────────────────────
def _load_square_env():
    # GitHub Actions では環境変数で直接渡す。ローカルでは .env から補完
    if SQUARE_ENV_PATH.exists():
        for line in SQUARE_ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    for required in ("SQUARE_ACCESS_TOKEN", "SQUARE_LOCATION_BEMOLLE"):
        if not os.environ.get(required):
            print(f"[ERROR] {required} が未設定", file=sys.stderr)
            sys.exit(1)


def _sq_post(path, body):
    token = os.environ["SQUARE_ACCESS_TOKEN"]
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://connect.squareup.com{path}",
        data=data,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json",
                 "Square-Version": "2025-04-16"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _sq_get(path):
    token = os.environ["SQUARE_ACCESS_TOKEN"]
    req = urllib.request.Request(
        f"https://connect.squareup.com{path}",
        headers={"Authorization": f"Bearer {token}",
                 "Square-Version": "2025-04-16"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def _get_plan_catalog():
    """全プランの {variation_id: {name, cadence_months}} を返す"""
    items = _sq_post("/v2/catalog/search", {
        "object_types": ["SUBSCRIPTION_PLAN_VARIATION"]
    }).get("objects", [])
    result = {}
    for o in items:
        vid = o["id"]
        data = o.get("subscription_plan_variation_data", {})
        phases = data.get("phases", [])
        cadence = phases[0].get("cadence", "MONTHLY") if phases else "MONTHLY"
        months = {"MONTHLY": 1, "QUARTERLY": 3, "ANNUAL": 12}.get(cadence, 1)
        result[vid] = {"name": data.get("name", "?"), "cadence_months": months}
    return result


def _get_customer_name(cust_id):
    try:
        c = _sq_get(f"/v2/customers/{cust_id}").get("customer", {})
        last = c.get("family_name", "")
        return last + "さん" if last else "?"
    except Exception:
        return "?"


def _add_months(d, n):
    """date d に n ヶ月加算"""
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    return d.replace(year=year, month=month, day=1)


def _get_square_expected_totals(month_str):
    """month_str (YYYY-MM) に課金される予定の サブスクの {苗字さん: 税込合計} を返す（翌月プレビュー用）。
    実課金前の予測：
      - ACTIVE で charged_through_date が month_str の月初（次の課金日が month_str の初日）
      - かつ CANCEL/PAUSE アクションが month_str の月初以前に effective されていない
      - PENDING で start_date が month_str
      - PAUSED でも RESUME が month_str に effective なら課金される
    QUARTERLY などの cadence も考慮する。"""
    subs = _sq_post("/v2/subscriptions/search", {"query": {}}).get("subscriptions", [])
    month_first = month_str + "-01"
    totals = {}

    for s in subs:
        if s.get("status") not in ("ACTIVE", "PENDING", "PAUSED"):
            continue
        ct    = s.get("charged_through_date") or ""
        start = s.get("start_date") or ""
        sub_id = s["id"]

        try:
            d_actions = _sq_get(f"/v2/subscriptions/{sub_id}?include=actions").get("subscription", {})
            actions = d_actions.get("actions", []) or []
        except Exception:
            actions = []

        # CANCEL or PAUSE が month_str の初日 "以前" or "当日" に effective → 課金されない
        blocked_by_action = False
        for a in actions:
            atype = a.get("type")
            adate = (a.get("effective_date") or "")[:10]
            if atype in ("CANCEL", "PAUSE") and adate and adate <= month_first:
                blocked_by_action = True
                break
        if blocked_by_action:
            continue

        # 課金タイミング判定
        will_bill = False
        if s["status"] == "PENDING":
            if start[:7] == month_str:
                will_bill = True
        elif s["status"] == "ACTIVE":
            if ct[:7] == month_str:
                will_bill = True
        elif s["status"] == "PAUSED":
            for a in actions:
                atype = a.get("type")
                adate = (a.get("effective_date") or "")[:10]
                if atype == "RESUME" and adate and adate[:7] == month_str:
                    will_bill = True
                    break

        if not will_bill:
            continue

        # order_template から税込金額を取得
        try:
            detail = _sq_get(f"/v2/subscriptions/{sub_id}").get("subscription", {})
            phases = detail.get("phases", []) or []
            order_id = next((p["order_template_id"] for p in phases
                             if p.get("order_template_id")), None)
            if order_id:
                orders = _sq_post("/v2/orders/batch-retrieve", {
                    "order_ids": [order_id],
                    "location_id": os.environ["SQUARE_LOCATION_BEMOLLE"]
                }).get("orders", [])
                if orders:
                    amount = orders[0].get("total_money", {}).get("amount", 0)
                    name = _get_customer_name(s["customer_id"])
                    totals[name] = totals.get(name, 0) + amount
        except Exception:
            pass
    return totals


# ── 月名ユーティリティ ────────────────────────────────────────────────────
def _current_month():
    """今月の (YYYY-MM, YYYY年M月)"""
    d = date.today()
    return d.strftime("%Y-%m"), f"{d.year}年{d.month}月"


def _next_month():
    """翌月の (YYYY-MM, YYYY年M月)"""
    d = date.today().replace(day=28) + timedelta(days=10)
    return d.strftime("%Y-%m"), f"{d.year}年{d.month}月"


# ── LINE 通知 ──────────────────────────────────────────────────────────────
def _notify(msg):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token and SQUARE_ENV_PATH.exists():
        for line in SQUARE_ENV_PATH.read_text().splitlines():
            if line.startswith("LINE_CHANNEL_ACCESS_TOKEN="):
                token = line.split("=", 1)[1].strip()
    if not token:
        print(f"[LINE通知 skip] {msg}")
        return
    body = json.dumps({"messages": [{"type": "text", "text": msg}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast",
        data=body,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


# ── シート構造ヘルパー ────────────────────────────────────────────────────
def _amount_col_idx(rows):
    """ヘッダー行（行2）から「金額」列のインデックス（0-indexed）を動的検出。
    商品列（マックスボディー等）を追加しても壊れないため。"""
    headers = rows[1] if len(rows) >= 2 else []
    for i, h in enumerate(headers):
        if h and "金額" in str(h):
            return i
    return COL_AMT  # フォールバック


def _col_letter(col_idx):
    """0-indexed の列番号をA, B, ... に変換"""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if col_idx < 26:
        return letters[col_idx]
    return letters[col_idx // 26 - 1] + letters[col_idx % 26]


# ── CREATE: 翌月シートの作成 ──────────────────────────────────────────────
def cmd_create():
    svc = _sheets()
    sheets = _get_all_sheets(svc)
    cur_str, cur_name = _current_month()
    nxt_str, nxt_name = _next_month()

    # 翌月シートが既に存在する場合はスキップ
    if nxt_name in sheets:
        print(f"[create] '{nxt_name}' シートは既に存在します。スキップ。")
        return

    # 今月シートが存在しない場合はエラー
    if cur_name not in sheets:
        print(f"[create] '{cur_name}' シートが見つかりません。終了。")
        return

    src_sheet_id = sheets[cur_name]
    print(f"[create] '{cur_name}' → '{nxt_name}' を作成中...")

    # 1. 今月シートをコピー
    copy_result = svc.spreadsheets().sheets().copyTo(
        spreadsheetId=SID,
        sheetId=src_sheet_id,
        body={"destinationSpreadsheetId": SID}
    ).execute()
    new_sheet_id = copy_result["sheetId"]

    # 2. 新シートをリネーム＋先頭タブに移動（copyToは最後尾に作るため見つけにくい）
    _batch_update(svc, [{
        "updateSheetProperties": {
            "properties": {"sheetId": new_sheet_id, "title": nxt_name, "index": 0},
            "fields": "title,index"
        }
    }])

    # 3. タイトルセル（A1）を更新
    svc.spreadsheets().values().update(
        spreadsheetId=SID,
        range=f"'{nxt_name}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [[f"定期購入　{nxt_name}"]]}
    ).execute()

    # 4. 数量は前月のまま引き継ぐ（2026-07-11 変更：サブスクは毎月ほぼ同一のため
    #    クリア→全手入力をやめ、スキップ・追加など変更分だけ調整する運用に）
    print("[create] 数量は前月から引き継ぎ（クリアなし）")

    # 5. 1ヶ月スキップの復元・課金月商品の数量を自動投入
    #    （スキップは指示がない限りその月だけ。前月コピーだと空欄が引き継がれるため戻す）
    restored = []
    entries = CREATE_AUTO_QTY.get(nxt_str, [])
    if entries:
        svc.spreadsheets().values().batchUpdate(
            spreadsheetId=SID,
            body={"valueInputOption": "USER_ENTERED",
                  "data": [{"range": f"'{nxt_name}'!{cell}", "values": [[qty]]}
                           for cell, qty, _ in entries]}
        ).execute()
        restored = [f"・{memo}（{cell}={qty}）" for cell, qty, memo in entries]
        print("[create] 自動投入:\n" + "\n".join(restored))

    # 6. LINE通知
    msg = (f"[サブスク表] {nxt_name} シートを作成しました。\n"
           f"数量は{cur_name}の内容を引き継いでいます。\n"
           f"スキップ・追加・解約などの変更分だけ調整してください。\n"
           f"https://docs.google.com/spreadsheets/d/{SID}/edit")
    if restored:
        msg += "\n\n以下は自動で数量を入れました（1ヶ月スキップの再開・課金月商品）：\n" + "\n".join(restored)
    _notify(msg)
    print(msg)


# ── CHECK: 翌月 Square × Sheets 整合性チェック（毎月28日実行）────────────
# 翌月分のシートに反映された「スキップ予定」が、Square側でPAUSE/CANCEL設定済みかを照合。
# 「シートには無い（スキップ予定）」のに「Squareでは課金される予定」を早期検知する。
def cmd_check():
    _load_square_env()
    svc = _sheets()
    nxt_str, nxt_name = _next_month()

    print(f"[check] 翌月 {nxt_name} の整合性チェック開始...")

    # Sheets（翌月分）の顧客別合計を取得
    sheets_totals, sheets_tax10 = _get_sheets_totals(svc, nxt_name)

    # 振込対応分をシート合計から差し引く（照合はSquare課金分だけで行う）
    adj_notes = []
    for name, (adj_excl, memo) in TRANSFER_ADJUSTMENTS.get(nxt_str, {}).items():
        if name in sheets_totals:
            sheets_totals[name] = max(0, sheets_totals[name] - adj_excl)
            adj_notes.append(f"※ {name}: 振込対応 ¥{adj_excl:,}（税抜）を照合から除外 — {memo}")

    # Square 側で翌月に課金される予定の合計を取得（CANCEL/PAUSE考慮済み）
    square_totals = _get_square_expected_totals(nxt_str)

    # Sheets は税抜 → 税込換算（Square方式: tax = floor(excl × rate)）
    # 食品は8%・TAX10_PRODUCTS（マックスボディー等）は10%で計算する
    # ※ TRANSFER_ADJUSTMENTS で差し引くのは8%商品の想定（10%商品を振込対応に
    #   する場合は tax10 側からも引く改修が必要）
    def to_tax_incl(name, excl):
        excl10 = min(sheets_tax10.get(name, 0), excl)
        excl8 = excl - excl10
        return excl8 + int(excl8 * 0.08) + excl10 + int(excl10 * 0.10)

    sheets_incl = {k: to_tax_incl(k, v) for k, v in sheets_totals.items()}

    # 比較（許容誤差 ±2円：端数処理の差）
    TOLERANCE = 2
    all_names = set(sheets_incl.keys()) | set(square_totals.keys())
    ok_list = []
    ng_list = []

    for name in sorted(all_names):
        s_val = sheets_incl.get(name, 0)   # Sheets 税込換算
        q_val = square_totals.get(name, 0) # Square 税込
        if s_val == 0 and q_val == 0:
            continue
        diff = abs(s_val - q_val)
        s_excl = sheets_totals.get(name, 0)
        if diff <= TOLERANCE:
            ok_list.append(f"✅ {name}: Sheets¥{s_excl:,}（税込¥{s_val:,}） ≒ Square¥{q_val:,}")
        elif s_val == 0 and q_val > 0:
            ng_list.append(
                f"🚨 {name}: シートには無い（スキップ予定）が Squareでは¥{q_val:,} 課金予定！\n"
                f"   → SquareでPAUSE/CANCELが設定されていません。今すぐ設定してください。"
            )
        elif s_val > 0 and q_val == 0:
            ng_list.append(
                f"⚠ {name}: シートには¥{s_excl:,}（税込¥{s_val:,}）あるが Square 課金予定なし。\n"
                f"   → Squareで既にPAUSE/CANCELされているか、サブスク自体が存在しない可能性。"
            )
        else:
            ng_list.append(
                f"⚠ {name}: Sheets税込¥{s_val:,}（税抜¥{s_excl:,}） / Square¥{q_val:,}（差¥{diff:,}）"
            )

    # サマリ
    sheets_excl_sum  = sum(sheets_totals.values())
    sheets_incl_sum  = sum(sheets_incl.values())
    square_total_sum = sum(square_totals.values())
    tax_sum = sheets_incl_sum - sheets_excl_sum

    lines = [f"[サブスク表] {nxt_name}（翌月）整合性チェック結果"]
    lines.append(f"Sheets合計: 税抜¥{sheets_excl_sum:,} / 税込¥{sheets_incl_sum:,}（消費税¥{tax_sum:,}）")
    lines.append(f"Square合計: ¥{square_total_sum:,}")
    lines.append("")

    if ng_list:
        lines.append(f"差分あり {len(ng_list)} 件：")
        lines.extend(ng_list)
        lines.append("")
    if ok_list:
        lines.append(f"一致 {len(ok_list)} 件（±{TOLERANCE}円以内）")

    if not ng_list:
        lines.append("✅ 全件一致。問題なし。")

    if adj_notes:
        lines.append("")
        lines.extend(adj_notes)

    msg = "\n".join(lines)
    _notify(msg)
    print(msg)


def _get_sheets_totals(svc, sheet_name):
    """シートから顧客別の (金額合計, うち税率10%商品の金額) を
    ({苗字さん: 税抜合計}, {苗字さん: 10%商品の税抜金額}) で返す。
    金額列はヘッダー名「金額」で動的に判定（商品列を追加・削除しても壊れない）。"""
    rows = _read_sheet(svc, sheet_name)
    if not rows:
        return {}, {}

    amt_idx = _amount_col_idx(rows)

    # 税率10%商品の列 {列idx: 税抜単価} をヘッダー行から検出
    headers = rows[1] if len(rows) >= 2 else []
    prices = rows[2] if len(rows) >= 3 else []
    tax10_cols = {}
    for i, h in enumerate(headers):
        if any(p in str(h).replace("\n", "") for p in TAX10_PRODUCTS):
            try:
                tax10_cols[i] = int(str(prices[i]).replace(",", "").replace("¥", ""))
            except (ValueError, TypeError, IndexError):
                pass

    totals, tax10_totals = {}, {}
    for row in rows:
        if len(row) <= amt_idx:
            continue
        name = row[COL_NAME] if len(row) > COL_NAME else ""
        amt_raw = row[amt_idx] if len(row) > amt_idx else ""
        if not name or name in ("合計", "(10日発送)", "(20日発送)", ""):
            continue
        try:
            amt = int(str(amt_raw).replace(",", "").replace("¥", ""))
            if amt > 0:
                totals[name] = totals.get(name, 0) + amt
        except (ValueError, TypeError):
            continue
        for i, price in tax10_cols.items():
            try:
                qty = int(str(row[i]).strip() or 0) if len(row) > i else 0
            except (ValueError, TypeError):
                qty = 0
            if qty > 0:
                tax10_totals[name] = tax10_totals.get(name, 0) + qty * price
    return totals, tax10_totals


# ── エントリポイント ──────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "create":
        cmd_create()
    elif cmd == "check":
        cmd_check()
    else:
        print("使い方: python3 subsk_ops.py create | check")
