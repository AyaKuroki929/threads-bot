"""Threads(SPA) の DOM 読み取りを堅牢化する共通ヘルパー。

ThreadsはReactのSPAで、閲覧者自身の状態（ログイン済み/いいね済み等）は
ページ読み込み直後ではなく後からAPIで反映される。domcontentloaded直後に
読むと未描画のまま誤判定する（2026-07に like/login/research で誤報が続発）。

対策の共通化：
- セレクタを一元管理（次にMetaがDOMを変えても、ここだけ直せば全スクリプトに反映）
- goto_ready(): networkidleまで待ち、失敗時のみdomcontentloadedにフォールバック
- is_logged_in(): 「作成 or loginリンク」のどちらかが描画されるまで待ってから判定。
  一時的な描画遅れで「判定不能」になり誤スキップ/誤警告するのを、リトライで防ぐ。
"""
import random

# ── セレクタ一元管理（Metaの仕様変更時はここだけ直す）─────────────
# ログイン中のサイン（作成ボタン。ja/en両対応）
CREATE_SEL = '[aria-label*="Create"], [aria-label*="作成"], [aria-label*="新規スレッド"]'
# ログイン中にだけ出るナビ要素の総合セット。「作成」が未描画でも、これらのどれかが
# 出ていればログイン中と判定できる（実DOMで確認済みのaria-label群）。
LOGGEDIN_SEL = (
    CREATE_SEL
    + ', [aria-label*="お知らせ"], [aria-label*="プロフィール"], [aria-label*="保存済み"]'
    + ', [aria-label*="インサイト"], [aria-label*="メッセージ"]'
    + ', [aria-label*="Notifications"], [aria-label*="Profile"], [aria-label*="Saved"]'
    + ', [aria-label*="Insights"], [aria-label*="Direct"]'
)
# 未ログインのサイン（loginリンク）
LOGIN_LINK_SEL = 'a[href*="/login"]'
# 再認証モーダル（パスワード変更後等に出る「Instagramで続行」画面）。
# この状態は作成ボタンもloginリンクも出ないため、検知しないと永遠に「判定不能」になる。
REAUTH_SEL = ('button:has-text("Continue with Instagram"), '
              'a:has-text("Continue with Instagram"), '
              'div[role="button"]:has-text("Continue with Instagram"), '
              'div[role="button"]:has-text("Instagramで続行"), '
              'button:has-text("Instagramで続行")')
# いいねボタン/トグル（未いいね「いいね！」/ いいね済み「いいね！を取り消す」。svgにaria-labelが載る）
LIKE_TOGGLE_SEL = ('svg[aria-label*="いいね"], svg[aria-label*="取り消"], '
                   'svg[aria-label*="Like"], svg[aria-label*="Unlike"], svg[aria-label*="like"]')
# 投稿カードのコンテナ（検索結果・プロフィールの各投稿）
POST_CONTAINER_SEL = 'div[data-pressable-container="true"]'


def goto_ready(page, url, timeout=25000):
    """networkidle まで待って遷移する。SPAのAPI応答完了を待つのが目的。
    networkidleが取れない重いページ向けに domcontentloaded へフォールバック。"""
    try:
        page.goto(url, wait_until="networkidle", timeout=timeout)
    except Exception:
        page.goto(url, wait_until="domcontentloaded", timeout=min(timeout, 20000))


def is_logged_in(page, retries=1):
    """threads.com トップでログイン状態を確認する。
    戻り値: True=ログイン中 / False=未ログイン / None=判定不能。
    判定不能(None)は一時的な描画遅れのことが多いので、retries回まで再試行する
    （偽いいね防止と、誤『判定不能』警告の抑制を両立）。"""
    for attempt in range(retries + 1):
        try:
            goto_ready(page, "https://www.threads.com/")
            # ログイン中のサイン（作成/お知らせ/プロフィール等）か 未ログイン(loginリンク)
            # のどちらかが描画されるまで待つ。「作成」1つに頼らず総合セットで見る。
            try:
                page.wait_for_selector(f"{LOGGEDIN_SEL}, {LOGIN_LINK_SEL}", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            if page.locator(LOGGEDIN_SEL).count() > 0:
                return True
            if page.locator(LOGIN_LINK_SEL).count() > 0:
                return False
            # 再認証モーダル＝実質セッション無効（要再ログイン）。判定不能ではなくFalse。
            try:
                if page.locator(REAUTH_SEL).count() > 0:
                    print("[login] 再認証モーダル（Continue with Instagram）を検出 → セッション無効")
                    return False
            except Exception:
                pass
        except Exception:
            pass
        if attempt < retries:
            page.wait_for_timeout(random.randint(2500, 4000))  # 描画遅れならリトライで回復
    return None


def is_red_heart(color: str) -> bool:
    """いいね済みハートの赤系色か。rgb(0-255) / display-p3(0-1) 両対応の緩い判定。
    未いいねは黒〜グレー。いいね済みは赤（例 display-p3 1 0.18 0.25 / rgb(255,48,64)）。"""
    if not color:
        return False
    import re
    cleaned = color.replace("display-p3", "").replace("p3", "")
    nums = re.findall(r"[\d.]+", cleaned)
    if len(nums) < 3:
        return False
    r, g, b = float(nums[0]), float(nums[1]), float(nums[2])
    if r <= 1 and g <= 1 and b <= 1:        # 0-1スケール(display-p3等)
        return r > 0.6 and g < 0.5 and b < 0.5
    return r > 150 and g < 120 and b < 120  # 0-255スケール(rgb)
