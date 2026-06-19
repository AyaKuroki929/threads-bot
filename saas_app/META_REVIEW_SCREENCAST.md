# Meta App Review — Screencast Script（絶対に通す版）
**App:** Threads Auto Post  ·  **Permissions:** `threads_basic`, `threads_content_publish`, `threads_manage_replies`
**狙い:** 過去2回の却下理由（"エンドツーエンドが不十分 / in your app が無い"）を完全に潰す。
**録画:** 1本の連続画面録画（カット編集しない）／**英語字幕**／各画面2〜3秒キープ／所要2〜3分。

---

## ★ 録画前の必須準備（これを忘れると同意画面が出ず、また落ちます）
1. **アプリの認証を一度解除する**（＝次のOAuthで「権限許可画面」を確実に出すため）
   - スマホのThreadsアプリ → プロフィール右上≡ → **その他の設定** → **ウェブサイトのアクセス許可（Apps and websites / Website permissions）** → **「Threads Auto Post」を探して解除/削除**
   - ※解除しても、録画中のOAuthで再連携されるので問題なし（既存データも壊れません）
2. **シークレット/プライベートウィンドウ**でChromeを開く（Threadsログインを最初から見せるため）。
3. Threads **@aya_0929_private** のID/パスワードを用意。
4. 結果確認用に**ネイティブThreads**（スマホアプリ or threads.com）を @aya_0929_private で開けるように。
5. 画面録画ソフト＋英語字幕の準備。**URLバーを常に映す**（＝"on our app platform" の証明）。

> ⚠️ ログインは必ず `https://saas.shikisai.work/connect`（customer_id無し）から。既存データを汚しません。

---

## Scene 1 — Intro（約5秒）
- **Show:** `https://saas.shikisai.work/`（ランディング）
- **Caption (EN):** "Threads Auto Post helps salon owners schedule and publish posts to their Threads profile."

## Scene 2 — Threads OAuth login & permission grant 〔threads_basic〕（約45秒）★最重要
- **Action:** URLバーに `https://saas.shikisai.work/connect` を入力して開く（URLを見せる）。
- **Caption (EN):** "The user links their Threads account using the Threads OAuth login on our app platform."
- **Action:** Threadsの**ログイン画面**でID・パスワードを入力してログイン（入力の様子を見せる）。
- **Action:** **権限の同意画面**が表示される。ここで**3秒しっかり止める**。画面に出ている権限を見せる。
- **Caption (EN):** "The user is asked to grant our app three permissions: **threads_basic**, **threads_content_publish**, and **threads_manage_replies**."
- **Action:** **「許可 / Allow」**を押す。
- **Action:** アプリの「接続完了 / Connected」画面に戻る。
- **Caption (EN):** "The account is now linked to our app."

## Scene 3 — Dashboard: connected account 〔threads_basic〕（約15秒）
- **Action:** `https://saas.shikisai.work/dashboard?account=aya_0929_private` を開く。
- **Show:** "Connected Threads account: @aya_0929_private" ＋ 最近の投稿一覧。
- **Caption (EN):** "Using **threads_basic**, our app reads and displays the connected account and its recent posts."

## Scene 4 — Publish a post + reply, shown IN THE APP 〔content_publish ＋ manage_replies〕（約35秒）
- **Caption (EN):** "Now we publish a new post and a reply to it, directly from our app."
- **Action:** **「Publish demo post + reply」**ボタンを押す。
- **Show:** 「Publishing…」→ 数秒後に**結果カード**が表示：
  - **Post** … created with threads_content_publish ＋ 投稿本文 ＋ "View on Threads"
  - **↳ Reply** … created with threads_manage_replies ＋ 返信本文 ＋ "View on Threads"
- **Caption (EN):** "The post was created with **threads_content_publish**, and the reply with **threads_manage_replies**. Both results are shown here inside our app."

## Scene 5 — View the same results in the native Threads app（約30秒）★"and in the native Threads App"
- **Action:** 結果カードの **Post の "View on Threads"** を押す → ネイティブThreadsで投稿を表示。
- **Caption (EN):** "Here is the published post in the native Threads app."
- **Action:** 同じ投稿に付いている **返信** を表示（タップして展開）。
- **Caption (EN):** "And here is the reply, shown under the same post in the native Threads app."

## Scene 6 — Recap（約10秒）
- **Action:** ダッシュボードに戻る。
- **Caption (EN):** "End to end, inside our app: link the account (threads_basic) → publish a post (threads_content_publish) → reply to it (threads_manage_replies)."

---

## 提出方法（重要）
- この**1本の動画を、3権限すべてのスクリーンキャスト欄＋「審査担当者の指示」のdocuments欄**にアップ（使い回しOK）。
- 動画は**英語字幕入り**でアップロード。

## 再申請ノート欄に貼る文（English — コピペ用）
> This screencast shows the complete end-to-end experience in a single continuous recording:
> 1) The full Threads OAuth login flow and the consent screen, where the account grants threads_basic, threads_content_publish, and threads_manage_replies.
> 2) Inside our app dashboard, we read and display the connected account and its posts (threads_basic).
> 3) We publish a new post to the Threads profile from our app, and the result is shown both in our app and in the native Threads app (threads_content_publish).
> 4) We create a reply to that same post on behalf of the profile, and the reply is shown both in our app and in the native Threads app (threads_manage_replies).
> The app uses a standard frontend Threads OAuth login (shown in the video); it does not use a system user token.

---

## 提出前チェックリスト（全部✓で提出）
- [ ] 録画前にアプリの認証を解除した（→同意画面が出た）
- [ ] Threadsの**ログイン入力**が映っている
- [ ] **同意画面で3権限**が見えている（3秒キープ）
- [ ] URLバーに **saas.shikisai.work** が映っている
- [ ] **投稿**が「アプリ内（結果カード）」と「ネイティブThreads」の両方で見えている
- [ ] **返信**が「アプリ内（結果カード）」と「ネイティブThreads」の両方で見えている
- [ ] 字幕はすべて**英語**
- [ ] **1本の連続録画**（カットで誤魔化していない）／早送りしていない
