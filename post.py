"""
Threadsに自動投稿するスクリプト（self-reply chain でツリー投稿）
使い方: python3 post.py morning  /  python3 post.py noon  /  python3 post.py evening
"""
import sys
import json
import random
import os
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

POSTS_FILE = os.path.join(os.path.dirname(__file__), "posts.json")
SESSION_FILE = os.path.join(os.path.dirname(__file__), "session.json")
USED_FILE = os.path.join(os.path.dirname(__file__), "used_posts.json")
LAST_RUN_FILE = os.path.join(os.path.dirname(__file__), "last_run.json")
USERNAME = "bemolle_diet"

# 投稿失敗時の終了コード
EXIT_COOKIE_EXPIRED = 3   # Threadsログイン切れ → workflow側で専用Issue作成
EXIT_GENERIC_FAIL = 2


class CookieExpiredError(Exception):
    """Threadsのcookieが切れている時に送出。"""
    pass


def _detect_login_required(page):
    """ログイン画面に飛ばされていないかチェック。飛ばされていたら CookieExpiredError。"""
    login_selectors = [
        'input[autocomplete="username"]',
        'input[name="username"]',
        'a[href="/login"]',
        'a[href*="/login?"]',
    ]
    for sel in login_selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                raise CookieExpiredError(
                    f"Threadsのログインセッションが切れています（{sel} 検出）。"
                    "Macで `python3 extract_cookies2.py && gh secret set THREADS_SESSION < session.json` を実行してください。"
                )
        except CookieExpiredError:
            raise
        except Exception:
            continue


def already_posted_today(time_slot):
    """今日その時間帯に投稿済みか。cron多重発火時の重複防止。
    各slotには「妥当な投稿時間帯」を設定し、その範囲内の記録のみ「投稿済み」と判定する。
    範囲外（例: evening が深夜0時に記録）は遅延発火扱いで無効 → 正規時間帯に再発火させる。

    JST時間帯：
    - morning: 5:00〜10:59
    - noon:    11:00〜16:59
    - evening: 17:00〜23:59
    """
    if not os.path.exists(LAST_RUN_FILE):
        return False
    try:
        with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    last = data.get(time_slot)
    if not last:
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    if not last.startswith(today):
        return False

    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False

    valid_hours = {
        "morning": (5, 11),
        "noon":    (11, 17),
        "evening": (17, 24),
    }
    h_start, h_end = valid_hours.get(time_slot, (0, 24))
    return h_start <= last_dt.hour < h_end


def record_success(time_slot):
    data = {}
    if os.path.exists(LAST_RUN_FILE):
        try:
            with open(LAST_RUN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[time_slot] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def schedule_next_wake(time_slot):
    """次の投稿時刻に Mac を自動 wake させる予約。Mac実行時のみ動く。
    クラウド（GitHub Actions等）実行時は CI 環境変数で検知してスキップする。"""
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return

    next_wake_times = {
        "morning": ["11:58:00", "12:06:00"],
        "noon": ["20:58:00", "21:06:00"],
    }
    if time_slot not in next_wake_times:
        return

    target_date = datetime.now().strftime("%m/%d/%y")
    for target_time in next_wake_times[time_slot]:
        schedule_str = f"{target_date} {target_time}"
        try:
            subprocess.run(
                ["sudo", "-n", "/usr/bin/pmset", "schedule", "wakeorpoweron", schedule_str],
                check=True, capture_output=True, text=True, timeout=10
            )
            print(f"[wake予約] {schedule_str} 登録完了")
        except subprocess.CalledProcessError as e:
            print(f"[warn] wake予約失敗 ({schedule_str}): {e.stderr.strip() if e.stderr else e}")
        except Exception as e:
            print(f"[warn] wake予約で例外 ({schedule_str}): {e}")


def wait_for_network(timeout=240, interval=5):
    """ネットが繋がるまで待機。DarkWake直後のWi-Fi未接続対策。"""
    deadline = time.time() + timeout
    last_err = None
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                "https://www.threads.com/",
                method="HEAD",
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status < 500:
                    print(f"[net] 疎通OK status={resp.status}")
                    return True
        except Exception as e:
            last_err = e
        time.sleep(interval)
    print(f"[net] タイムアウト ({timeout}s) 最後のエラー: {last_err}")
    return False


def select_post(time_slot):
    """ネタを1つ選んで (idx, text_or_list) を返す。used_posts.json への書き込みはしない。
    used_indices は posts.json への永続的な追記履歴として扱い、リセットしない。
    全消費した場合は RuntimeError で停止する（CI失敗→メール通知で気付ける）。"""
    with open(POSTS_FILE, "r", encoding="utf-8") as f:
        posts = json.load(f)

    used = {}
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r", encoding="utf-8") as f:
            used = json.load(f)

    used_indices = used.get(time_slot, [])
    all_posts = posts[time_slot]
    available = [i for i in range(len(all_posts)) if i not in used_indices]
    if not available:
        raise RuntimeError(
            f"[ネタ枯渇] {time_slot} の未使用ネタが0本です。Macで regen.sh を手動実行して "
            f"posts.json を補充してください: cd ~/threads_bot && ./regen.sh"
        )

    idx = random.choice(available)
    return idx, all_posts[idx]


def commit_used(time_slot, idx):
    """投稿成功後に used_posts.json へコミット。リセットせず永続的に記録し続ける。
    posts.json は regen.sh で追記され続けるので、indices は単調増加していく。
    一度使ったidxは二度と引かれない＝過去投稿との被りを永続的にゼロ保証。"""
    used = {}
    if os.path.exists(USED_FILE):
        with open(USED_FILE, "r", encoding="utf-8") as f:
            used = json.load(f)
    used.setdefault(time_slot, [])
    if idx not in used[time_slot]:
        used[time_slot].append(idx)
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, ensure_ascii=False, indent=2)


def _input_text(page, text):
    """モーダル内の最初のcontenteditableに改行込みテキストをkeyboard.typeで入力。"""
    textarea = page.locator('[contenteditable="true"]').first
    textarea.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    textarea.click()
    page.wait_for_timeout(600)
    for i, line in enumerate(text.split("\n")):
        if i > 0:
            page.keyboard.press("Shift+Enter")
        if line:
            page.keyboard.type(line, delay=25)
    page.wait_for_timeout(800)


def _click_submit(page):
    """ダイアログ内の「投稿」ボタンを掴んでclickし、ダイアログが閉じるまで待つ。"""
    dialog = page.locator('div[role="dialog"]').last
    all_buttons = dialog.locator('div[role="button"]')
    btn_count = all_buttons.count()
    EXCLUDE = {"スレッドに追加", "Add to thread", "キャンセル", "Cancel", "オプション", "Options"}
    SUBMIT_LABELS = {"投稿", "投稿する", "Post"}
    candidates = []
    for i in range(btn_count):
        try:
            txt = all_buttons.nth(i).inner_text().strip()
        except Exception:
            continue
        if txt in EXCLUDE:
            continue
        if txt in SUBMIT_LABELS:
            candidates.append((i, txt))
    if not candidates:
        for i in range(btn_count):
            try:
                txt = all_buttons.nth(i).inner_text().strip()
            except Exception:
                continue
            if txt in EXCLUDE:
                continue
            if "投稿" in txt or "Post" in txt:
                candidates.append((i, txt))
    if not candidates:
        raise RuntimeError("送信ボタンが見つかりませんでした")
    target_idx, target_text = candidates[-1]
    target = all_buttons.nth(target_idx)
    print(f"[debug] 送信ボタンクリック: idx={target_idx} text={repr(target_text)}")
    target.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    target.click()
    for _ in range(30):
        page.wait_for_timeout(500)
        if page.locator('div[role="dialog"]').count() == 0:
            break


def _open_composer(page):
    """ホームから新規投稿モーダルを開く。"""
    page.goto("https://www.threads.com", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    _detect_login_required(page)   # cookie切れならここで CookieExpiredError
    opener_selectors = [
        'div[role="button"]:has-text("What\'s new?")',
        'div[role="button"]:has-text("新しい投稿を作成")',
        'div[role="button"]:has-text("いまどうしてる")',
        '[aria-label*="Create"]',
        '[aria-label*="新しい"]',
        'a[href="/intent/post"]',
    ]
    for sel in opener_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            loc.click()
            page.wait_for_timeout(2500)
            return
    raise RuntimeError("投稿コンポーザーを開けませんでした")


def _get_latest_post_url(page, after_reply=False):
    """1部目投稿後（after_reply=False）はプロフィール最上位から取得。
    返信投稿後（after_reply=True）は今いるチェーン詳細ページから自分の最新投稿URLを拾う。
    プロフィールから取るとチェーンの頭（=1部目）が返ってくるため、3部目以降が
    1部目への兄弟返信になりツリーが分裂する不具合を防ぐ。"""
    if after_reply:
        page.wait_for_timeout(3500)
        locator = page.locator(f'a[href*="/@{USERNAME}/post/"]')
        count = locator.count()
        if count == 0:
            raise RuntimeError("チェーン詳細に自分の投稿リンクが見つかりません")
        seen = []
        for i in range(count):
            href = locator.nth(i).get_attribute("href")
            if href and "/post/" in href and href not in seen:
                seen.append(href)
        if not seen:
            raise RuntimeError("チェーン詳細から有効なpost URLを取得できません")
        return seen[-1]

    page.goto(f"https://www.threads.com/@{USERNAME}", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    articles = page.locator('div[data-pressable-container="true"]')
    if articles.count() == 0:
        articles = page.locator('article')
    if articles.count() == 0:
        raise RuntimeError("最新投稿が見つかりません")
    first = articles.first
    first.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    time_link = first.locator('time').first
    if time_link.count() == 0:
        raise RuntimeError("最新投稿のtimeリンクが見つかりません")
    href = time_link.evaluate("el => el.closest('a')?.href")
    if not href:
        raise RuntimeError("最新投稿のhrefが取得できません")
    return href


def _open_reply_modal(page, post_url):
    """投稿詳細ページに遷移して、post_url のポスト本体に紐づく「返信」アイコンを押す。
    Threadsの仕様:
      - 1部目のリプライアイコンを押すと「1部目への返信」になり、既存の2部目と兄弟化する
      - 2部目（=チェーン末尾）のリプライアイコンを押すと「2部目への返信」=チェーン延長になる
    なので post_url にマッチする投稿コンテナを特定してから、その配下の「返信」を押す。"""
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    post_id = post_url.rstrip("/").split("/")[-1]
    target = page.locator(
        f'div[data-pressable-container="true"]:has(a[href*="/post/{post_id}"])'
    ).first
    reply_btn = None
    if target.count() > 0:
        reply_btn = target.locator('[aria-label*="返信"]').first
    if reply_btn is None or reply_btn.count() == 0:
        # フォールバック: 末尾のチェーンポスト（自分のpost link を持つ最後のコンテナ）
        own_containers = page.locator(
            f'div[data-pressable-container="true"]:has(a[href*="/@{USERNAME}/post/"])'
        )
        if own_containers.count() > 0:
            reply_btn = own_containers.last.locator('[aria-label*="返信"]').first
    if reply_btn is None or reply_btn.count() == 0:
        raise RuntimeError("返信ボタンが見つかりません")
    reply_btn.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    reply_btn.click()
    page.wait_for_timeout(2500)
    if page.locator('div[role="dialog"]').count() == 0:
        raise RuntimeError("返信モーダルが開きませんでした")


def post_to_threads(texts, debug=False, dry_run=False):
    """texts: list[str] または str。
    複数要素なら 1部目を単発投稿 → 自分の投稿に self-reply で 2部目, 3部目...と続けてツリー化。"""
    if isinstance(texts, str):
        texts = [texts]

    with sync_playwright() as p:
        force_headed = os.environ.get("HEADED") == "1"
        is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
        # CI（クラウド）では XServer が無いので常にheadless。
        # ローカルは dry-run時のみheadedで目視確認できる従来挙動を維持。
        headless = True if is_ci else ((not dry_run) and not force_headed)
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            storage_state=SESSION_FILE,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()

        try:
            print(f"[1/{len(texts)}] 1部目を新規投稿")
            _open_composer(page)
            _input_text(page, texts[0])
            if dry_run:
                print("[dry_run] 1部目の入力まで完了。Postは押さずに終了。")
                page.screenshot(path="debug_dry_part1.png", full_page=False)
                browser.close()
                return
            _click_submit(page)

            if len(texts) > 1:
                page.wait_for_timeout(3000)
                current_url = _get_latest_post_url(page)
                print(f"[debug] 1部目URL: {current_url}")

                for i, text in enumerate(texts[1:], start=2):
                    print(f"[{i}/{len(texts)}] {i}部目を返信として投稿")
                    _open_reply_modal(page, current_url)
                    _input_text(page, text)
                    _click_submit(page)
                    page.wait_for_timeout(3000)
                    current_url = _get_latest_post_url(page, after_reply=True)
                    print(f"[debug] {i}部目URL: {current_url}")
        finally:
            browser.close()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ["morning", "noon", "evening"]:
        print("使い方: python3 post.py morning|noon|evening [--dry-run]")
        sys.exit(1)

    time_slot = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    if not dry_run and already_posted_today(time_slot):
        print(f"[skip] {time_slot} は本日すでに投稿済み。終了。")
        return

    if dry_run:
        texts = ["テスト1部目（dry-run）。", "テスト2部目。", "テスト3部目。"]
        idx = None
    else:
        idx, text = select_post(time_slot)
        texts = [text] if isinstance(text, str) else list(text)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = " [DRY-RUN]" if dry_run else ""
    kind = "ツリー" if len(texts) > 1 else "単発"
    print(f"[{now}]{mode} {time_slot} の投稿を開始（{len(texts)}部の{kind}）")
    for i, t in enumerate(texts):
        print(f"--- part {i+1}/{len(texts)} ---\n{t}\n")

    if not dry_run:
        wait_for_network(timeout=240, interval=5)

        # 投稿時刻ジッター（0〜5分のランダム遅延）。毎日同じ時刻ピッタリのbot臭を消す。
        if not os.environ.get("SKIP_JITTER"):
            jitter_sec = random.randint(0, 300)
            print(f"[jitter] 投稿前 {jitter_sec}秒（{jitter_sec//60}分{jitter_sec%60}秒）待機")
            time.sleep(jitter_sec)

    # 投稿失敗時の自動リトライ（最大3回・90秒間隔）。ネタは成功時のみ消費する。
    max_attempts = 1 if dry_run else 3
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            post_to_threads(texts, debug=dry_run, dry_run=dry_run)
            last_exc = None
            break
        except CookieExpiredError as e:
            # cookie切れはリトライしても無駄なので即exit。専用exit codeで workflow に伝える
            print(f"[fatal] Threadsログイン切れ: {e}")
            sys.exit(EXIT_COOKIE_EXPIRED)
        except Exception as e:
            last_exc = e
            print(f"[retry] 試行{attempt}/{max_attempts} 失敗: {e}")
            if attempt < max_attempts:
                # 再試行前にネット疎通だけ再確認
                wait_for_network(timeout=120, interval=5)
                time.sleep(60)
    if last_exc is not None:
        print(f"[fatal] {max_attempts}回試行して投稿できませんでした: {last_exc}")
        sys.exit(EXIT_GENERIC_FAIL)

    print("投稿完了！" if not dry_run else "[dry-run] 終了")

    if not dry_run:
        commit_used(time_slot, idx)
        record_success(time_slot)
        schedule_next_wake(time_slot)


if __name__ == "__main__":
    main()
