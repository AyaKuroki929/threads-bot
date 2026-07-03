#!/usr/bin/env python3
"""Threads 自動いいねスクリプト（bemolle / personal 両対応）

ターゲットリスト（comment_targets.json）から投稿を探していいねする。
いいね済みは liked_posts.json に記録して重複防止。
残り50件以下になったらキーワード検索で新規アカウントを自動補充。
"""
import os, sys, json, time, random
from datetime import datetime, timedelta

from botlib import line_broadcast, load_json as _botlib_load, save_json as _botlib_save

_BASE = os.path.dirname(os.path.abspath(__file__))


def _line_alert(text: str):
    """いいね機能の重要トラブル時のみ管理者LINEに通知（Claude通知Bot経由）。
    コメント/suggest系の通知は停止中でも、この関数は動作する。
    実装は botlib.line_broadcast（ADMIN_NOTIFY_LINE_TOKEN優先で解決）。"""
    line_broadcast(text)

ACCOUNT = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("bemolle", "personal") else "bemolle"

if ACCOUNT == "personal":
    SESSION_ENV   = "THREADS_SESSION_PERSONAL"
    SESSION_FILE  = os.path.join(_BASE, "session_personal.json")
    TARGETS_FILE  = os.path.join(_BASE, "comment_targets_personal.json")
    LIKED_FILE    = os.path.join(_BASE, "liked_posts_personal.json")
    KEYWORDS_FILE = os.path.join(_BASE, "comment_search_keywords_personal.json")
    USERNAME      = "aya_kuroki_0929"
    LABEL         = "個人"
else:
    SESSION_ENV   = "THREADS_SESSION"
    SESSION_FILE  = os.path.join(_BASE, "session.json")
    TARGETS_FILE  = os.path.join(_BASE, "comment_targets.json")
    LIKED_FILE    = os.path.join(_BASE, "liked_posts.json")
    KEYWORDS_FILE = os.path.join(_BASE, "comment_search_keywords.json")
    USERNAME      = "bemolle_diet"
    LABEL         = "ベモーレ"

# ── 人間らしいペーシング（2026-07-03 予防強化）─────────────────
# 規則的なボット挙動（毎日同時刻・毎回同件数・数秒間隔の連続処理）は
# フラグの引き金になり得るため、量・間隔・タイミングを毎回ゆらす。
# ※注意: これは「新規フラグの予防」。既に付いたフラグは休養でしか解けない
#  （2026-06に住宅IP・stealth等を全て試して実証済み → RESTRICTION.md）
LIKES_MIN_PER_RUN  = 2    # 1回あたりのいいね件数の下限（毎回この範囲でランダム）
LIKES_PER_RUN      = 5    # 1回あたりのいいね上限
SKIP_RUN_PROB      = 0.10 # この確率で今回の実行を丸ごとサボる（人間は毎日3回×365日やらない）
START_JITTER_SEC   = (0, 300)   # 実行開始前のランダム待機（毎日きっかり同時刻を避ける）
LIKE_GAP_SEC       = (8, 40)    # いいね後〜次のアカウントへ移る間隔（従来3〜8秒→大幅拡大）
LIKE_COOLDOWN_DAYS = 30   # 同アカに再いいねしない日数
REPLENISH_THRESHOLD = 50  # ターゲット残りがこの件数以下で補充発動
DISCOVER_COUNT      = 15  # 1回の補充で追加する最大件数
MAX_FAIL            = 8   # 連続失敗でセッション異常と判断し中断
PAUSE_HOURS         = 72  # サイレント破棄検知時の自動休止時間（制限中に叩き続けてフラグを深めない）
ALERT_THROTTLE_HOURS = 24 # 同種LINE警告の再送間隔（LINE枠節約・1日3回の連続警告を防ぐ）

# 反映検証（canary）・休止・警告抑制の状態ファイル（アカウント別・workflowがcommitして永続化）
_SUFFIX = "_personal" if ACCOUNT == "personal" else ""
VERIFY_FILE = os.path.join(_BASE, f"like_verify_queue{_SUFFIX}.json")
PAUSE_FILE  = os.path.join(_BASE, f"like_pause{_SUFFIX}.json")
ALERT_FILE  = os.path.join(_BASE, f"like_alert_state{_SUFFIX}.json")


def _load_liked():
    return _botlib_load(LIKED_FILE, {})


def _save_liked(data):
    _botlib_save(LIKED_FILE, data)


def _already_liked(liked, account):
    ts = liked.get(account)
    if not ts:
        return False
    try:
        return (datetime.utcnow() - datetime.fromisoformat(ts)).days < LIKE_COOLDOWN_DAYS
    except Exception:
        return False


def _load_targets():
    return _botlib_load(TARGETS_FILE, [])


def _save_targets(data):
    _botlib_save(TARGETS_FILE, data)


# 汎用JSON読み書きは botlib に集約（既存呼び出し名は維持）
_load_json = _botlib_load
_save_json = _botlib_save


# ── 自動休止（制限検知時にcronが叩き続けてフラグを深めないため） ──
def _check_pause() -> bool:
    """休止中ならTrue。期限が過ぎていたら休止を解除して再開。"""
    if not os.path.exists(PAUSE_FILE):
        return False
    try:
        d = _load_json(PAUSE_FILE, {})
        until = datetime.fromisoformat(d.get("until", "1970-01-01T00:00:00"))
        if datetime.utcnow() < until:
            print(f"[like] {LABEL} 自動休止中（理由: {d.get('reason','')}・{until.isoformat()}UTCまで）→ スキップ")
            return True
    except Exception:
        pass  # 壊れた休止ファイルは解除扱い
    os.remove(PAUSE_FILE)
    print(f"[like] {LABEL} 休止期間終了 → 再開（今回の実行で反映検証から再テスト）")
    return False


def _set_pause(reason: str, hours: int = PAUSE_HOURS):
    _save_json(PAUSE_FILE, {
        "until": (datetime.utcnow() + timedelta(hours=hours)).isoformat(),
        "reason": reason,
        "set_at": datetime.utcnow().isoformat(),
    })


def _escalated_pause_hours() -> int:
    """破棄検知が繰り返された回数に応じて休止時間を自動延長（72h→144h→288h…最大14日）。
    反映確認が取れたら _reset_discard_count() でリセットされる。"""
    state = _load_json(ALERT_FILE, {})
    count = int(state.get("discard_count", 0)) + 1
    state["discard_count"] = count
    _save_json(ALERT_FILE, state)
    return min(PAUSE_HOURS * (2 ** (count - 1)), 14 * 24)


def _reset_discard_count():
    state = _load_json(ALERT_FILE, {})
    if state.get("discard_count"):
        state["discard_count"] = 0
        _save_json(ALERT_FILE, state)
        print(f"[verify] {LABEL} 反映確認が取れたため破棄カウントをリセット")


def _alert_throttled(kind: str, text: str):
    """同種の警告を ALERT_THROTTLE_HOURS に1回だけLINE送信（毎cron連発を防ぐ）。"""
    state = _load_json(ALERT_FILE, {})
    try:
        last = datetime.fromisoformat(state.get(kind, "1970-01-01T00:00:00"))
        if datetime.utcnow() - last < timedelta(hours=ALERT_THROTTLE_HOURS):
            print(f"[like_alert] {kind} は{ALERT_THROTTLE_HOURS}h以内に通知済み → 抑制")
            return
    except Exception:
        pass
    _line_alert(text)
    state[kind] = datetime.utcnow().isoformat()
    _save_json(ALERT_FILE, state)


# ── ログイン状態確認 ──────────────────────────────────────────
def _is_logged_in(page):
    """threads.comトップでログイン状態を確認する。
    True=ログイン中 / False=未ログイン / None=判定不能。
    セッション切れだと『クリックは成功に見えるが未ログインで無効』という
    偽いいねが発生し、反映検証でも「破棄」と誤診してしまうため、
    行動する前に必ず確認する（cookie_refresh と同じ Create ボタン判定）。"""
    try:
        page.goto("https://www.threads.com/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
        if page.locator('[aria-label*="Create"], [aria-label*="作成"], [aria-label*="新規スレッド"]').count() > 0:
            return True
        if page.locator('a[href*="/login"]').count() > 0:
            return False
        return None
    except Exception as e:
        print(f"[login] {LABEL} 確認失敗（判定不能）: {e}", file=sys.stderr)
        return None


# ── 反映検証（canary）──────────────────────────────────────────
def _verify_previous_likes(page) -> tuple[int, int]:
    """前回いいねした投稿を再訪問し、いいね状態が残っているか確認する。
    クリック成功＝成功ではない：Metaのアクション制限は「操作は通るがサーバー側で
    サイレント破棄」する（2026-06のbemolleコメント制限で実証済み・偽OK問題）。
    returns: (判定できた件数, 反映が確認できた件数)"""
    queue = _load_json(VERIFY_FILE, [])
    if not queue:
        return 0, 0
    checked = reflected = 0
    for item in queue[:4]:
        url = item.get("post_url") or ""
        if not url:
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(random.randint(2000, 3500))
            btns = page.locator('[aria-label*="Like"], [aria-label*="いいね"], [aria-label*="like"], [aria-label*="取り消"]')
            if btns.count() == 0:
                print(f"[verify] @{item.get('account','')} ボタン見つからず（投稿削除等・判定不能）")
                continue
            label = (btns.first.get_attribute("aria-label") or "")
            if "unlike" in label.lower() or "取り消" in label:
                reflected += 1
            else:
                print(f"[verify] ⚠️ いいね未反映: @{item.get('account','')} {url}")
            checked += 1
        except Exception as e:
            print(f"[verify] @{item.get('account','')} 確認失敗（判定不能）: {e}")
    _save_json(VERIFY_FILE, [])  # 検証済みキューはクリア
    return checked, reflected


def _try_like(page, acc):
    """プロフィールページの最初の投稿をいいねする。
    returns: (result, post_url)
      result: True=いいね実行 / None=既いいね済み / False=失敗
      post_url: いいねした投稿のURL（反映検証用。取得できなければ空文字）"""
    try:
        page.goto(f"https://www.threads.com/@{acc}", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2500, 4000))

        # 最初の投稿カードのいいねボタンを探す
        # Threads の like ボタンは aria-label に "Like" または "いいね" を含む
        like_btns = page.locator(
            '[aria-label*="Like"], [aria-label*="いいね"], [aria-label*="like"]'
        )
        if like_btns.count() == 0:
            print(f"[like] @{acc} いいねボタンが見つからない → スキップ")
            return False, ""

        btn = like_btns.first
        label = (btn.get_attribute("aria-label") or "").lower()

        # 既にいいね済みの場合はスキップ（aria-label に "unlike"/取り消す が含まれる）
        if "unlike" in label or "取り消" in label:
            print(f"[like] @{acc} 既にいいね済み → スキップ（記録して次へ）")
            return None, ""  # None = 既いいね（カウントせず記録だけ）

        # いいね対象＝最初の投稿のURLを反映検証用に控える
        post_url = ""
        try:
            href = page.locator('a[href*="/post/"]').first.get_attribute("href") or ""
            if href.startswith("/"):
                post_url = "https://www.threads.com" + href
            elif href:
                post_url = href
        except Exception:
            pass

        btn.scroll_into_view_if_needed()
        btn.click()
        # 人間らしさ④: いいね後の滞在をたっぷりゆらす（従来3〜8秒→8〜40秒）
        page.wait_for_timeout(random.randint(LIKE_GAP_SEC[0] * 1000, LIKE_GAP_SEC[1] * 1000))
        print(f"[like] @{acc} ❤️ いいね完了")
        return True, post_url

    except Exception as e:
        print(f"[like] @{acc} エラー: {e}")
        return False, ""


def main():
    # 自動休止中なら何もしない（制限中に叩き続けてフラグを深めない）
    if _check_pause():
        return

    # 人間らしさ①: たまに丸ごとサボる（毎日3回×365日は人間の挙動ではない）
    if random.random() < SKIP_RUN_PROB:
        print(f"[like] {LABEL} 今回はランダムスキップ（人間らしさ・{int(SKIP_RUN_PROB*100)}%の確率）")
        return

    # 人間らしさ②: 開始時刻をゆらす（cronの毎日きっかり同時刻を避ける）
    jitter = random.randint(*START_JITTER_SEC)
    print(f"[like] {LABEL} 開始ジッター {jitter}秒待機")
    time.sleep(jitter)

    # セッションファイル書き出し（GH Actions環境変数から）
    session_val = os.environ.get(SESSION_ENV, "")
    if session_val:
        with open(SESSION_FILE, "w", encoding="utf-8") as f:
            f.write(session_val)

    if not os.path.exists(SESSION_FILE):
        print(f"[like] {LABEL} セッションファイルなし → スキップ")
        return

    targets = _load_targets()
    liked   = _load_liked()

    eligible = [t for t in targets if not _already_liked(liked, t.get("account", ""))]
    random.shuffle(eligible)

    likes_target = random.randint(LIKES_MIN_PER_RUN, LIKES_PER_RUN)  # 人間らしさ③: 毎回件数を変える
    print(f"[like] {LABEL} 今回の目標: {likes_target}件")
    liked_count    = 0
    consecutive_fail = 0
    verify_queue_new = []  # 今回いいねした投稿（次回実行の冒頭で反映検証する）

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            storage_state=SESSION_FILE,
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )
        page = ctx.new_page()

        # ── ログイン確認：セッション切れでの「偽いいね」と誤診を防ぐ ──
        login = _is_logged_in(page)
        if login is False:
            print(f"[login] {LABEL} 未ログイン → いいね・検証をスキップ（偽いいね防止）")
            _alert_throttled(
                "session_dead",
                f"🚨 【{LABEL}】Threadsセッションが未ログイン状態です\n\n"
                f"いいね・反映検証を実行せずスキップしました\n"
                f"（未ログインだとクリックが無効なのに成功記録される偽いいねになるため）。\n\n"
                f"対処：playwright_login.py で {LABEL} を再ログイン\n"
                f"→ GitHub Secret {SESSION_ENV} を更新\n"
                f"（ローカルMacの cookie_refresh が毎日12時に自動更新するので、\n"
                f"　Chromeの該当プロファイルでThreadsにログインし直せば自動復帰します）"
            )
            browser.close()
            return
        if login is None:
            print(f"[login] {LABEL} ログイン判定不能 → 今回はスキップ（次回再試行）")
            _alert_throttled(
                "login_unknown",
                f"⚠️ 【{LABEL}】Threadsのログイン判定ができませんでした\n"
                f"（画面構造の変化の可能性）。自動いいねを今回スキップ。\n"
                f"連日続く場合はコードの確認が必要です。"
            )
            browser.close()
            return
        print(f"[login] {LABEL} ログイン確認OK")

        # ── 反映検証：前回いいねが実際にサーバー側に残っているか（偽OK検知） ──
        checked, reflected = _verify_previous_likes(page)
        if checked:
            print(f"[verify] {LABEL} 前回いいねの反映確認: {reflected}/{checked}")
        if reflected > 0:
            _reset_discard_count()  # 反映が確認できた＝破棄は起きていない
        if checked >= 2 and reflected == 0:
            # ログイン確認済みで2件以上全滅＝クリックは通るがサーバー側で破棄されている
            # （2026-06のコメント制限と同じアクション制限の症状）。
            # 続行はフラグを深めるだけなので自動休止（繰り返すたびに休止を自動延長）。
            hours = _escalated_pause_hours()
            print(f"[verify] {LABEL} 全滅 → サイレント破棄の疑い。{hours}時間の自動休止")
            _set_pause("いいね未反映（サイレント破棄の疑い）", hours)
            _alert_throttled(
                "silent_discard",
                f"🚨 【{LABEL}】いいねがサーバー側で反映されていません\n\n"
                f"ログイン状態は正常なのに、前回いいねした{checked}件が全て未反映。\n"
                f"Meta側でアクションが破棄されています\n"
                f"（6月のコメント制限と同じアクション制限の症状）。\n\n"
                f"自動いいねを{hours}時間休止します。\n"
                f"期間終了後に自動で再テストします（操作不要）。\n"
                f"検知のたびに休止期間は自動で延長されます（最大14日）。"
            )
            browser.close()
            _save_liked(liked)
            return

        for t in eligible:
            if liked_count >= likes_target:
                break
            acc = t.get("account", "")
            if not acc:
                continue

            result, post_url = _try_like(page, acc)
            if result is True:
                liked[acc] = datetime.utcnow().isoformat()
                liked_count += 1
                consecutive_fail = 0
                if post_url:
                    verify_queue_new.append({
                        "account": acc, "post_url": post_url,
                        "ts": datetime.utcnow().isoformat(),
                    })
            elif result is None:
                # 既いいね済み → 記録して次へ
                liked[acc] = datetime.utcnow().isoformat()
                consecutive_fail = 0
            else:
                consecutive_fail += 1
                if consecutive_fail >= MAX_FAIL:
                    print(f"[like] {LABEL} 連続{MAX_FAIL}件失敗 → セッション異常の可能性。中断")
                    _alert_throttled(
                        "session_dead",
                        f"🚨 【{LABEL}】自動いいねが停止しました\n\n"
                        f"連続{MAX_FAIL}件で「いいねボタンが見つからない」→ 中断\n"
                        f"セッション(Cookie)期限切れの可能性が高いです。\n\n"
                        f"対処：playwright_login.py で {LABEL} を再ログイン\n"
                        f"→ GitHub Secret {SESSION_ENV} を更新\n"
                        f"（更新すれば次の定時実行から自動復帰します）"
                    )
                    break

        # ターゲット補充（残りが REPLENISH_THRESHOLD 以下）
        remaining = [t for t in targets if not _already_liked(liked, t.get("account", ""))]
        if len(remaining) < REPLENISH_THRESHOLD:
            print(f"[like] {LABEL} ターゲット残り{len(remaining)}件 → 新規発掘開始")
            import post as _post
            _post.USERNAME             = USERNAME
            _post.COMMENT_KEYWORDS_FILE = KEYWORDS_FILE
            already_known = {t["account"] for t in targets}
            new_accounts = _post._discover_new_accounts(page, already_known, max_new=DISCOVER_COUNT)
            if new_accounts:
                targets.extend(new_accounts)
                _save_targets(targets)
                print(f"[like] {len(new_accounts)}件追加 → 合計{len(targets)}件")

        browser.close()

    _save_liked(liked)
    _save_json(VERIFY_FILE, verify_queue_new)  # 次回実行の冒頭で反映検証
    print(f"[like] {LABEL} 完了: {liked_count}件いいね（次回反映検証: {len(verify_queue_new)}件）")


if __name__ == "__main__":
    main()
