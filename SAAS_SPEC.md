# とうこさん サービス仕様書

> Threads自動投稿代行サービス「とうこさん」の全体仕様・運用フロー
>
> 🔄 2026-06-19 更新：連携をOAuth方式（クライアント主導・callback自動登録）に、投稿を1日3回（7/12/21時JST・昼ツリー）に更新。

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
   - URL: https://docs.google.com/forms/d/e/1FAIpQLSc4RAj_6O1nP6_9Ehm5FyLp_tFv4qgO3mQTUf2FHs9hsvz1cw/viewform
   - サロン名・Threadsユーザー名・強み・ターゲット・NGワードなど

---

### STEP 3: アカウント連携（OAuth・クライアント主導／彩さんは2操作のみ）

> ⚡ **旧方式（手動でトークン生成＋`register_saas_user.py`実行）は廃止。現在はOAuthでクライアント自身が連携し、`/api/callback` が自動でSupabase登録＋初回投稿生成まで行う。**

#### フォーム回答後（自動）

1. クライアントがGoogleフォームに回答すると、GAS（`gas_form_handler.gs`）が彩さんに「📋 フォーム回答あり」をLINE通知。通知にはサロン名・Threads ID・**send-stepリンク**が入っている。

#### 彩さんの操作（2つだけ）

2. **Meta Developer ConsoleでThreadsテスターとして追加**
   `https://developers.facebook.com/apps/1497479218824264/roles/roles/`
   → クライアントのInstagram/ThreadsユーザーIDを入力
   （※Metaアプリ審査が承認されれば、このテスター追加は不要になる）

   💬 **Claudeに話しかけるタイミング：** 「テスターIDが分からない」「追加できない」場合

3. 通知内の **send-stepリンクをタップ** → STEP1/STEP2の連携手順がクライアントのLINEに自動送信される（本文は `saas_app/api/send-step.py` が生成）

#### クライアントの操作（連携STEP1 → 連携STEP2）

4. **連携STEP1**：Threadsアプリ → プロフィール右上≡ → **その他の設定** → ウェブサイトのアクセス許可 →「Invites」タブ →「Threads Auto Post」の【同意する】

5. **連携STEP2**（STEP1の5分後）：**シークレットモード**のブラウザで連携URL（`https://saas.shikisai.work/api/connect?customer_id=...`）を開く → Threads用アカウントでログイン → 同意画面で3権限を許可

6. → `/api/callback` が **salonをSupabaseに自動登録（`is_active=true`）＋初回投稿生成を自動トリガー** → 完了すると彩さんに「✅ 投稿生成完了」が通知され、当日から自動配信開始

> 彩さんの手作業は「①テスター追加」＋「②send-stepリンクをタップ」の2つだけ。トークン取得もスクリプト実行も不要（OAuthが自動でトークン取得・登録・投稿生成まで行う）。

---

### STEP 5: 投稿生成（自動）

- GitHub Actions「SaaS - サロン別投稿生成」が毎週月曜AM10時(JST)に実行
- Googleスプレッドシート → Claude API → `posts_saas/posts_{サロン名}.json` 生成
- 残本数が5本以下になったとき自動で15本追加生成

### STEP 6: 自動投稿（自動・毎日3回）

- GitHub Actions「SaaS - サロン自動投稿」＋ cron-job.org が実行
  - 朝7時（JST）：朝投稿（単発）
  - 昼12時（JST）：昼投稿（ツリー2部）
  - 夜21時（JST）：夜投稿（単発）
  - 正時はcron-job.orgが担当、GitHubの予備cronが正時+30分。深夜など想定時間帯外に遅延発火した場合は投稿せずスキップ（時間帯ガード）
- Threads公式API（Graph API）で投稿
- 投稿失敗時はLINEに通知が届く
- **トークン切れ**の場合はLINEに専用通知（→ クライアントが連携STEP2のURLをもう一度開いてOAuthし直せば復旧。彩さんはsend-stepリンクを再送するだけ）

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

→ 上記の場合、LINEに通知が届く。クライアントが連携STEP2のURLをもう一度開いてOAuthし直せば復旧（彩さんはsend-stepリンクを再送するだけ）。トークン取得・スクリプト実行は不要。

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

- 1日3回（朝7時・昼12時・夜21時 JST）自動投稿（昼はツリー2部、朝・夜は単発）
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
    ↑ /api/callback（OAuth連携時に自動登録・is_active=true・初回投稿生成もトリガー）
    ↑ refresh_threads_token.yml（月次トークン自動更新）
    ↑ register_saas_user.py（手動登録・テスト用。通常運用はOAuthで不要）

Supabase（post_logs テーブル）
  - 投稿済み記録（重複防止・使用済み管理）

Supabase（line_users テーブル）
  - LINEフォロワー管理
```

---

## 7. GitHub Actions 一覧

| ワークフロー | 実行タイミング | 役割 |
|---|---|---|
| SaaS - サロン自動投稿 (`post_saas.yml`) | 毎日 朝7時・昼12時・夜21時(JST) | Threads投稿（公式API） |
| SaaS - サロン別投稿生成 (`generate_saas_posts.yml`) | 毎週月曜AM10時(JST) | 投稿JSON生成 |
| Threads トークン自動更新 (`refresh_threads_token.yml`) | 毎月1日AM8時(JST) | ベモーレ・個人・全SaaSトークン更新 |
| Heartbeat (`heartbeat.yml`) | 毎日11時・16時・23時(JST) | 投稿確認＋未投稿時の自動リカバリ |

---

## 8. 彩さんの作業（クライアント1人あたり）

| タイミング | 作業 | 所要時間 |
|---|---|---|
| フォーム回答後 | テスター追加＋send-stepリンクをタップ（以降はクライアントがOAuthで自分で連携） | **約2分** |
| 通常時 | なし（完全自動） | 0分 |
| トークン切れのLINE通知が来たとき | send-stepリンクを再送 → クライアントがOAuthし直す | **約2分** |
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
| 右：投稿へのリクエスト | ポストバック | 改訂版（トラブル防止条項3つ＋反映時期説明付き）を自動返信。詳細は下の「自動返信ルール」参照 |

> ポストバック方式のためチャットにユーザーのテキストは表示されない。

### 自動返信ルール（D1に登録済み）

| キーワード | マッチ | 返信内容 |
|---|---|---|
| 投稿へのリクエスト | ポストバック完全一致 | 「どんな変更を希望しますか？」冒頭＋トラブル防止条項3つ（過去投稿修正不可・該当投稿は手動削除・スクショ依頼）＋反映時期説明（既存ストック消費まで順次配信・月曜AM10時生成時に反映）。line-harness D1 auto_replies ID: c58d0f04 |

### 実装済みコマンド（彩さん専用）

| コマンド | 動作 |
|---|---|
| `LIST` | 登録サロン一覧を表示（🟢稼働中・Cloudflare Worker） |
| `myid` | 自分のLINE User IDを確認（🟢稼働中・Cloudflare Worker） |

### クライアント向け自動動作
- 友だち追加 → ウェルカムシナリオ自動送信（🟢稼働中・2ステップ）
- 友だち追加 → 彩さんに新規フォロワー通知（🟢稼働中）
- リッチメニュー右ボタン → 投稿リクエスト自動返信（🟢稼働中）

### Cloudflare Workers 設定
- **Worker名**: toukosan
- **Worker URL**: https://toukosan.nailsalon-flat.workers.dev
- **管理画面**: https://line-harness-admin-6ulitnovv-ayakuroki929s-projects.vercel.app
- Secrets: LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET / API_KEY

### Vercelファイル（LINE用ではなくStripe用として待機）
- `saas_app/api/line_webhook.py`（LINE Webhookは現在Cloudflare Workerが処理・このファイルはイベントを受け取らない）
- `saas_app/api/stripe_webhook.py`（🟢 稼働中 — Stripe解約/支払失敗を処理）

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

## 18. 解約フロー（彩さん向け手順）

クライアントから「解約したい」とLINEが来たとき：

1. **契約期間確認**：Supabase `salons` テーブルの `created_at` を確認。契約日から**3ヶ月未満**なら解約不可と返信
   > 「最低契約期間の3ヶ月を満了されていないため、現時点でのご解約はお受けできません。〇月〇日以降にご連絡ください。」

2. **3ヶ月以上経過している場合**：翌月末解約を確認してStripeを操作
   - Stripe Dashboard → 顧客 → 該当サブスクリプション → 「サブスクリプションを解約」→「**期間終了時に解約**」を選択（即時解約は選ばない）
   - クライアントに返信：
     > 「承りました。〇月末日をもってサービスを終了いたします。最終投稿日は〇月〇日です。」

3. **サービス終了日に**：Supabase → `salons` テーブル → 該当行の `is_active` を `false` に変更

> ⚠️ Stripeで「期間終了時に解約」を選ぶと `customer.subscription.deleted` イベントが期間末に発火し、Webhookが自動で `is_active=false` にする。**手順3は念のための確認**として実施。

---

## 19. 投稿内容クレーム対応（彩さん向け）

クライアントから「この投稿の内容が間違っている」「削除してほしい」と連絡が来たとき：

### 既に投稿されたものを削除したい場合
- Threadsの投稿削除はクライアント自身がアプリ上で行う（当社では削除できない）
- 返信テンプレート：
  > 「ご連絡ありがとうございます。Threadsアプリを開いて、該当の投稿を長押し→「削除」で削除いただけます。今後の投稿で同様の表現を使わないよう、内容を反映いたします。」

### 今後の投稿に反映させたい場合
1. Googleスプレッドシートの該当サロン行で `NGワード` 列や `発信スタイルのNGライン` 列に内容を追記
2. GitHub Actions → 「SaaS - サロン別投稿生成」を手動実行（サロン名を指定）
3. 新しい投稿JSONが再生成され、翌投稿から反映される

---

## 20. サービス開始タイミング（SLA）

| フェーズ | 目安 |
|---|---|
| Stripe決済完了 → 彩さんへLINE通知 | 即時（Webhookは稼働中だが、現時点は手動確認） |
| 彩さんがStripe確認 → フォームURL送付 | **当日中〜翌営業日** |
| フォーム回答 → アカウント連携作業 | **回答確認後2営業日以内** |
| 連携完了 → 投稿開始 | 当日（手動実行）または翌朝9時自動投稿 |

> クライアントへの案内文（ウェルカムシナリオ2通目に含める）：
> 「お申込み後、通常2営業日以内にGoogleフォームのURLをこちらからお送りします。フォームご回答後、担当者が設定作業（約5分）を行い、翌日以降から自動投稿が開始されます。」

---

## 21. GitHub Secrets・Vercel 環境変数 追加項目（2026-05-18）

LINE broadcastをpush（管理者専用）に変更したことで、以下の追加設定が必要：

### GitHub Actions Secret
| Secret名 | 値 |
|---|---|
| `LINE_ADMIN_USER_ID` | `Ucf261a250763ff136250262e4639e9ee` |

> GitHub → threads-botリポジトリ → Settings → Secrets and variables → Actions → 「New repository secret」

### Vercel 環境変数
| 変数名 | 値 |
|---|---|
| `LINE_ADMIN_USER_ID` | `Ucf261a250763ff136250262e4639e9ee` |

> Vercel Dashboard → `saas.shikisai.work` → Settings → Environment Variables

---

## 22. 残タスク

- ⏳ **通しテスト**（Section 10の手順でテスト用サロン1件を全部流す）
- 🟢 Stripe Webhook 設定（完了 2026-05-17）
- 🟢 ウェルカムシナリオ設定（完了 2026-05-17 — 2ステップ）
- 🟢 管理者コマンド（LIST・myid）Cloudflare Worker移植・稼働中
- 🟢 LINE broadcast→push修正（完了 2026-05-18 — post_saas.yml・stripe_webhook.py）
- 🔴 **GitHub Secret `LINE_ADMIN_USER_ID` を追加**（Section 21参照・テスト前に必須）
- 🔴 **Vercel 環境変数 `LINE_ADMIN_USER_ID` を追加**（Section 21参照・テスト前に必須）
- ⏳ LINE Channel Secret のローテーション（チャット上で露出したため・任意）
- ⏳ Metaアプリ公開申請（50人超える前に実施）
