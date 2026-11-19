#!/usr/bin/env python3
"""解約の通知が来ても、お客様にお伝えした投稿停止日（stop_on）までは止めないこと。

2026-11-19 杉村さん（piccolo）で、契約終了日に is_active が false になり、
「11月いっぱい投稿します」とお伝えしていた11/30より11日早く投稿が止まった。
"""
import importlib.util, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    "stripe_webhook", os.path.join(ROOT, "saas_app", "api", "stripe_webhook.py"))
wh = importlib.util.module_from_spec(spec)
sys.modules["stripe_webhook"] = wh
spec.loader.exec_module(wh)

FAILS = []
calls = {"deactivated": [], "lines": []}
wh.deactivate_salon = lambda sid: calls["deactivated"].append(sid)
wh.line_push_admin = lambda msg: calls["lines"].append(msg)
wh.get_salon_by_subscription = lambda sub, cus: {"id": "salon-id", "salon_name": "test.salon"}
wh._is_toukosan_product = lambda items: True

today = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600))
future = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600 + 7 * 86400))
past = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600 - 7 * 86400))


def run(case, stop_on, want_deactivate):
    calls["deactivated"].clear(); calls["lines"].clear()
    cus = "cus_TEST"
    wh.CANCEL_WATCH[cus] = {"name": "テスト", "stop_on": stop_on} if stop_on else {"name": "テスト"}
    wh.handle_subscription_deleted({"id": "sub_1", "customer": cus, "items": {"data": []}})
    got = bool(calls["deactivated"])
    ok = got == want_deactivate
    print(f"  {'OK ' if ok else '🚨 '}{case}: 止めた={got}（期待={want_deactivate}）"
          f" 通知={calls['lines'][0][:24] if calls['lines'] else 'なし'}")
    if not ok:
        FAILS.append(case)
    wh.CANCEL_WATCH.pop(cus, None)


print("解約通知が来たときに投稿を止めるか")
run("投稿停止日がまだ先（約束あり）", future, False)
run("投稿停止日が今日", today, False)
run("投稿停止日が過ぎている", past, True)
run("投稿停止日の指定なし（満了と同じ）", None, True)

print("\n実際の登録内容も見る")
for cus, w in wh.CANCEL_WATCH.items():
    print(f"  {w.get('name')}: until={w.get('until')} stop_on={w.get('stop_on', '—')}")

sys.exit(1 if FAILS else 0)
