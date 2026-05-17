"""
とうこさん SaaS ユーザー登録スクリプト
Usage:
  export SUPABASE_URL=...
  export SUPABASE_SERVICE_KEY=...
  python3 register_saas_user.py

トークン取得手順：
  Meta Developer Console → Threads Auto Post アプリ
  → ユースケース → Threads API → 設定 → ユーザートークン生成ツール
  → 対象ユーザーの「アクセストークンを生成」をクリック → 貼り付け
"""
import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys

def _load_env_file():
    """session_server/.env または .env から環境変数を自動読み込み（未設定の変数のみ上書き）"""
    for path in [
        os.path.join(os.path.dirname(__file__), "session_server", ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
    ]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        if k not in os.environ:
                            os.environ[k] = v
            break

_load_env_file()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
THREADS_API  = "https://graph.threads.net/v1.0"


def me(token):
    url = f"{THREADS_API}/me?fields=id,username&access_token={token}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_salon(salon_name):
    url = (f"{SUPABASE_URL}/rest/v1/salons"
           f"?salon_name=eq.{urllib.parse.quote(salon_name)}&select=id,salon_name,threads_user_id,is_active")
    req = urllib.request.Request(url, headers=supabase_headers())
    with urllib.request.urlopen(req, timeout=15) as resp:
        rows = json.loads(resp.read())
    return rows[0] if rows else None


def insert_salon(salon_name, user_id, token, stripe_customer_id=None, stripe_subscription_id=None):
    row = {
        "salon_name": salon_name,
        "threads_user_id": user_id,
        "access_token": token,
        "is_active": True,
    }
    if stripe_customer_id:
        row["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id:
        row["stripe_subscription_id"] = stripe_subscription_id
    data = json.dumps(row).encode()
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/salons",
        data=data,
        headers={**supabase_headers(), "Prefer": "return=minimal"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def update_salon(salon_id, user_id, token, stripe_customer_id=None, stripe_subscription_id=None):
    row = {
        "threads_user_id": user_id,
        "access_token": token,
        "is_active": True,
    }
    if stripe_customer_id:
        row["stripe_customer_id"] = stripe_customer_id
    if stripe_subscription_id:
        row["stripe_subscription_id"] = stripe_subscription_id
    data = json.dumps(row).encode()
    url = f"{SUPABASE_URL}/rest/v1/salons?id=eq.{salon_id}"
    req = urllib.request.Request(
        url, data=data,
        headers={**supabase_headers(), "Prefer": "return=minimal"},
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("エラー: SUPABASE_URL と SUPABASE_SERVICE_KEY を環境変数に設定してください")
        sys.exit(1)

    print("=== とうこさん SaaS ユーザー登録 ===")
    print()

    salon_name = input("サロン名（英数字・アンダースコア推奨）: ").strip()
    if not salon_name:
        print("エラー: サロン名が空です")
        sys.exit(1)

    print()
    print("【トークン取得手順】")
    print("1. 以下のURLを開く（Metaにログイン済みのブラウザで）")
    print("   https://developers.facebook.com/apps/1497479218824264/use_cases/customize/settings/?use_case_enum=THREADS_API&selected_tab=settings&product_route=threads-api")
    print("2. 「ユーザートークン生成ツール」セクションでユーザーの「アクセストークンを生成」をクリック")
    print("3. 表示されたトークンをコピーして貼り付ける")
    print()
    print("※ ユーザーが未承認の場合：")
    print("   https://developers.facebook.com/apps/1497479218824264/roles/roles/")
    print("   でThreadsテスターとして追加 → ユーザーが https://www.threads.com/settings/account で承認")
    print()

    token = input("アクセストークンを貼り付け: ").strip()
    if not token:
        print("エラー: トークンが空です")
        sys.exit(1)

    # /me でトークン確認 & user_id 取得
    print()
    print("トークンを確認中...")
    try:
        me_data = me(token)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"エラー: トークンが無効です HTTP {e.code}: {body[:200]}")
        sys.exit(1)

    user_id = str(me_data["id"])
    username = me_data.get("username", "")
    print(f"✅ トークン確認OK: @{username} (id={user_id})")

    # Stripe customer ID（任意）
    print()
    print("【Stripe ID について】")
    print("解約・支払い失敗の自動検知に使います（未入力でも投稿は動作します）。")
    print()
    print("Stripe customer ID（cus_...）: Stripeダッシュボード → 顧客一覧 → 当該顧客を開いてIDをコピー")
    stripe_customer_id = input("Stripe customer ID（未入力でスキップ）: ").strip()
    print()
    print("Stripe subscription ID（sub_...）: 同顧客ページ → サブスクリプション → IDをコピー")
    print("※ 同じ人がうらかたさんにも加入している場合は必ず入力してください（誤動作防止）")
    stripe_subscription_id = input("Stripe subscription ID（未入力でスキップ）: ").strip()

    # Supabase 登録
    existing = get_salon(salon_name)
    if existing:
        print(f"\n既存サロン「{salon_name}」を更新します（id={existing['id']}）")
        update_salon(existing["id"], user_id, token, stripe_customer_id or None, stripe_subscription_id or None)
        print("✅ Supabase 更新完了")
    else:
        print(f"\n新規サロン「{salon_name}」を登録します")
        insert_salon(salon_name, user_id, token, stripe_customer_id or None, stripe_subscription_id or None)
        print("✅ Supabase 登録完了")

    print()
    print("=" * 50)
    print(f"登録完了！")
    print(f"  サロン名: {salon_name}")
    print(f"  @{username} (id={user_id})")
    print()
    print("次のステップ：")
    print("1. GitHub Actions で「SaaS - サロン別投稿生成」を手動実行")
    print("2. 約2分後、「SaaS - サロン自動投稿」を手動実行して動作確認")
    print()
    print("GitHub Actions:")
    print("https://github.com/AyaKuroki929/threads-bot/actions")


if __name__ == "__main__":
    main()
