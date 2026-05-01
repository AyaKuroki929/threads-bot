#!/bin/bash
# Mac起動/ログイン時に呼ばれ、前回から7日以上経っていれば補充を実行する

set -e
export PATH="/Applications/cmux.app/Contents/Resources/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd /Users/ayakuroki/threads_bot

LOG=/Users/ayakuroki/threads_bot/regen.log
STAMP=/Users/ayakuroki/threads_bot/.regen_last_run

# 前回実行から1日未満ならスキップ（在庫切れリスク回避のため発火頻度UP）
# 旧: 3日 → 新: 1日（補充閾値も5本→10本に緩和）
if [ -f "$STAMP" ]; then
  last=$(stat -f %m "$STAMP")
  now=$(date +%s)
  diff_days=$(( (now - last) / 86400 ))
  if [ "$diff_days" -lt 1 ]; then
    echo "=== $(date) skip (last run ${diff_days}d ago) ===" >> "$LOG"
    exit 0
  fi
fi

echo "=== $(date) regen start ===" >> "$LOG"

PROMPT='あなたはベモーレサロンのSNS運用担当です。以下を実行してください。

# 必ず参照する3つのソース（順番厳守）

## 1. /Users/ayakuroki/threads_bot/GENERATE_RULES.md
トーン・記号・物語型／保存型／フックルール・年齢取扱い・記号「…」(1個)の制約等を把握。

## 2. /Users/ayakuroki/threads_bot/story_bank.md（最重要・真実の台帳）
**黒木さんの実体験・台詞・数字・お客様の言葉・コアメッセージはすべてここにある**。
**ここに書かれていない事実・エピソードは絶対に創作しない**。
新規エピソードを作るのではなく、既存事実を「違う型・違う角度・違う長さ」で語り直す。

## 3. Notion Bemolleマニュアル（Threadsトーンのキャリブレ用）
- page_id: `2c3e55d8-9092-8076-b7de-d266515f70fd`
- mcp__notion__API-get-block-children などで参照可
- 「Bemolleの在り方」「医者のスタンス」「提案・販売の在り方」セクションを特に確認
- スタッフ向け業務マニュアル部分（身だしなみ・制服など）はThreads発信には**含めない**
- 一次資料として「お客様への在り方」だけを発信に活かす

# やること

1. /Users/ayakuroki/threads_bot/posts.json と /Users/ayakuroki/threads_bot/used_posts.json を読む
2. 各スロット（morning, noon, evening）の未使用本数を計算
3. 未使用が**10本以下**のスロットには、新たに**10本**を生成してposts.jsonに追記（在庫切れリスク回避のため余裕度高め）
   - morning / evening は文字列、noon は3要素の配列（3部ツリー）
   - **story_bank.mdの実体験のみを使う**。新規エピソード創作は絶対NG
   - 同じ事実を「違う型」で語り直すアプローチ：
     * 物語型（時系列narrative）
     * 保存型リスト（黒木の習慣／結果が出るお客様の共通点／自己流の落とし穴等）
     * 質問投げかけ型（短文・morning向け）
     * 数字フック型（「60→47kg。1年でやったこと」）
     * 逆説型（「『痩せるのは簡単』と言われた話」）
     * 比較型（「20代と40代の決定的な違い」）
     * お客様反証型（「『どうせ戻る』に3年で答えた話」）
   - **既存posts.jsonの全投稿（過去使用済み含む全件）と訴求軸が被らないよう注意**
   - GENERATE_RULES.mdの「物語型」「保存型」「エンゲージメント誘発フレーズ」「記号ルール（…1個）」を厳守
   - 全投稿の30〜40%にコメント誘発／保存促進のCTAを末尾に入れる
4. used_posts.json は変更しない
5. 完了後、標準出力に「morning +N / noon +N / evening +N」の1行サマリのみを出力

# 重要な禁則
- story_bank.mdに無い数字・台詞・出来事を作らない
- 黒木さん本人を「40代」と書かない（30代後半）
- 「うち」NG、「ベモーレ」「当サロン」OK
- 3点リーダーは「…」（1個）、「……」NG
- 「あなた専用」「あなたに合わせて組む」NG（カスタムメニュー誤解禁止）
- 薬剤名（マンジャロ等）は直接出さず「去年すごく流行った薬や注射で短期間で落とす方法」と一般化

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
