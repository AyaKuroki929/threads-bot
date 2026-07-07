#!/bin/bash
# Threads Cookie 自動更新スクリプト（2アカウント対応）
# - Profile 4 → bemolle_diet → THREADS_SESSION
# - Profile 3 → aya_kuroki_0929 → THREADS_SESSION_PERSONAL

set -uo pipefail
cd "$(dirname "$0")"

LOG_FILE="cookie_refresh.log"
REPO="AyaKuroki929/threads-bot"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
LAST_RUN_FILE=".cookie_refresh_last_run"
REFRESH_INTERVAL_HOURS=20  # 20時間以内に更新済みならスキップ（実質1日1回）

log() { echo "[$TIMESTAMP] $*" >> "$LOG_FILE"; }
log "==== cookie refresh start ===="

# 20時間以内に更新済みならスキップ（launchd の複数スケジュールによる重複実行防止）
if [ -f "$LAST_RUN_FILE" ]; then
    last_run=$(cat "$LAST_RUN_FILE")
    now=$(date +%s)
    elapsed=$(( (now - last_run) / 3600 ))
    if [ "$elapsed" -lt "$REFRESH_INTERVAL_HOURS" ]; then
        log "SKIP: 前回更新から${elapsed}時間（${REFRESH_INTERVAL_HOURS}時間未満）。スキップします。"
        exit 0
    fi
fi

fail_with_issue() {
    local title="$1"
    local body="$2"
    log "FAIL: $title"
    log "$body"
    local existing
    existing=$(/opt/homebrew/bin/gh issue list --repo "$REPO" --state open --search "$title in:title" --json number --jq '.[0].number' 2>/dev/null || echo "")
    if [ -z "$existing" ]; then
        /opt/homebrew/bin/gh issue create \
            --repo "$REPO" \
            --title "$title" \
            --label "cookie-expired" \
            --body "$body" >> "$LOG_FILE" 2>&1 || log "Issue作成失敗"
    else
        log "既存 Issue #$existing が open のため skip"
    fi
}

refresh_account() {
    local account="$1"      # bemolle_diet or aya_kuroki_0929
    local profile="$2"      # 3 or 1
    local secret="$3"       # THREADS_SESSION or THREADS_SESSION_PERSONAL
    local session_file="$4" # session.json or session_personal.json
    local min_cookies="$5"  # minimum expected cookie count
    local expected_uid="$6" # 期待するds_user_id（アカウント取り違え防止）

    log "--- [$account] 更新開始 (Profile $profile) ---"

    # バックアップ
    cp "$session_file" "${session_file}.bak.$(date +%s)" 2>/dev/null || true

    # Cookie抽出（extract_cookies2.py は常に session.json に書き出す）
    # 個人アカ処理の前に bemolle の session.json を退避しておく。放置すると
    # 実行後の session.json が常に個人Cookieになり、ローカル手動実行
    # (post_local_bemolle.sh 等 SESSION_FILE=session.json) が個人アカから
    # 発信される取り違え事故の温床になるため、処理後に必ず復元する。
    if [ "$session_file" != "session.json" ]; then
        cp session.json session.json.bemolle_keep 2>/dev/null || true
    fi
    if ! /usr/bin/python3 extract_cookies2.py --profile "$profile" >> "$LOG_FILE" 2>&1; then
        fail_with_issue \
            "Cookie自動更新失敗[$account] - 抽出エラー" \
            "extract_cookies2.py --profile $profile が失敗。Chrome Profile $profile または Keychain アクセスを確認してください。"
        [ -f session.json.bemolle_keep ] && mv session.json.bemolle_keep session.json
        return 1
    fi

    # bemolle以外は session_personal.json にコピーし、session.json を bemolle に復元
    if [ "$session_file" != "session.json" ]; then
        cp session.json "$session_file"
        if [ -f session.json.bemolle_keep ]; then
            mv session.json.bemolle_keep session.json
            log "session.json を bemolle 用に復元（取り違え防止）"
        fi
    fi

    # ユーザーIDチェック（アカウント取り違え防止）
    actual_uid=$(/usr/bin/python3 -c "
import json
cookies = json.load(open('$session_file'))['cookies']
uid = next((c['value'] for c in cookies if c['name'] == 'ds_user_id' and 'threads' in c.get('domain','')), '')
print(uid)
" 2>/dev/null || echo "")
    if [ "$actual_uid" != "$expected_uid" ]; then
        fail_with_issue \
            "Cookie自動更新失敗[$account] - アカウント取り違え検出" \
            "@$account: 期待UID=${expected_uid}, 実際のUID=${actual_uid}。Chrome Profile $profile に別アカウントがログインしています。確認してください。"
        log "CRITICAL: アカウント取り違え。Secretは更新しません。"
        return 1
    fi
    log "UID確認OK: $actual_uid = @$account"

    # 抽出件数チェック
    cookie_count=$(/usr/bin/python3 -c "import json; print(len(json.load(open('$session_file'))['cookies']))" 2>/dev/null || echo "0")
    if [ "$cookie_count" -lt "$min_cookies" ]; then
        fail_with_issue \
            "Cookie自動更新失敗[$account] - 抽出件数異常 (${cookie_count}件)" \
            "@$account: 抽出件数が${cookie_count}件（期待: ${min_cookies}件以上）。Chrome Profile $profile で再ログインが必要な可能性。"
        return 1
    fi

    # ローカル疎通テスト
    verify_output=$(/usr/bin/python3 verify_login.py --session "$session_file" 2>&1)
    echo "$verify_output" >> "$LOG_FILE"
    if ! echo "$verify_output" | grep -q "ログイン状態を確認"; then
        fail_with_issue \
            "Cookie自動更新失敗[$account] - ログイン無効" \
            "@$account: 新規抽出した Cookie でログインできませんでした。Chrome Profile $profile で再ログインしてください。"
        return 1
    fi

    # GitHub Secret 更新
    if ! /opt/homebrew/bin/gh secret set "$secret" --repo "$REPO" < "$session_file" >> "$LOG_FILE" 2>&1; then
        fail_with_issue \
            "Cookie自動更新失敗[$account] - Secret更新エラー" \
            "@$account: gh secret set $secret が失敗しました。\`gh auth status\` で GitHub 認証を確認してください。"
        return 1
    fi

    log "SUCCESS[$account]: Cookie更新完了 (${cookie_count}件)"

    # 成功時：関連する open Issue があればクローズ（次回失敗時のLINE通知を復活させる）
    for label in "cookie-expired" "account-mismatch-personal" "cookie-expired-personal"; do
        open_issue=$(/opt/homebrew/bin/gh issue list --repo "$REPO" --state open --label "$label" --json number --jq '.[0].number' 2>/dev/null || echo "")
        if [ -n "$open_issue" ]; then
            /opt/homebrew/bin/gh issue close "$open_issue" --repo "$REPO" \
                --comment "[$account] Cookie自動更新成功。Issue自動クローズ。" >> "$LOG_FILE" 2>&1 && \
                log "Issue #$open_issue を自動クローズ"
        fi
    done

    return 0
}

# bemolle_diet: Profile 4, min 5 cookies, UID=73523451930
refresh_account "bemolle_diet" "4" "THREADS_SESSION" "session.json" "5" "73523451930"

# aya_kuroki_0929: Profile 3, min 10 cookies, UID=63084943935
refresh_account "aya_kuroki_0929" "3" "THREADS_SESSION_PERSONAL" "session_personal.json" "10" "63084943935"

# 最終実行時刻を記録
date +%s > "$LAST_RUN_FILE"

# 深夜実行（4:00 AM）の場合はスクリプト完了後にスリープに戻す
CURRENT_HOUR=$(date +%-H)
if [ "$CURRENT_HOUR" -lt 6 ]; then
    log "深夜実行のため5分後にスリープします"
    (sleep 300 && pmset sleepnow) &
fi

# 古いバックアップ削除（7日以上前）
find . -maxdepth 1 -name "session*.json.bak.*" -mtime +7 -delete 2>/dev/null || true

# ログ切り詰め
if [ "$(wc -l < "$LOG_FILE")" -gt 5000 ]; then
    tail -2000 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
fi

exit 0
