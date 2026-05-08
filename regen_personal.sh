#!/bin/bash
# 個人アカウント（@aya_kuroki_0929）用 投稿ネタ補充スクリプト
# Mac起動/ログイン時に呼ばれ、前回から1日以上経っていれば補充を実行する

set -e
export PATH="/Applications/cmux.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd /Users/ayakuroki/threads_bot

LOG=/Users/ayakuroki/threads_bot/regen_personal.log
STAMP=/Users/ayakuroki/threads_bot/.regen_personal_last_run

# 前回実行から1日未満ならスキップ
if [ -f "$STAMP" ]; then
  last=$(stat -f %m "$STAMP")
  now=$(date +%s)
  diff_days=$(( (now - last) / 86400 ))
  if [ "$diff_days" -lt 1 ]; then
    echo "=== $(date) skip (last run ${diff_days}d ago) ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date) regen_personal start ===" >> "$LOG"

PROMPT='あなたは黒木彩さん（@aya_kuroki_0929）の個人Threads投稿を担当しています。以下を実行してください。

# 参照するソース（この順番で必ず読む）

## 1. GENERATE_RULES_personal.md（ルール全文）
/Users/ayakuroki/threads_bot/GENERATE_RULES_personal.md を読む。
投稿哲学・トーン・3本柱・文体ルール・禁則を全て把握する。

## 2. Cloudレポート（事実の倉庫）
黒木さんが実際に作ったもの・やってきた全プロジェクトが記録されている。
- page_id: `351e55d8-9092-8051-88ff-d2c7930d92ec`
- mcp__notion__API-get-block-children で参照（複数ページ取得が必要）
- ここから「どんな経験をしてきた人か」を把握する素材として使う

## 3. ネタ帳（感情・反応・気づき）
Cloudレポートに載らない「そのとき何を感じたか・何に気づいたか」が書いてある。
- page_id: `358e55d8-9092-81b6-8842-e7fbaadf2381`
- mcp__notion__API-get-block-children で参照
- 事実に「人間の温度」を加える素材として使う

# やること

1. /Users/ayakuroki/threads_bot/posts_personal.json と /Users/ayakuroki/threads_bot/used_posts_personal.json を読む
2. morning と evening の未使用本数を計算（noon は使用しないため無視）
3. 未使用が**10本以下**のスロットには、新たに**10本**を生成してposts_personal.jsonに追記
   - morning / evening ともに2要素の配列（2部ツリー）。noonは生成しない
   - 1部目：フック＋本文（100〜200文字）、2部目：気づき・結果・問いかけ（80〜150文字）
   - GENERATE_RULES_personal.mdの3本柱の配分（やってみた4:あるある3:裏側公開2:うらかたさん1）を守る
   - 全体の30〜40%の末尾にエンゲージメント誘発フレーズを入れる
   - 既存posts_personal.jsonの全投稿と訴求軸が被らないよう注意
4. used_posts_personal.json は変更しない
5. 完了後、標準出力に「morning +N / evening +N」の1行サマリのみを出力

# 重要な禁則
- Cloudレポートやネタ帳に無いエピソードを創作しない
- 「何をしたか」をそのまま書かない。「どんな人か・どんな感覚で生きてるか」が伝わるように変換する
- 機械的な投稿・やったことを列挙するだけの投稿・説明口調の投稿は作らない
- 形式ワード禁止・AI臭排除
- 3点リーダーは「…」（1個）、「……」NG
- タメ口禁止

ファイル編集にはEdit/Writeツールを使い、確認・承認を求めず自動で完了させてください。'

# 最新ファイルをクラウドから取得
git pull --quiet --rebase >> "$LOG" 2>&1 || echo "[warn] git pull failed, continue with local" >> "$LOG"

/Applications/cmux.app/Contents/Resources/bin/claude \
  -p "$PROMPT" \
  --permission-mode bypassPermissions \
  >> "$LOG" 2>&1

touch "$STAMP"

# 補充結果をクラウドへpush
if ! git diff --quiet posts_personal.json; then
  git add posts_personal.json
  git commit -m "regen_personal: posts_personal.json refilled $(date '+%Y-%m-%d %H:%M JST')" >> "$LOG" 2>&1
  git push --quiet >> "$LOG" 2>&1 && echo "[ok] regen_personal pushed to origin" >> "$LOG"
else
  echo "[info] posts_personal.json no change" >> "$LOG"
fi

echo "=== $(date) regen_personal end ===" >> "$LOG"
