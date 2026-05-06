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

# 必ず参照する2つのソース

## 1. /Users/ayakuroki/threads_bot/GENERATE_RULES_personal.md
トーン・3本柱・実体験ソース・文体ルール等を把握。

## 2. Notion ネタ帳（最優先）
黒木さんがメモした最新ネタがあればそれを優先使用。
- page_id: `358e55d8-9092-81b6-8842-e7fbaadf2381`
- mcp__notion__API-get-block-children で参照
- ネタがない場合はGENERATE_RULES_personal.mdの「実体験ソース」から生成

# やること

1. /Users/ayakuroki/threads_bot/posts_personal.json と /Users/ayakuroki/threads_bot/used_posts_personal.json を読む
2. morning と evening の未使用本数を計算（noon は使用しないため無視）
3. 未使用が**10本以下**のスロットには、新たに**10本**を生成してposts_personal.jsonに追記
   - morning / evening ともに文字列の単発投稿（noonは生成しない）
   - GENERATE_RULES_personal.mdの3本柱の配分（やってみた4:あるある3:裏側公開2:うらかたさん1）を守る
   - 全体の30〜40%の末尾にエンゲージメント誘発フレーズを入れる
   - 既存posts_personal.jsonの全投稿と訴求軸が被らないよう注意
4. used_posts_personal.json は変更しない
5. 完了後、標準出力に「morning +N / evening +N」の1行サマリのみを出力

# 重要な禁則
- GENERATE_RULES_personal.mdの「実体験ソース」に無いエピソードを創作しない
- Notionネタ帳にメモがあればそれを最優先で使う
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
