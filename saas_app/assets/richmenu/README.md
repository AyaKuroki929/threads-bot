# とうこさんOA リッチメニュー

LINE公式アカウント「とうこさん」（@444ojril）のリッチメニュー。

- `toukosan_richmenu_2026-10-22.jpg` … 現在のメニュー画像（2500×1686・3ボタン）
- `toukosan_richmenu_before.jpg` … 差し替え前（2分割）。戻すときの素材
- `build_richmenu.py` … before画像から現在の画像を組み直すスクリプト。
  イラストと文字を切り出して上段に詰め直し、下段に帯を足す

## 現在のメニュー

| 位置 | ラベル | 動作 |
| --- | --- | --- |
| 上段左 | サービスに登録する | https://liff.line.me/2010124948-mdICjM2b |
| 上段右 | 投稿へのリクエスト | postback `投稿へのリクエスト` |
| 下段 | とうこさんって何？ | https://saas.shikisai.work/toukosan |

- 現在のID：`richmenu-1c90daf7ec0732367bc59813b99c9103`（2026-10-22 設定）
- 旧ID：`richmenu-bace607a4bdd8c9b8642eb227e648589`（2ボタン版。消していないので戻せる）

## 差し替え手順

チャネルトークンは line-crm D1 の `line_accounts`（id `cb6e376edf2317a73ef502d24fb1ead2`）にある。

1. `POST /v2/bot/richmenu` にメニュー定義（size・areas）を送ってIDを受け取る
2. `POST https://api-data.line.me/v2/bot/richmenu/{id}/content` に画像を送る（JPEG/PNG・**1MB以下**・2500×1686）
3. `POST /v2/bot/user/all/richmenu/{id}` でデフォルトに設定
4. 旧メニューは消さずに残す（戻すときは手順3を旧IDで実行）
