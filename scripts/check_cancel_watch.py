#!/usr/bin/env python3
"""解約待ち（CANCEL_WATCH）の満了日が、Stripeの実物と合っているかを毎日見張る。

なぜ要るか：2026-10-06に杉村真理子さんを解約待ちへ登録したとき、Stripeの課金履歴を
実測せず「毎月1日課金・11/30満了」と推測で書いた。実物は19日課金で、満了日が11日ずれていた
（2026-10-31に発覚）。ずれたままだと、彩さんへ飛ぶリマインドの満了日が間違い、
投稿停止の日もずれる。人の目で気づけないので機械で毎日突き合わせる。

判定するのは次の3点。
  ① 記録の満了日（until）＝ Stripeの「契約が切れる日の前日」になっているか
  ② 最終課金が済んでいる人に「期間終了時にキャンセル」が設定されているか
  ③ サブスクが見つかるか（解約済み・顧客違いを検知）

ずれが1件でもあれば彩さんのLINE（Claude通知Bot）へ🚨を送る。全部合っていれば何も送らない。
"""
import ast
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))
WEBHOOK_PY = os.environ.get("CANCEL_WATCH_FILE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "saas_app", "api", "stripe_webhook.py")


def load_cancel_watch(path=WEBHOOK_PY):
    """stripe_webhook.py を読み込まずに（依存を走らせずに）CANCEL_WATCH だけ取り出す。"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "CANCEL_WATCH":
                    return ast.literal_eval(node.value)
    raise SystemExit("CANCEL_WATCH が見つかりません: " + path)


def stripe(path, key, params=None):
    url = "https://api.stripe.com/v1/" + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def jst_date(ts):
    return datetime.fromtimestamp(ts, JST).date() if ts else None


def add_one_month(d):
    """1ヶ月後の同じ日。無い日（1/31の1ヶ月後など）は月末に寄せる。"""
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    day = d.day
    while day > 1:
        try:
            return d.replace(year=y, month=m, day=day)
        except ValueError:
            day -= 1
    return d.replace(year=y, month=m, day=1)


def check_one(cid, w, key):
    """1人分を突き合わせて、問題があれば文章のリストで返す。"""
    problems = []
    recorded = datetime.strptime(w["until"], "%Y-%m-%d").date()
    final = w.get("final_charge")
    final = datetime.strptime(final, "%Y-%m-%d").date() if final else None
    if not final:
        problems.append("final_charge（最終課金日）が入っていないので突き合わせできません")

    subs = stripe("subscriptions", key, {"customer": cid, "status": "all", "limit": 10})["data"]
    live = [s for s in subs if s["status"] in ("active", "trialing", "past_due", "unpaid")]
    if not live:
        if any(s["status"] == "canceled" for s in subs):
            if recorded >= datetime.now(JST).date():
                problems.append(f"Stripeのサブスクは解約済みなのに、記録の満了日は {recorded}（未来）です")
        else:
            problems.append("Stripeに有効なサブスクが見つかりません（顧客IDが違う可能性）")
        return problems

    s = live[0]
    item = s["items"]["data"][0]
    period_start = jst_date(item.get("current_period_start"))
    period_end = jst_date(item.get("current_period_end"))
    cancel_at = jst_date(s.get("cancel_at"))
    cape = bool(s.get("cancel_at_period_end"))

    if not final:
        return problems

    if period_end and period_end <= final:
        # まだ最終課金が来ていない段階
        expected = add_one_month(final) - timedelta(days=1)
        if cape:
            problems.append(
                f"最終課金（{final}）より前なのに「期間終了時にキャンセル」が設定されています。"
                f"このままだと {period_end} で切れて1ヶ月短くなります"
            )
        if period_end != final:
            problems.append(f"次の課金日 {period_end} が、予定の最終課金日 {final} と合いません")
    elif period_start == final:
        # 最終課金が済んだ段階
        expected = (cancel_at or period_end) - timedelta(days=1)
        if not cape:
            problems.append(
                f"最終課金（{final}）は済んでいるのに「期間終了時にキャンセル」が未設定です。"
                f"このままだと {period_end} に次の課金が走ります"
            )
    else:
        problems.append(
            f"予定の最終課金日（{final}）を過ぎて、もう1回課金が走っています"
            f"（今の期間 {period_start}〜{period_end}）。設定が漏れた可能性があります"
        )
        expected = (cancel_at or period_end) - timedelta(days=1)

    if recorded != expected:
        problems.append(f"満了日が合いません。記録 {recorded} ／ Stripeから計算すると {expected}")
    return problems


def notify(text):
    """彩さんのLINE（Claude通知Bot・友だちは彩さんだけ）へ送る。このリポの他の仕組みと同じ broadcast。"""
    token = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN")
    if not token:
        raise SystemExit("ADMIN_NOTIFY_LINE_TOKEN が無いので🚨を送れません")
    body = json.dumps({"messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/broadcast", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        print("LINE送信:", r.status)


def main():
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        print("STRIPE_SECRET_KEY が無いので突き合わせできません", file=sys.stderr)
        return 1
    watch = load_cancel_watch()
    print(f"解約待ち {len(watch)}件を突き合わせます（{datetime.now(JST):%Y-%m-%d %H:%M} JST）")
    bad = []
    for cid, w in watch.items():
        try:
            problems = check_one(cid, w, key)
        except Exception as e:
            problems = [f"Stripeの照会に失敗しました（{e}）"]
        if problems:
            bad.append((cid, w, problems))
            for p in problems:
                print(f"  NG {w['name']}（{cid}）: {p}")
        else:
            print(f"  OK {w['name']}（満了日 {w['until']}）")

    if not bad:
        print("全件一致。通知は送りません")
        return 0

    lines = ["🚨 解約待ちの満了日がStripeと合っていません", ""]
    for cid, w, problems in bad:
        lines.append(f"■ {w['name']}さん")
        for p in problems:
            lines.append(f"・{p}")
        lines.append(f"https://dashboard.stripe.com/customers/{cid}")
        lines.append("")
    lines.append("saas_app/api/stripe_webhook.py の CANCEL_WATCH を直してください。")
    if os.environ.get("DRY_RUN"):
        print("--- DRY_RUN なので送りません ---")
        print("\n".join(lines))
    else:
        # 送れなければ例外で落ちる＝ワークフローが失敗して別の🚨が飛ぶ（黙って終わらせない）
        notify("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
