#!/bin/bash
# Mac起動/ログイン時に呼ばれ、前回から7日以上経っていれば補充を実行する

set -e
export PATH="/Applications/cmux.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd /Users/ayakuroki/threads_bot

LOG=/Users/ayakuroki/threads_bot/regen.log
STAMP=/Users/ayakuroki/threads_bot/.regen_last_run

# 前回実行から3日未満ならスキップ（被り絶対回避のため余裕度UP）
if [ -f "$STAMP" ]; then
  last=$(stat -f %m "$STAMP")
  now=$(date +%s)
  diff_days=$(( (now - last) / 86400 ))
  if [ "$diff_days" -lt 3 ]; then
    echo "=== $(date) skip (last run ${diff_days}d ago) ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date) regen start ===" >> "$LOG"

PROMPT='あなたはベモーレサロンのSNS運用担当です。以下を実行してください。

1. /Users/ayakuroki/threads_bot/GENERATE_RULES.md を読んでトーン・内容のルールを把握する
2. /Users/ayakuroki/threads_bot/posts.json と /Users/ayakuroki/threads_bot/used_posts.json を読む
3. 各スロット（morning, noon, evening）について、未使用本数（posts[slot]の総数 - used_posts[slot].length）を計算する
4. 未使用が**5本以下**のスロットには、GENERATE_RULES.mdに従って**新たに10本**を生成してposts.jsonに追記する（重要：過去ネタとの被り絶対回避のため余裕を持って補充）
   - morning / evening は文字列
   - noon は3要素の配列（3部ツリー）
   - **既存posts.jsonの全投稿（過去使用済みも含む全件）と内容・切り口・表現が被らないよう注意深く書く**。表面的な言い換えではなく、訴求軸そのものを変える
   - 黒木さんのストーリー（13kg減・肌荒れ克服・運が良くなるサロン）を必ず盛り込む
5. used_posts.json は変更しない（一度使ったidxは永久保持・絶対リセットしない）
6. 完了後、標準出力に「morning +N / noon +N / evening +N」の1行サマリのみを出力する（追加しなかったスロットは +0）

ファイル編集にはEdit/Writeツールを使い、確認・承認を求めず自動で完了させてください。'

# 最新の posts.json / used_posts.json をクラウドから取得（クラウド側が直近の真実）
git pull --quiet --rebase >> "$LOG" 2>&1 || echo "[warn] git pull failed, continue with local" >> "$LOG"

/Applications/cmux.app/Contents/Resources/bin/claude \
  -p "$PROMPT" \
  --permission-mode bypassPermissions \
  >> "$LOG" 2>&1

touch "$STAMP"

# 補充結果（posts.json）をクラウドへ push。これでクラウド実行時に最新ネタが反映される
if ! git diff --quiet posts.json; then
  git add posts.json
  git commit -m "regen: posts.json refilled $(date '+%Y-%m-%d %H:%M JST')" >> "$LOG" 2>&1
  git push --quiet >> "$LOG" 2>&1 && echo "[ok] regen pushed to origin" >> "$LOG"
else
  echo "[info] posts.json no change" >> "$LOG"
fi

echo "=== $(date) regen end ===" >> "$LOG"
