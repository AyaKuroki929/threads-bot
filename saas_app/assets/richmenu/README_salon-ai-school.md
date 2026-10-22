# サロンAI塾 LINE公式アカウント リッチメニュー

LINE公式アカウント「サロンAI塾」（@497tgbqa・友だち追加 https://lin.ee/NT624Z0k ）のリッチメニュー。

- `salon-ai-school_richmenu_2026-10-22.png` … 現在のメニュー画像（2500×843・左右2分割）。ChatGPTで生成し、2500×843にリサイズしたもの
- `set_salon_ai_school_richmenu.py` … 作成・画像アップロード・デフォルト設定を行うスクリプト

## 現在のメニュー

| 位置 | ラベル | リンク |
| --- | --- | --- |
| 左 | 申し込み | https://rocky-ash-f11.notion.site/AI-3dee55d89092814b9e50c4f099d6d01d |
| 右 | サロンAI塾とは | https://saas.shikisai.work/salon-ai-school |

- 現在のID：`richmenu-0a861428d2bcb839574a9ea75a346f46`（2026-10-22 設定）

## 認証

Keychain `line-salonaijuku`（`channel_id` / `channel_secret` / `token`）。
トークンは30日で切れるので、切れたら `line_token.py` と同じ方法（`POST https://api.line.me/v2/oauth/accessToken` の client_credentials）で再発行する。ID・シークレットはKeychainにある。

## 差し替え手順

1. `POST /v2/bot/richmenu` にメニュー定義（size・areas）を送ってIDを受け取る
2. `POST https://api-data.line.me/v2/bot/richmenu/{id}/content` に画像を送る（JPEG・**1MB以下**・2500×843）
3. `POST /v2/bot/user/all/richmenu/{id}` でデフォルトに設定
4. 旧メニューは消さずに残す（戻すときは手順3を旧IDで実行）
