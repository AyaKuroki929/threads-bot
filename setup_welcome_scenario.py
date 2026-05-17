"""
とうこさん ウェルカムシナリオ 作成スクリプト
line-harness-oss API を使って「友達追加時の自動メッセージ」を登録する。

使い方:
  export LINE_HARNESS_API_KEY="..."   # Cloudflare Worker secret の API_KEY
  python3 setup_welcome_scenario.py

API_KEY の確認方法:
  cd ~/projects/line-harness-oss
  pnpm wrangler secret list --name toukosan
  # ← API_KEY が表示されていれば、その値を設定済み
  # 値を確認するには Cloudflare Dashboard → Workers → toukosan → Settings → Variables
"""
import json
import os
import sys
import urllib.request

WORKER_URL = "https://toukosan.nailsalon-flat.workers.dev"
API_KEY    = os.environ.get("LINE_HARNESS_API_KEY", "")

WELCOME_TEXT = """\
はじめまして！とうこさんです🌿

Threads自動投稿サービスへのご登録ありがとうございます。

担当者より順にご連絡いたしますので、もう少しだけお待ちください😊

ご質問があれば、このトーク画面からいつでもお気軽にどうぞ。"""

SECOND_TEXT = """\
サービスのご利用にあたって、Threadsのアカウント情報が必要です。

担当者から別途ご案内いたします。
まずはフォームのご回答をお願いします📝"""


def api(method: str, path: str, data: dict | None = None):
    url = f"{WORKER_URL}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def main():
    if not API_KEY:
        print("エラー: LINE_HARNESS_API_KEY を設定してください")
        print("  export LINE_HARNESS_API_KEY='...'")
        sys.exit(1)

    # 既存の friend_add シナリオを確認
    print("既存シナリオを確認中...")
    resp = api("GET", "/api/scenarios")
    existing = [s for s in resp.get("data", []) if s.get("triggerType") == "friend_add"]
    if existing:
        names = [s["name"] for s in existing]
        print(f"⚠️  friend_add シナリオが既に存在します: {names}")
        ans = input("上書きしますか？（既存は削除されません。新規追加します）[y/N]: ").strip().lower()
        if ans != "y":
            print("キャンセルしました")
            return

    # シナリオ作成
    print("\nウェルカムシナリオを作成中...")
    scenario = api("POST", "/api/scenarios", {
        "name": "ウェルカムメッセージ",
        "description": "友達追加時に自動送信されるウェルカムメッセージ",
        "triggerType": "friend_add",
        "isActive": True,
    })
    scenario_id = scenario["data"]["id"]
    print(f"✅ シナリオ作成: id={scenario_id}")

    # ステップ1: 即時送信（ウェルカム文）
    step1 = api("POST", f"/api/scenarios/{scenario_id}/steps", {
        "stepOrder": 1,
        "delayMinutes": 0,
        "messageType": "text",
        "messageContent": WELCOME_TEXT,
    })
    print(f"✅ ステップ1追加: id={step1['data']['id']}")

    # ステップ2: 1分後（次のアクション案内）
    step2 = api("POST", f"/api/scenarios/{scenario_id}/steps", {
        "stepOrder": 2,
        "delayMinutes": 1,
        "messageType": "text",
        "messageContent": SECOND_TEXT,
    })
    print(f"✅ ステップ2追加: id={step2['data']['id']}")

    print()
    print("=" * 50)
    print("ウェルカムシナリオの設定が完了しました！")
    print()
    print("確認方法:")
    print("  とうこさんLINEアカウントを一度ブロック→解除すると")
    print("  ウェルカムメッセージが届くはずです。")
    print()
    print("管理画面でも確認できます:")
    print("  https://line-harness-admin-6ulitnovv-ayakuroki929s-projects.vercel.app")


if __name__ == "__main__":
    main()
