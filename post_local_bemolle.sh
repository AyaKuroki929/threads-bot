#!/bin/bash
# ベモーレ Threads 定期投稿（Mac LaunchAgent 用・GH Actions不要）
# Usage: post_local_bemolle.sh morning|noon|evening

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd /Users/ayakuroki/threads_bot

SLOT="$1"
LOG=/Users/ayakuroki/threads_bot/post_local_bemolle.log

echo "=== $(TZ=Asia/Tokyo date '+%Y-%m-%d %H:%M:%S JST') $SLOT START ===" >> "$LOG"

export TZ=Asia/Tokyo
export POSTS_FILE=posts.json
export SESSION_FILE=session.json
export USED_FILE=used_posts.json
export LAST_RUN_FILE=last_run.json
export PRIORITY_FILE=priority_posts.json
export COMMENT_TARGETS_FILE=comment_targets.json
export COMMENTED_FILE=commented_posts.json
export THREADS_TOPIC=美容
export AUTO_COMMENT=1
export MAX_COMMENTS_PER_RUN=3
export COMMENT_MIN_POOL=5
export COMMENT_KEYWORDS_FILE=comment_search_keywords.json
export EXPECTED_USER_ID=73523451930
export USERNAME=bemolle_diet

python3 post.py "$SLOT" >> "$LOG" 2>&1
ec=$?

echo "[$(TZ=Asia/Tokyo date '+%H:%M:%S')] exit_code=$ec" >> "$LOG"
exit $ec
