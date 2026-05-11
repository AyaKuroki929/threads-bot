# Threadsサブスクシステム セットアップ手順

## 残りの設定（Googleサービスアカウント）

スプレッドシートを自動で読むために、GoogleのサービスアカウントのJSONキーが必要です。

### ステップ1：Google Cloud Consoleでサービスアカウントを作成

1. https://console.cloud.google.com を開く
2. 新しいプロジェクトを作成（例：「threads-saas」）
3. 左メニュー → 「APIとサービス」→「ライブラリ」
4. 「Google Sheets API」を検索 → 有効化
5. 「Google Drive API」を検索 → 有効化
6. 左メニュー → 「APIとサービス」→「認証情報」
7. 「認証情報を作成」→「サービスアカウント」
8. 名前：「threads-saas-bot」→ 作成
9. 作成したサービスアカウントをクリック
10. 「キー」タブ →「鍵を追加」→「新しい鍵を作成」→「JSON」
11. JSONファイルがダウンロードされる

### ステップ2：スプレッドシートをサービスアカウントと共有

1. スプレッドシート（Threadsサブスクシステム）を開く
2. 右上の「共有」ボタン
3. ダウンロードしたJSONファイルの中の `client_email` の値をコピー
   （例：threads-saas-bot@threads-saas.iam.gserviceaccount.com）
4. そのメールアドレスを「閲覧者」として追加

### ステップ3：GitHub Secretsに登録

1. GitHubのリポジトリ → Settings → Secrets and variables → Actions
2. 「New repository secret」
3. 名前：`GOOGLE_SERVICE_ACCOUNT_JSON`
4. 値：ダウンロードしたJSONファイルの中身をそのまま貼り付け
5. 保存

### ステップ4：動作確認

GitHub Actions の「SaaS - サロン別投稿生成」ワークフローを手動実行してテスト。

---

## 現在の状態

| 項目 | 状態 |
|------|------|
| Metaアプリ（Threads Auto Post） | ✅ 作成済み |
| Google Formsオンボーディングフォーム | ✅ 作成済み |
| Googleスプレッドシート連携 | ⚠️ フォームとシートの紐付けが未完了 |
| 投稿生成スクリプト（generate_saas_posts.py） | ✅ 作成済み |
| GitHub Actions ワークフロー | ✅ 作成済み |
| Googleサービスアカウント設定 | ❌ 未設定 |
| OAuth連携（サロンのThreadsと接続） | ❌ 未着手 |
| Stripe決済 | ❌ 未着手 |

## 次にやること

1. Googleサービスアカウントのセットアップ（上記手順）
2. フォームとスプレッドシートの紐付け
3. テスト用サロンデータをフォームに入力してエンドツーエンドテスト
