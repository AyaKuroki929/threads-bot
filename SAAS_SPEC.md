# とうこさん サービス仕様書

> Threads自動投稿代行サービス「とうこさん」の全体仕様・運用フロー

---

## 1. サービス概要

| 項目 | 内容 |
|------|------|
| サービス名 | **とうこさん** |
| 提供者 | 株式会社紫妃彩（黒木彩） |
| 内容 | Threadsへの定期自動投稿代行 |
| 料金 | **月額2,500円（税別）／2,750円（税込）** |
| 最低契約期間 | **3ヶ月**（お試し期間なし） |
| 請求タイミング | 契約日起算（例：5日契約 → 毎月5日請求） |
| 決済方法 | Stripe（商品カタログ登録済み） |
| 解約通知 | 解約希望月の**1ヶ月前まで**にLINEで連絡 → 翌月末で解約 |
| 連絡手段 | LINE公式アカウント「とうこさん」(@444ojril) |

---

## 2. クライアントの流れ（完全版）

### STEP 1: LINE友達追加（クライアント側・最初にやること）

1. 「とうこさん」LINE公式アカウントを友達追加
   - LINE ID: **@444ojril**
   - 友達追加URL: `https://line.me/R/ti/p/@444ojril`
2. 友達追加後、ウェルカムメッセージを確認（申込手順が届く）

---

### STEP 2: 申込・フォーム回答（クライアント側）

1. リッチメニュー「サービスに登録する」タップ  
   または直接 **https://saas.shikisai.work/signup** を開く
2. 利用規約を読んでチェックボックスにチェック
3. 「カード登録へ進む →」ボタンを押してStripeでカード登録
4. 彩さんから送られてくるGoogleフォームのURLに回答
   - サロン名・Threadsユーザー名・強み・ターゲット・NGワードなど

---

### STEP 3: アカウント連携（彩さん主導・約5分・Zoom不要）

> ⚡ **この作業のみ彩さんが1回だけ行う。以降は完全自動。**

#### 【準備】フォーム回答後

1. Googleスプレッドシートでサロン名・Threadsユーザー名（@から）を確認してメモ

2. **Meta Developer ConsoleでThreadsテスターとして追加**  
   → 以下のURLをブラウザで開く（Metaログイン済みのブラウザで）  
   `https://developers.facebook.com/apps/1497479218824264/roles/roles/`  
   → 「テスターを追加」からクライアントのInstagram/ThreadsアカウントIDを入力  

   💬 **①Claudeに話しかけるタイミング：** 「テスターIDが分からない」「追加できない」場合

3. クライアントに以下をLINEで連絡する：
   > 「お手数ですが、Threadsの設定ページから承認をお願いします。
   > https://www.threads.com/settings/account
   > 「テスター招待」という項目が出ているはずですので、承認ボタンを押してください。」

4. クライアントから「承認しました」の返信を待つ

#### 【トークン取得・Supabase登録】

5. 以下のURLを開く（Metaにログイン済みのブラウザで）  
   `https://developers.facebook.com/apps/1497479218824264/use_cases/customize/settings/?use_case_enum=THREADS_API&selected_tab=settings&product_route=threads-api`  
   → 「ユーザートークン生成ツール」セクションを探す  
   → 対象ユーザーの「アクセストークンを生成」をクリック → トークンをコピー

   💬 **②Claudeに話しかけるタイミング：** 「ユーザートークン生成ツールが見つからない」「エラーが出る」場合

6. ターミナルを開いて以下を実行：

   ```bash
   cd ~/threads_bot
   export SUPABASE_URL="..." # Supabaseの設定ページから
   export SUPABASE_SERVICE_KEY="..." # Supabase Service Roleキー
   python3 register_saas_user.py
   ```

   - サロン名を入力（英数字・アンダースコア推奨。例：`flowerpetals`）
   - 手順5でコピーしたトークンを貼り付け
   - `✅ Supabase 登録完了` と出れば完了

   💬 **③Claudeに話しかけるタイミング：** スクリプトがエラーになった場合

---

### STEP 4: 初回投稿生成（彩さんが手動実行・1回のみ）

1. GitHubの「SaaS - サロン別投稿生成」ワークフローを手動実行  
   💬 **④Claudeに話しかけるタイミング：** 「手動実行のやり方が分からない」場合
2. 約2分後、投稿JSON（15本分）が生成される  
   💬 「投稿JSONが生成されたか確認して」とClaudeに言えば一緒に確認できる
3. 動作確認：「SaaS - サロン自動投稿」を手動実行して実際に投稿されるか確認

---

### STEP 5: 投稿生成（自動）

- GitHub Actions「SaaS - サロン別投稿生成」が毎週月曜AM10時(JST)に実行
- Googleスプレッドシート → Claude API → `posts_saas/posts_{サロン名}.json` 生成
- 残本数が5本以下になったとき自動で15本追加生成

### STEP 6: 自動投稿（自動・毎日2回）

- GitHub Actions「SaaS - サロン自動投稿」が実行
  - AM9時（JST）：朝投稿
  - PM8時（JST）：夜投稿
- Threads公式API（Graph API）で投稿
- 投稿失敗時はLINEに通知が届く
- **トークン切れ**の場合はLINEに専用通知（→ STEP 3の手順5〜6で再登録、約3分）

### STEP 7: トークン自動更新（自動・月1回）

- GitHub Actions「Threads トークン自動更新」が毎月1日AM8時(JST)に実行
- ベモーレ・個人・全SaaSユーザーのトークンを一括更新
- 失敗した場合のみLINEに通知が届く
- **60日ごとに有効期限があるが月次更新で自動延命**（手動対応不要）

---

## 3. トークン管理

### 自動更新で対応できるケース（約95%）

- 通常の60日有効期限切れ → 月次更新ワークフローで自動延命

### 手動対応が必要なケース（約5%）

- クライアントがThreads/Instagramのパスワードを変更した
- Metaのセキュリティ検知でトークンが強制無効化された
- Meta Developerアプリの設定変更でトークンが無効になった

→ 上記の場合、LINEに通知が届く。STEP 3の手順5〜6（トークン再取得・スクリプト実行）で約3分で復旧できる。

---

## 4. 料金・請求

| 項目 | 内容 |
|------|------|
| 月額 | 2,500円（税別）= 2,750円（税込10%） |
| 最低期間 | 3ヶ月（途中解約時も残月分請求） |
| 請求サイクル | 契約日起算（毎月同日） |
| 決済 | Stripe（クレジットカード自動引き落とし） |
| 決済リンク | https://buy.stripe.com/14AeV64rV9zFcr8f9P5wI00 |
| 申込ページ | https://saas.shikisai.work/signup（利用規約同意→Stripe誘導） |
| 未払い時 | Stripeの自動リトライ → 失敗でサービス停止 |
| 解約 | 3ヶ月後、1ヶ月前までにLINEで連絡 → 翌月末で解約 |

---

## 5. 投稿内容

- 1日2回（朝9時・夜8時）自動投稿
- 内容はGoogleフォームの回答をもとにClaudeが生成
- ギャップ投稿構造（期待と現実のズレを見せる構成）
- 投稿プール：約100本を自動生成、残5本以下で自動再生成

---

## 6. システム構成

```
Googleフォーム（申込）
    ↓
Googleスプレッドシート（サロン情報）
    ↓ generate_saas_posts.py（Claude API）
posts_saas/posts_{サロン名}.json（投稿ストック）
    ↓ post_saas.py（Threads 公式 Graph API）
Threads（自動投稿）

Supabase（salons テーブル）
  - salon_name, threads_user_id, access_token, is_active
    ↑ register_saas_user.py（初回登録・トークン再登録）
    ↑ refresh_threads_token.yml（月次トークン自動更新）

Supabase（post_logs テーブル）
  - 投稿済み記録（重複防止・使用済み管理）

Supabase（line_users テーブル）
  - LINEフォロワー管理
```

---

## 7. GitHub Actions 一覧

| ワークフロー | 実行タイミング | 役割 |
|---|---|---|
| SaaS - サロン自動投稿 (`post_saas.yml`) | 毎日AM9時・PM8時(JST) | Threads投稿（公式API） |
| SaaS - サロン別投稿生成 (`generate_saas_posts.yml`) | 毎週月曜AM10時(JST) | 投稿JSON生成 |
| Threads トークン自動更新 (`refresh_threads_token.yml`) | 毎月1日AM8時(JST) | ベモーレ・個人・全SaaSトークン更新 |
| Heartbeat (`heartbeat.yml`) | 毎日11時・16時・23時(JST) | 投稿確認＋未投稿時の自動リカバリ |

---

## 8. 彩さんの作業（クライアント1人あたり）

| タイミング | 作業 | 所要時間 |
|---|---|---|
| 申込後 | STEP 3の手順でテスター追加→トークン取得→スクリプト実行 | **約5分** |
| 通常時 | なし（完全自動） | 0分 |
| トークン切れのLINE通知が来たとき | STEP 3の手順5〜6でトークン再取得・スクリプト実行 | **約3分** |
| 解約時 | Supabaseで`is_active=false`に変更 | 1分 |

---

## 9. 必要なGitHub Secrets

| Secret名 | 内容 |
|---|---|
| `SUPABASE_URL` | SupabaseプロジェクトURL |
| `SUPABASE_SERVICE_KEY` | Supabase Service Roleキー |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Googleサービスアカウントキー（JSON） |
| `ANTHROPIC_API_KEY` | Claude APIキー |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE通知用チャンネルアクセストークン |

---

## 10. テスト手順（通しテスト）

> テスト用サロン名 `toko_test` 向けの投稿ストックは `posts_saas/posts_toko_test.json` に15本用意済み。

### テスト前提条件

- テスト用Threadsアカウントを Meta Developer Console でThreadsテスターとして承認済みであること
- Supabase の `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` が手元にあること

### テスト手順

1. **STEP 3 の手順でアカウント登録**
   ```bash
   cd ~/threads_bot
   export SUPABASE_URL="..."
   export SUPABASE_SERVICE_KEY="..."
   python3 register_saas_user.py
   # サロン名：toko_test
   # トークン：テスト用アカウントのトークンを貼り付け
   ```

2. **Supabase確認（任意）**
   - Supabase Dashboard → `salons` テーブルに `toko_test` が入っているか確認

3. **投稿生成ワークフロー実行**
   - GitHub Actions → 「SaaS - サロン別投稿生成」 → 「Run workflow」
   - 約2分後、`posts_saas/posts_toko_test.json` が生成されることを確認

   💬 **⑤Claudeに話しかけるタイミング：** 「投稿JSONが生成されたか確認して」

4. **投稿テスト**
   - GitHub Actions → 「SaaS - サロン自動投稿」 → 「Run workflow」 → `slot: morning`
   - テスト用Threadsアカウントに投稿されることをスマホで確認

5. **テスト完了後**
   - テストが成功したら、Supabaseで `toko_test` の `is_active` を `false` にする
   - または実際のユーザーとして使い続ける場合はそのままでOK

---

## 11. オンボーディングチェックリスト（彩さん向け）

### 申込後すぐに確認

- [ ] Stripeで決済完了を確認
- [ ] クライアントがLINE友達追加済みか確認
- [ ] GoogleフォームのURLをLINEで送付

### フォーム回答後

- [ ] Googleスプレッドシートに回答が反映されているか確認
- [ ] Threadsユーザー名（@から）を控える
- [ ] Meta Developer Console でThreadsテスターとして追加
- [ ] クライアントに承認URLをLINEで送付し、承認してもらう

### テスター承認後（約5分の作業）

- [ ] Meta Developer Consoleでトークン生成
- [ ] `python3 register_saas_user.py` を実行してSupabaseに登録
- [ ] `✅ Supabase 登録完了` が出たことを確認
- [ ] GitHubで「SaaS - サロン別投稿生成」を手動実行
- [ ] 約2分後、`posts_saas/posts_{サロン名}.json` が生成されているか確認
- [ ] 「SaaS - サロン自動投稿」を手動実行して動作確認
- [ ] クライアントにLINEで「設定完了・明日から投稿開始」と連絡

---

## 12. LINE公式アカウント（とうこさんBot）

### アカウント情報
- **アカウント名**: とうこさん
- **LINE ID**: @444ojril
- **管理者LINE User ID**: Ucf261a250763ff136250262e4639e9ee
- **Webhook URL**: https://toukosan.nailsalon-flat.workers.dev/webhook（Cloudflare Workers）
- **アイコン**: コーラル色・ロボット＋女性キャラ・自動運用サービス表記
- **インフラ**: line-harness-oss（OSS LINE CRM）/ Cloudflare Workers + D1

### リッチメニュー（設定済み 2026-05-16）

| ボタン | アクション | 動作 |
|---|---|---|
| 左：サービスに登録する | URI | saas.shikisai.work/signup を開く |
| 右：投稿へのリクエスト | ポストバック | 「どんな変更を希望しますか？教えていただいた内容は、今後の投稿づくりの参考にします😊」を自動返信 |

> ポストバック方式のためチャットにユーザーのテキストは表示されない。

### 自動返信ルール（D1に登録済み）

| キーワード | マッチ | 返信内容 |
|---|---|---|
| 投稿へのリクエスト | ポストバック完全一致 | どんな変更を希望しますか？（2行） |

### 実装済みコマンド（彩さん専用）

| コマンド | 動作 |
|---|---|
| `LIST` | 登録サロン一覧を表示（旧Vercel実装・移植待ち） |
| `myid` | 自分のLINE User IDを確認（旧Vercel実装・移植待ち） |

### クライアント向け自動動作
- 友だち追加 → ウェルカムメッセージ送信（シナリオ設定後）
- リッチメニュー右ボタン → 投稿リクエスト自動返信（稼働中）

### Cloudflare Workers 設定
- **Worker名**: toukosan
- **Worker URL**: https://toukosan.nailsalon-flat.workers.dev
- **管理画面**: https://line-harness-admin-6ulitnovv-ayakuroki929s-projects.vercel.app
- Secrets: LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET / API_KEY

### 旧Vercelファイル（参照のみ・現在は使用していない）
- `saas_app/api/line_webhook.py`（URL/LIST/myidコマンドの移植元）

---

## 13. Supabaseテーブル

テーブル定義SQL：`supabase_tables.sql`（Supabase → SQL Editor で実行）

| テーブル | 用途 |
|---|---|
| `salons` | サロン情報・アクセストークン（`id, salon_name, threads_user_id, access_token, is_active`） |
| `post_logs` | 投稿済み記録（重複防止） |
| `line_users` | LINEフォロワー管理 |

---

## 14. 利用規約（確定 2026-05-16）

- ファイル：`saas_app/public/signup/index.html`（サイトに埋め込み済み）
- PDF：デスクトップ `とうこさん_利用規約.pdf`
- 施行日：2026年5月16日
- 主要条項：最低3ヶ月・解約1ヶ月前通知・AI限界免責・障害免責・修正依頼はユーザー責任

---

## 15. スケール時の注意（30人以上のユーザーが増えた場合）

- **投稿・トークン更新は自動スケール**：GitHub Actions が全サロンをループ処理するため、30人でも100人でも追加作業なし
- **Metaアプリ審査（重要）**：現在はアプリが「未公開」のため、Threadsテスターに追加できる人数は最大50人まで。50人を超える前にMetaへアプリ公開申請が必要
  - 申請内容：サービス説明・プライバシーポリシー・動画デモ
  - 💬 **申請時にClaudeに話しかける：** 「Metaアプリ審査の申請を手伝って」

---

## 16. Stripe Webhook 設定手順

> **一度だけ行う設定。設定後は解約・支払い失敗が自動で検知される。**

### Step A: Supabase に stripe_customer_id 列を追加（未実施の場合）

Supabase Dashboard → SQL Editor で実行：
```sql
ALTER TABLE salons ADD COLUMN IF NOT EXISTS stripe_customer_id text;
```

### Step B: Vercel に環境変数を追加

Vercel Dashboard → `saas.shikisai.work` プロジェクト → Settings → Environment Variables で追加：

| 変数名 | 値 |
|---|---|
| `STRIPE_WEBHOOK_SECRET` | StripeのWebhookシークレット（手順Cで取得） |
| `SUPABASE_URL` | SupabaseプロジェクトURL |
| `SUPABASE_SERVICE_KEY` | Supabase Service Roleキー |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE通知用トークン |

### Step C: Stripe に Webhook を登録

1. Stripe Dashboard → 開発者 → Webhook → 「エンドポイントを追加」
2. URL: `https://saas.shikisai.work/api/stripe-webhook`
3. リッスンするイベント：
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. 「署名シークレット」（`whsec_...`）をコピー → Vercelの `STRIPE_WEBHOOK_SECRET` に設定

### Step D: 新規ユーザー登録時に Stripe customer ID も入力

`register_saas_user.py` 実行時に Stripe customer ID（`cus_...`）を入力する手順を追加済み。
Stripe Dashboard → 顧客一覧 → 対象顧客のIDを確認して貼り付ける。

---

## 17. ウェルカムシナリオ設定手順

> **一度だけ行う設定。設定後は友達追加時に自動メッセージが届く。**

```bash
cd ~/threads_bot
export LINE_HARNESS_API_KEY="..."  # Cloudflare Dashboard → toukosan Worker → Settings
python3 setup_welcome_scenario.py
```

API_KEY の確認場所：
- Cloudflare Dashboard → Workers & Pages → toukosan → Settings → Variables and Secrets

---

## 18. 残タスク

- ⏳ 通しテスト（Section 10の手順でテスト用サロン1件を全部流す）
- ⏳ Stripe Webhook 設定（Section 16の手順を実行）
- ⏳ ウェルカムシナリオ設定（Section 17の手順を実行）
- ⏳ 管理者コマンド（LIST・myid）をCloudflare Workerに移植
- ⏳ LINE Channel Secret のローテーション（チャット上で露出したため）
- ⏳ Metaアプリ公開申請（50人超える前に実施）
