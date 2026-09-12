# Meta App Review — `threads_manage_insights` 申請セット

**App:** Threads Auto Post（App ID 1497479218824264）
**追加で取る権限:** `threads_manage_insights` の1つだけ
**すでに承認済み:** `threads_basic` / `threads_content_publish` / `threads_manage_replies`（2026-07-14）＋アクセス認証（Tech Provider・2026-07-17）

---

## なぜ要るのか（1文）

今のとうこさんSaaSは、2,700本以上を自動投稿してきたのに、**どの投稿が何人に見られたかを1件も測れていない**。
見られている投稿の型が分からないままなので、良くなっているのか悪くなっているのかを誰も判断できない。

この権限が通ると、投稿ごとの表示回数・いいね・返信をダッシュボードに出せる。
出せるようになると、伸びた型を次の生成ルールに反映できる。

---

## 現在の状態（2026-09-13 実測）

Threads APIに問い合わせた実際の返り：

```
GET /{user-id}/threads_insights?metric=views,likes,replies,reposts,quotes,followers_count
→ {"error":{"message":"Application does not have permission for this action","code":10,...}}
```

コード側は先に入れてある（未承認でも落ちず、画面に理由が出る）。

| 場所 | 内容 |
|---|---|
| `saas_app/api/dashboard.py` | `_insights_payload()` と `?action=insights`、画面に「Post performance (insights)」カードと Load insights ボタン |
| `saas_app/api/connect.py` | `?insights=1` を付けたときだけ `threads_manage_insights` をスコープに足す（通常の申込みリンクは変えない＝クライアントの連携を壊さない） |

---

## 彩さんの作業（順番どおりに）

### STEP 0. Vercelへデプロイ（これをしないと画面に出ません）

`saas_app/` は自動デプロイではないので、先に本番へ上げる。

### STEP 1. デモ用アカウントを、インサイト付きで連携し直す

未承認の権限は「アプリの管理者・テスター」でだけ許可される。だから**彩さんのアカウントで**取り直す。

1. スマホのThreadsアプリ → プロフィール右上 ≡ → **その他の設定** → **ウェブサイトのアクセス許可** → **Threads Auto Post を削除**
   （削除しないと同意画面が出ない。録画でも同じ手順が必要）
2. Chromeの**シークレットウィンドウ**で次を開く（URLバーを映したまま）

   ```
   https://saas.shikisai.work/connect?insights=1
   ```
3. Threads **@aya_0929_private** でログイン → 同意画面で **4つの権限**が並ぶのを確認 → 許可
4. 連携完了画面に戻る

> ⚠️ この連携し直しで書き換わるのは `salons` の aya_0929_private の行だけ。
> このアカウントは `is_active=false`（投稿対象外）なので、クライアントの投稿には影響しません。

### STEP 2. 数字が出るか確認

```
https://saas.shikisai.work/dashboard?account=aya_0929_private
```
「Load insights」を押して、Views / Likes / Replies に数字が出れば準備完了。
まだ `Application does not have permission` が出る場合は、Meta Dev Console の
**アプリのロール**で @aya_0929_private がテスターまたは管理者になっているかを見る。

### STEP 3. 録画（1本・カット編集なし・英語字幕・所要2分）

| Scene | 映すもの | 英語字幕 |
|---|---|---|
| 1 | `https://saas.shikisai.work/` | "Threads Auto Post schedules and publishes posts for salon owners." |
| 2 | `/connect?insights=1` → Threadsログイン → **同意画面で3秒止める** → 許可 | "The user grants four permissions, including **threads_manage_insights**." |
| 3 | `/dashboard?account=aya_0929_private` に @ユーザー名と最近の投稿 | "Using threads_basic, our app shows the connected account." |
| 4 | **Load insights** を押す → Account totals（Views/Likes/Replies/Reposts/Quotes/Followers）が出る | "With **threads_manage_insights**, our app reads the account's insights and displays them here, inside our app." |
| 5 | その下の投稿ごとの数字を映す（2〜3秒止める） | "Per-post views, likes and replies are shown for each scheduled post, so the salon owner can see which posts actually reached people." |
| 6 | ネイティブThreadsアプリのインサイト画面を開き、同じ投稿の数字を見せる | "The same numbers as in the native Threads app." |
| 7 | ダッシュボードに戻る | "End to end, inside our app: link the account → publish → measure the result." |

> 過去2回の却下理由は「**in your app** で結果を見せていない」だった。
> Scene 4と5（アプリの画面に数字が出ているところ）が今回の合否そのもの。ここを一番長く映す。

### STEP 4. 提出

アプリレビュー画面 → https://developers.facebook.com/apps/1497479218824264/app-review/submissions/

1. `threads_manage_insights` を追加
2. スクリーンキャスト欄に上の動画をアップ
3. 「許可された用途」に下の英語をそのまま貼る

```
Our app (Threads Auto Post) schedules and publishes posts to the Threads profile
of each salon owner who links their account.

We use threads_manage_insights for one purpose: to show each account owner how their
own scheduled posts performed, inside our app.

In the dashboard shown in the screencast, our app calls
GET /{threads-user-id}/threads_insights (views, likes, replies, reposts, quotes,
followers_count) and GET /{media-id}/insights (views, likes, replies, reposts, quotes)
for the connected account only, and renders the numbers in the
"Post performance (insights)" panel.

The salon owner uses these numbers to decide which kind of post to keep publishing.
We do not read insights for accounts that have not linked themselves to our app,
we do not aggregate data across accounts, and we do not share or sell it.
```

4. 「審査担当者の指示」欄に貼る英語

```
Test account: @aya_0929_private (added as a tester of this app).

1. Open https://saas.shikisai.work/connect?insights=1
2. Log in with the Threads test account and grant the permissions.
3. Open https://saas.shikisai.work/dashboard?account=aya_0929_private
4. Click "Load insights".
   - "Account totals" shows views / likes / replies / reposts / quotes / followers,
     read with threads_manage_insights.
   - Below it, each recent post shows its own views / likes / replies / reposts / quotes.

All insights are read only for the account that completed the OAuth login above.
```

---

## 却下されたときに見るところ

- 「in your app が無い」→ Scene 4・5 の尺が足りない。数字が出ている画面を長く映し直す
- 「用途が不明」→ 「許可された用途」の英語が短すぎる。上の全文を貼る
- 「テストできない」→ @aya_0929_private がテスターから外れている

---

## 通ったあとにやること

1. `connect.py` の `SCOPE` に `threads_manage_insights` を入れる（全クライアントの新規連携に乗る）
2. 既存11アカウントは連携し直しが必要（＝彩さんからクライアントへ連携リンクを送り直す）。
   送らずに済ませたい場合は、承認後もインサイトは彩さんのアカウントだけで測れる
3. 投稿ごとの数字を `post_logs` に書き戻して、伸びた型を生成ルール（`GENERATE_RULES_saas.md`）へ反映する
