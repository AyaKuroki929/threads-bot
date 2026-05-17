#!/bin/bash
# 個人アカウント Threads 定期投稿（Mac LaunchAgent 用・GH Actions不要）
# Usage: post_local_personal.sh morning|noon|evening

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd /Users/ayakuroki/threads_bot

SLOT="$1"
LOG=/Users/ayakuroki/threads_bot/post_local_personal.log

echo "=== $(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S JST') $SLOT START ===" >> "$LOG"

export TZ=Asia/Tokyo
export POSTS_FILE=posts_personal.json
export SESSION_FILE=session_personal.json
export USED_FILE=used_posts_personal.json
export LAST_RUN_FILE=last_run_personal.json
export PRIORITY_FILE=priority_posts_personal.json
export COMMENT_TARGETS_FILE=comment_targets_personal.json
export COMMENTED_FILE=commented_posts_personal.json
export THREADS_TOPIC=ビジネス＆起業家
export AUTO_COMMENT=1
export MAX_COMMENTS_PER_RUN=3
export COMMENT_MIN_POOL=5
export COMMENT_KEYWORDS_FILE=comment_search_keywords_personal.json
export EXPECTED_USER_ID=63084943935
export USERNAME=aya_kuroki_0929

python3 post.py "$SLOT" >> "$LOG" 2>&1
ec=$?

echo "[$(TZ=Asia/Tokyo date '+%H:%M:%S')] exit_code=$ec" >> "$LOG"
exit $ec
