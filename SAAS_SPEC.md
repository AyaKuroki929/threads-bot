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

### STEP 1: 申込（クライアント側）

1. **申込ページ**（https://saas.shikisai.work/signup）で利用規約に同意 → Stripeでカード登録
2. **Googleフォーム**に回答（オンボーディングフォーム）
   - サロン名、Threadsユーザー名、業種、強み、ターゲット客層など

### STEP 2: セッション取得（彩さん側・1回のみ）

1. ターミナルで2つのコマンドを起動：
   ```
   # ターミナル1（スリープ防止つきで起動）
   cd ~/threads_bot/session_server
   caffeinate -i python3 server.py

   # ターミナル2
   ngrok http 8765
   ```
2. LINEで「とうこさん」Botに以下を送信：
   ```
   URL {クライアントのLINE User ID} {サロン名} @{Threadsユーザー名}
   ```
3. クライアントのLINEに自動でセットアップURLが送られる
4. クライアントがURLを開いてInstagramログイン → Supabaseに自動保存
5. ログイン完了後、ターミナルを閉じてOK

> ⚡ **この作業のみ彩さんが1回だけ行う。以降は完全自動。**

### STEP 3: 投稿生成（自動）

- GitHub Actions「SaaS - サロン別投稿生成」が毎週月曜AM10時(JST)に実行
- Googleスプレッドシート → Claude API → `posts_saas/posts_{サロン名}.json` 生成
- 残本数が5本以下になったとき自動で15本追加生成

### STEP 4: 自動投稿（自動・毎日2回）

- GitHub Actions「SaaS - サロン自動投稿」が実行
  - AM9時（JST）：朝投稿
  - PM8時（JST）：夜投稿
- Playwrightでブラウザを操作してThreadsに投稿
- 投稿失敗時はLINEに通知が届く

### STEP 5: セッション維持（自動・週1回）

- GitHub Actions「SaaS - セッション週次自動更新」が毎週日曜AM5時(JST)に実行
- 全サロンのセッションをThreadsにアクセスして自動延命・Supabaseに保存
- **セッション切れが発生した場合のみ**LINEに通知が届く
  - 通知が来たらSTEP 2の手順で再ログイン

---

## 3. セッション管理（自動化の限界と対応）

### 自動更新で対応できるケース（約90%）

- 通常のCookieの有効期限切れ
- ブラウザセッションの自然な期限切れ

### 手動対応が必要なケース（約10%）

- クライアントがパスワードを変更した
- Instagramにログイン済み端末を「全デバイスからログアウト」した
- Metaのセキュリティ検知で強制ログアウトされた

→ 上記の場合、LINEに通知が届くのでSTEP 2の手順で再ログイン（約5分）

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
    ↓ post_saas_playwright.py（Playwright）
Threads（自動投稿）

Supabase（salons テーブル）
  - salon_name, threads_username, session_data, is_active
    ↑ session_server/server.py（初回ログイン・ngrok経由）
    ↑ refresh_sessions_saas.py（週次自動更新）

Supabase（post_logs テーブル）
  - 投稿済み記録（重複防止・使用済み管理）

Supabase（line_users テーブル）
  - LINEフォロワー管理
```

---

## 7. GitHub Actions 一覧

| ワークフロー | 実行タイミング | 役割 |
|---|---|---|
| SaaS - サロン自動投稿 | 毎日AM9時・PM8時(JST) | Threads投稿（Playwright方式のみ） |
| SaaS - サロン別投稿生成 | 毎週月曜AM10時(JST) | 投稿JSON生成 |
| SaaS - セッション週次自動更新 | 毎週日曜AM5時(JST) | セッション延命 |

---

## 8. 彩さんの作業（クライアント1人あたり）

| タイミング | 作業 | 所要時間 |
|---|---|---|
| 申込後 | STEP 2の手順でセッション取得 | 5分 |
| 通常時 | なし（完全自動） | 0分 |
| LINE通知が来たとき | 同上手順で再ログイン | 5分 |
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
| `LINE_ADMIN_USER_ID` | 彩さんのLINE User ID |

---

## 10. オンボーディング手順（彩さん向けチェックリスト）

- [ ] Stripeで決済確認
- [ ] Googleフォーム回答確認（スプレッドシートに反映されているか）
- [ ] `SaaS - サロン別投稿生成` を手動実行（投稿JSONを生成）
- [ ] STEP 2の手順でセッション取得
- [ ] 動作確認：GitHub Actionsを手動実行（SaaS - サロン自動投稿 → morning）

---

## 11. セッション取得の仕組み（技術詳細）

### 概要

彩さんのMacでサーバーを起動し、ngrokで公開URLを作る。クライアントがそのURLを開くと、彩さんのMac上でブラウザが動き、その画面がリアルタイムでクライアントのブラウザに映し出される。クライアントは画面の中でThreadsにログインするだけ。パスワードは彩さんに届かない。

### URLの構造

- **ドメイン**（例：`squealer-goofiness-undying.ngrok-free.dev`）→ 変わらない（同じngrokアカウントなら固定）
- **`/setup/xxxxxxxxxxxxxxxx`** → クライアントごとに変わる（1回使い切り・ログイン完了で自動無効化）

### 彩さんの操作手順（クライアント1人あたり）

1. ターミナルで `session_server/` フォルダへ移動してサーバー起動（スリープ防止つき）：
   ```
   caffeinate -i python3 server.py
   ```
2. 別ターミナルでngrok起動：`ngrok http 8765`
3. LINEのとうこさんBotに `URL {line_id} {サロン名} @username` を送信
4. クライアントのLINEにセットアップURLが自動送信される
5. クライアントがログインすると自動でSupabaseに保存される → 完了

### クライアントの操作（スマートフォン対応済み）

1. 受け取ったURLをブラウザで開く（初回のみngrokの警告が出る → Visit Siteを押す）
2. 画面に表示されたThreadsの「Continue with Instagram」をタップ
3. 画面下の「**Abc テキスト**」ボタンが選択中の状態で入力欄をタップしてID（メールアドレス等）を入力
4. 「**🔐 パスワード**」ボタンを押してからパスワードを入力（文字が隠れます）
5. ボタンが小さい場合は2本指でピンチして画面を拡大できる
6. 「セットアップ完了」が表示されたらページを閉じる → 以上

> 🔒 パスワードは彩さんのMacに記録されない。クライアントが自分でブラウザ上に入力するだけ。

### サーバーファイルの場所

- `~/threads_bot/session_server/server.py`
- `~/threads_bot/session_server/.env`（Supabaseの接続情報）

---

## 12. LINE公式アカウント（とうこさんBot）

### アカウント情報
- **アカウント名**: とうこさん
- **LINE ID**: @444ojril
- **管理者LINE User ID**: Ucf261a250763ff136250262e4639e9ee
- **Webhook URL**: https://saas.shikisai.work/api/line-webhook
- **アイコン**: コーラル色・ロボット＋女性キャラ・自動運用サービス表記

### 実装済みコマンド（彩さん専用）

| コマンド | 動作 |
|---|---|
| `URL {line_id} {サロン名} {@username}` | セットアップURLをクライアントのLINEに送信 |
| `LIST` | 登録サロン一覧を表示 |
| `myid` | 自分のLINE User IDを確認 |

### クライアント向け自動動作
- 友だち追加 → ウェルカムメッセージ送信 ＋ 彩さんに通知
- セットアップURL受信 → ブラウザでログイン → Supabaseに自動保存

### Vercel環境変数（saas_app）
- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `LINE_ADMIN_USER_ID`
- `NGROK_BASE_URL`（squealer-goofiness-undying.ngrok-free.dev）
- `SUPABASE_URL` / `SUPABASE_SERVICE_KEY`

### ファイル
- `saas_app/api/line_webhook.py`

---

## 13. Supabaseテーブル

テーブル定義SQL：`supabase_tables.sql`（Supabase → SQL Editor で実行）

| テーブル | 用途 |
|---|---|
| `salons` | サロン情報・セッションデータ |
| `post_logs` | 投稿済み記録（重複防止） |
| `line_users` | LINEフォロワー管理 |

---

## 14. 利用規約（確定 2026-05-16）

- ファイル：`saas_app/public/signup/index.html`（サイトに埋め込み済み）
- PDF：デスクトップ `とうこさん_利用規約.pdf`
- 施行日：2026年5月16日
- 主要条項：最低3ヶ月・解約1ヶ月前通知・AI限界免責・障害免責・修正依頼はユーザー責任

## 15. 残タスク

- ⏳ 通しテスト（テスト用サロン1件で STEP 1〜5 を全部流す）
