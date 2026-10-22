# サロンAI塾 LINE公式アカウント リッチメニュー

LINE公式アカウント「サロンAI塾」（@497tgbqa・友だち追加 https://lin.ee/NT624Z0k ）のリッチメニュー。

- `salon-ai-school_richmenu_2026-10-22.png` … 現在のメニュー画像（2500×843・左右2分割）。ChatGPTで生成し、2500×843にリサイズしたもの
- `set_salon_ai_school_richmenu.py` … 作成・画像アップロード・デフォルト設定を行うスクリプト
- `set_salon_ai_school_richmenu_swapped.py` … 左右入替版を設定したスクリプト

## 現在のメニュー

| 位置 | ラベル | リンク |
| --- | --- | --- |
| 左 | サロンAI塾とは | https://saas.shikisai.work/salon-ai-school |
| 右 | 申し込み | https://rocky-ash-f11.notion.site/AI-3dee55d89092814b9e50c4f099d6d01d |

- 現在のID：`richmenu-323cf453dc6683888b66bcf9def3dcbb`（2026-10-22 14:34 設定・左右入替版）
- 旧ID：`richmenu-0a861428d2bcb839574a9ea75a346f46`（左が申し込み。消していないので戻せる）
- 画像は `..._swapped.png`。元画像を左右の半分ごと入れ替えただけで、文字は描き直していない

## 認証

Keychain `line-salonaijuku`（`channel_id` / `channel_secret` / `token`）。
トークンは30日で切れるので、切れたら `line_token.py` と同じ方法（`POST https://api.line.me/v2/oauth/accessToken` の client_credentials）で再発行する。ID・シークレットはKeychainにある。

## 差し替え手順

1. `POST /v2/bot/richmenu` にメニュー定義（size・areas）を送ってIDを受け取る
2. `POST https://api-data.line.me/v2/bot/richmenu/{id}/content` に画像を送る（JPEG・**1MB以下**・2500×843）
3. `POST /v2/bot/user/all/richmenu/{id}` でデフォルトに設定
4. 旧メニューは消さずに残す（戻すときは手順3を旧IDで実行）
