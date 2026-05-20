# App Review 提出準備（ビジネス認証完了後にそのままコピペ）

---

## ① アプリ設定タブ（確認のみ）

確認項目：
- アプリ名：Threads Auto Post（または「とうこさん」）
- プライバシーポリシーURL：https://saas.shikisai.work/privacy.html ✅
- 利用規約URL：https://saas.shikisai.work/terms.html ✅
- アプリアイコン：設定済み ✅
- アプリURL：https://saas.shikisai.work

---

## ② threads_basic｜許可された用途

```
とうこさんは、ネイルサロン・エステなどの美容サロン向けに
Threads自動投稿を代行するサービスです。

threads_basic は以下の目的のみで使用します：

・OAuth認証完了後にユーザーのThreads user_id と username を取得し、
  どのサロンのアカウントが連携されたかを識別する
・取得した user_id と username をデータベースに保存し、
  以降の自動投稿時に正しいアカウントへ投稿するための識別子として使用する

取得したプロフィール情報を第三者に提供したり、
広告・マーケティング目的に利用することはありません。
```

---

## ③ threads_content_publish｜許可された用途

```
threads_content_publish は以下の目的のみで使用します：

・サロンオーナーがOAuth認証で連携後、そのThreadsアカウントに対して
  1日2回（毎朝9時・毎夜8時）の自動投稿を行う
・投稿内容はサロンオーナーが申込時に提供したサロン情報
  （施術内容・ターゲット客層・強みなど）をもとにAIが生成する

背景：
SNSの継続的な発信はサロン集客に効果的ですが、
施術に忙しいサロンオーナーが毎日投稿を続けることは難しい。
このサービスにより、オーナーの負担なく継続的な情報発信が実現できる。

投稿はすべてサービス加入サロンのアカウントのみに行い、
第三者アカウントへの無断投稿は行いません。
```

---

## ④ データの取り扱い

```
収集するデータ：
・Threads user_id（アカウント識別用）
・Threads username（表示名・ログ管理用）
・OAuthアクセストークン（自動投稿の実行権限）

保存場所：
・Supabase（Amazon AWS上でホスト、暗号化済み）

利用目的：
・サロンのThreadsアカウントを識別し代理投稿を行うため

保持期間：
・契約期間中、および解約後90日間保持した後、完全に削除

第三者への提供：
・なし（決済処理のためのStripe社のみ業務委託先として使用）

データ削除リクエスト：
・LINE公式アカウント @444ojril または以下URLより受付
・https://saas.shikisai.work/privacy.html
```

---

## ⑤ 審査担当者の指示（Reviewer Instructions）

```
【アプリ概要】
とうこさんは美容サロン向けのThreads自動投稿代行サービスです。
サロンオーナーがOAuth認証でThreadsアカウントを連携すると、
以降は1日2回自動でThreadsに投稿されます。

【テスト手順】

STEP 1：OAuth連携フロー（threads_basic の使用）
1. 以下のURLにアクセスしてください
   https://saas.shikisai.work/api/connect?customer_id=review_test
2. Threadsの認証画面（threads.net/oauth/authorize）にリダイレクトされます
3. テスト用アカウントでログインし「許可する」をクリック
4. 「✅ 接続が完了しました」画面が表示されれば連携成功
   ※ この時点で threads_basic を使用しています（user_id・username の取得）

STEP 2：自動投稿（threads_content_publish の使用）
スクリーンキャスト動画をご確認ください。
Threads APIを通じて投稿が作成・公開される様子を録画しています。

【テスト用認証情報】
Threadsテストアカウント：[スクリーンキャスト内で使用するアカウントを記載]
※ スクリーンキャストでは実際のThreadsアカウントへの投稿をデモしています

【アプリURL】
https://saas.shikisai.work

【プライバシーポリシー】
https://saas.shikisai.work/privacy.html

【利用規約】
https://saas.shikisai.work/terms.html
```

---

## ⑥ スクリーンキャスト録画チェックリスト

録画前の準備：
- [ ] bemolle_diet の is_active を一時的に true に変更（Supabase）
- [ ] 解像度を1080p以上に設定
- [ ] 画面収録ソフト起動（QuickTime Player でOK）

録画内容（5分以内に収める）：
1. [ ] https://saas.shikisai.work/api/connect?customer_id=review_test を開く
2. [ ] Threadsログイン画面が表示されることを見せる
3. [ ] bemolle_diet アカウントでログイン
4. [ ] 「✅ 接続が完了しました」画面
5. [ ] GitHub Actions → saas_post.yml を手動実行
6. [ ] Threads の bemolle_diet アカウントに投稿が公開されることを確認

録画後：
- [ ] bemolle_diet の is_active を false に戻す
- [ ] テスト投稿を削除（必要であれば）
- [ ] 動画をGoogle DriveまたはYouTube（限定公開）にアップロード
- [ ] URLをApp Reviewフォームに貼り付け

---

## ⑦ ビジネス認証完了後のアクション順序

1. ビジネス認証完了メール確認
2. Meta Developer Console → App Review → 認証タブが緑になっていることを確認
3. 「アプリ設定」タブ：上記①の内容を確認・入力
4. 「許可された用途」タブ：上記②③をコピペ
5. 「データの取り扱い」タブ：上記④をコピペ
6. スクリーンキャスト録画（上記⑥の手順で）
7. 「審査担当者の指示」タブ：上記⑤をコピペ＋動画URLを追記
8. 提出

---

*作成：2026-05-20*
