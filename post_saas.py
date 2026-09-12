"""
SaaS版 Threads自動投稿スクリプト
Supabaseから全アクティブサロンのトークンを読み込み、
公式 Threads API で投稿する。
使い方: python3 post_saas.py morning / python3 post_saas.py evening
"""
import sys
import json
import os
import re
import time
import random
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

from botlib import line_broadcast
import post_state

JST = timezone(timedelta(hours=9))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
# 投稿本文プールの場所。既定は同ディレクトリの posts_saas/。
# private リポ運用時は環境変数 POSTS_DIR でチェックアウト先を指す（公開リポに顧客ファイルを置かないため）。
POSTS_DIR = os.environ.get("POSTS_DIR") or os.path.join(os.path.dirname(__file__), "posts_saas")

SLOT = sys.argv[1] if len(sys.argv) > 1 else "morning"  # morning / noon / evening
# 特定サロンのみ投稿（テスト/デモ用）。argv[2] または環境変数 ONLY_SALON。
# 通常運用（スケジュール）では空＝全サロン。指定時は重複チェックも飛ばして必ず投稿する。
SALON_FILTER = (sys.argv[2] if len(sys.argv) > 2 else os.environ.get("ONLY_SALON", "")).strip()
# 検証用：DRY_RUN=1 でプール読込（POSTS_DIR）だけ確認し、Threadsへは投稿しない。
DRY_RUN = os.environ.get("DRY_RUN") == "1" or "--dry-run" in sys.argv
THREADS_API = "https://graph.threads.net/v1.0"


def supabase_get(path, params=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=_timeout(30)) as resp:
        return json.loads(resp.read())


def supabase_post(path, data):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=_timeout(30)) as resp:
        return resp.status


def get_active_salons():
    # created_at は「いつから投稿義務があるか」の目安。取りこぼしの点検で
    # 稼働前の日を「未投稿」と数えないために使う（2026-09-12 Sol指摘#5）
    return supabase_get("salons", {"is_active": "eq.true",
                                   "select": "id,salon_name,threads_user_id,"
                                             "access_token,instagram_url,created_at"})


LINE_TOKEN = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")


def _notify_line(message: str) -> bool:
    """管理者LINE通知。**送れたかどうかを返す**。

    ⚠️ 送る前に「通知済み」の印を付けると、送信に失敗したときに永久に黙る
    （2026-09-12 Sol指摘#3）。印は返り値がTrueのときだけ付けること。"""
    if not LINE_TOKEN:
        return False
    try:
        # ⚠️ line_broadcast は失敗しても例外にせず False を返す。
        # 戻り値を捨てると「送れていないのに通知済み」になる（2026-09-12 Sol指摘#1）
        return bool(line_broadcast(message, token=LINE_TOKEN))
    except Exception as e:
        print(f"[line] 通知に失敗: {str(e)[:100]}")
        return False


def get_used_posts(salon_id, slot):
    rows = supabase_get("post_logs", {
        "salon_id": f"eq.{salon_id}",
        "slot": f"eq.{slot}",
        "select": "post_content",
    })
    return {r["post_content"] for r in rows}


def already_posted_today(salon_id, slot, op_id=None, jst_date=None):
    # 「今日」はJST(Asia/Tokyo)基準で判定する。
    # 投稿はJSTスケジュール(7/12/21時)だが朝7時=UTC前日22時で、UTC日付だと
    # UTCの境目(0時UTC=朝9時JST)を朝投稿がまたぎ、前日分を当日扱いして誤スキップ→
    # 遅延した予備実行が二重投稿し、朝投稿が9時台に固定される不具合があった。
    # ⚠️ op_id を渡せるならそれで見る。posted_at だけで見ると、前日枠の回収が
    # 今日の時刻で記録されたとき「今日は投稿済み」と誤読して当日分を丸ごと落とす
    # （2026-09-12 Sol指摘#4）。op_id が無い古い記録だけ従来どおり日付で見る。
    if op_id:
        rows = supabase_get("post_logs", {
            "op_id": f"eq.{op_id}", "select": "id", "limit": "1"})
        if rows:
            return True
    # ⚠️ 対象日を渡せるようにする。渡さないと、昨夜の分を調べているのに
    # 「今日00:00以降」で探して見落とす（2026-09-12 Sol指摘#5）
    base = datetime.now(JST) if not jst_date else datetime.strptime(
        str(jst_date), "%Y-%m-%d").replace(tzinfo=JST)
    start_jst = base.replace(hour=0, minute=0, second=0, microsecond=0)
    end_jst = start_jst + timedelta(days=1)
    rows = supabase_get("post_logs", {
        "salon_id": f"eq.{salon_id}",
        "slot": f"eq.{slot}",
        "posted_at": f"gte.{start_jst.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "op_id": "is.null",          # op_id のある記録は上の照合で判断済み
        "select": "id,posted_at",
        "limit": "20",
    })
    end_utc = end_jst.astimezone(timezone.utc)
    for r in rows:
        d = _parse_ts(r.get("posted_at"))
        if d is not None and d < end_utc:
            return True
    return False


def _safe_name(salon_name):
    return re.sub(r'[^\w\-]', '_', salon_name)


# 自己修復ディスパッチの重複防止（同一実行内で同じサロンに2回キックしない）
_GENERATE_DISPATCHED = set()


def _trigger_generate(salon_name):
    """在庫僅少時に生成ワークフロー(saas_generate)を自動起動する（自己修復）。
    成功=True。GH_PAT未設定や失敗時はFalse（呼び元でフォールバック判断）。"""
    if DRY_RUN:
        print(f"[pool] DRY_RUNのため生成workflowは起動しません（{salon_name}）")
        return False
    pat = os.environ.get("GH_PAT", "")
    if not pat:
        return False
    try:
        payload = json.dumps({"ref": "main", "inputs": {"salon_name": salon_name}}).encode()
        req = urllib.request.Request(
            "https://api.github.com/repos/AyaKuroki929/threads-bot/actions/workflows/saas_generate.yml/dispatches",
            data=payload, method="POST",
            headers={"Authorization": f"Bearer {pat}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"[pool] 生成workflow自動キック失敗 ({salon_name}): {e}", file=sys.stderr)
        return False


def pick_post(salon_name, slot, used_texts):
    posts_file = os.path.join(POSTS_DIR, f"posts_{_safe_name(salon_name)}.json")
    if not os.path.exists(posts_file):
        raise FileNotFoundError(f"投稿ファイルが見つかりません: {posts_file}")

    with open(posts_file) as f:
        data = json.load(f)

    candidates = data.get(slot, [])
    if not candidates:
        # フォールバック：このスロットが空でも、他スロットの投稿を借りて投稿を止めない
        for alt in ("morning", "noon", "evening"):
            if data.get(alt):
                candidates = data[alt]
                print(f"[fallback] {salon_name}: {slot}が空 → {alt}スロットから投稿")
                break
    if not candidates:
        raise ValueError(f"{salon_name}: 全スロットの投稿がありません")

    def _key(p):
        return p if isinstance(p, str) else p[0]

    unused = [p for p in candidates if _key(p) not in used_texts]

    # 自己修復：未使用が残りわずかなら、枯渇する前に生成ワークフローを自動起動する。
    # （2026-07-12 うらかた枯渇の再発防止。人の対応を待たずに自動補充する）
    if len(unused) <= 2 and salon_name not in _GENERATE_DISPATCHED:
        _GENERATE_DISPATCHED.add(salon_name)
        if _trigger_generate(salon_name):
            print(f"[pool] {salon_name} {slot}: 未使用{len(unused)}本 → 生成workflowを自動起動（自己修復）")
        elif not unused:
            # 自動補充もできない時だけ人を呼ぶ（LINEは要アクション時のみの方針）
            _notify_line(f"⚠️ {salon_name} の {slot} 投稿プールが枯渇し、過去投稿を再利用しています。\n自動補充の起動にも失敗したため、saas_generate を手動実行してください。")

    if not unused:
        # プール使い切り→過去投稿の再利用（同一文の再投稿はMetaのスパム判定リスク）。
        # 自動補充を起動済みなので、次のスロットからは新ストックが使われる。
        print(f"[pool] {salon_name} {slot}: 未使用プール枯渇 → 今回のみ過去投稿を再利用")
        unused = candidates

    chosen = random.choice(unused)
    return chosen if isinstance(chosen, list) else [chosen]


_INSTAGRAM_CTA_TEMPLATES = [
    "\ninstagram.com/{handle} に施術写真を載せています。",
    "\ninstagram.com/{handle} にBeforeAfterを載せています。",
    "\ninstagram.com/{handle} にお客様の声を載せています。",
    "\ninstagram.com/{handle} に施術の様子を載せています。",
    "\ninstagram.com/{handle} にサロンの写真を載せています。",
]


def _maybe_add_instagram_cta_saas(texts: list, instagram_url: str) -> list:
    """instagram_urlが設定されているサロンのみ、1/4の確率でCTAを末尾に追加。"""
    if not instagram_url or random.random() >= 0.25:
        return texts
    handle = instagram_url.rstrip("/").split("/")[-1].lstrip("@")
    if not handle:
        return texts
    cta = random.choice(_INSTAGRAM_CTA_TEMPLATES).format(handle=handle)
    result = list(texts)
    result[-1] = result[-1].rstrip() + cta
    print(f"[cta] Instagram誘導追加: 「{cta.strip()}」")
    return result


class TokenExpiredError(Exception):
    pass


def _keyword_topic(text):
    """投稿内容のキーワードからトピックを判定する（AIフォールバック用）。"""
    t = text
    if any(k in t for k in ["ダイエット", "痩せ", "体重", "脂肪", "減量", "体型"]):
        return "ダイエット"
    if any(k in t for k in ["肌", "スキン", "美肌", "毛穴", "シミ", "乾燥", "保湿", "ニキビ"]):
        return "スキンケア"
    if any(k in t for k in ["エステ", "フェイシャル", "施術", "トリートメント", "脱毛"]):
        return "エステ"
    if any(k in t for k in ["健康", "腸活", "免疫", "睡眠", "疲れ", "体調"]):
        return "健康"
    return "美容"


# B2B（サロン向けSaaS等、経営者がターゲットのアカウント）。美容前提のトピックと分離する。
_B2B_SALONS = {"urakata_san_official"}


def _keyword_topic_b2b(text):
    """B2Bアカウント用：サロン経営・業務効率化系のトピックを判定する。"""
    t = text
    if any(k in t for k in ["AI", "リール", "チラシ", "画像生成", "勉強", "学べ", "学び"]):
        return "AI活用"
    if any(k in t for k in ["在庫", "発注", "棚卸", "欠品"]):
        return "在庫管理"
    if any(k in t for k in ["締め", "日報", "月報", "効率", "時短", "自動", "事務"]):
        return "業務効率化"
    if any(k in t for k in ["独立", "開業", "起業", "オープン"]):
        return "独立開業"
    if any(k in t for k in ["数字", "売上", "利益", "経費", "コスト", "通帳"]):
        return "サロン経営"
    return "サロン経営"


def _select_topic(texts, salon_name=""):
    """投稿内容に最適なトピックをClaude APIで選択して返す（SaaS版：全サロン対応）。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    post_body = "\n".join(texts)[:600]
    is_b2b = salon_name in _B2B_SALONS
    if is_b2b:
        account_hint = f"サロン経営・業務効率化・サロン向け管理システム・数字管理・独立開業・AI活用（{salon_name}）"
        topic_examples = "サロン経営、業務効率化、独立開業、AI活用、在庫管理"
        fallback_fn = _keyword_topic_b2b
    else:
        account_hint = f"美容サロン・エステ・スキンケア・ダイエット（{salon_name}）" if salon_name else "美容サロン・エステ・スキンケア・ダイエット"
        topic_examples = "美容、エステ、ダイエット、スキンケア"
        fallback_fn = _keyword_topic

    if not api_key:
        chosen = fallback_fn(post_body)
        print(f"[topic:{salon_name}] APIキー未設定 → キーワード判定: '{chosen}'")
        return chosen

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = f"""以下のThreads投稿に最もぴったりなトピック（話題カテゴリ）を1つだけ選んでください。
アカウントのテーマ：{account_hint}

【投稿内容】
{post_body}

【選び方の基準】
・Threads内でそのキーワードで検索したときに関連コンテンツが出るような一般的なカテゴリ名
・日本語1〜3語のキーワード（例：{topic_examples}）
・投稿の主題を最もよく表すもの

キーワードだけ出力してください。説明・記号・改行は不要です。"""
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        topic = re.sub(r'[「」『』・\n\r]', '', resp.content[0].text).strip()
        print(f"[topic:{salon_name}] AI選択: '{topic}'")
        return topic
    except Exception as e:
        chosen = fallback_fn(post_body)
        print(f"[topic:{salon_name}] AI選択失敗 → キーワード判定 '{chosen}': {e}")
        return chosen


def get_user_id_from_token(token, attempts=3):
    """/me。⚠️ ここが1回こけただけで全サロンが投稿できなくなるので、
    一時障害（タイムアウト・5xx・429）だけ少数回やり直す（2026-09-12 Sol指摘#3）。
    空IDや不一致は再試行しても直らないので、そのまま失敗させる。"""
    last = None
    for n in range(1, attempts + 1):
        if n > 1 and _out_of_time("/me"):
            raise last
        try:
            return _get_user_id_from_token_once(token)
        except TokenExpiredError:
            raise
        except Exception as e:
            last = e
            if n >= attempts or not _is_transient_error(e):
                raise
            wait = min(5 * n, 15)
            print(f"[/me] 取得失敗 {n}/{attempts}（{str(e)[:60]}）→ {wait}秒待って再試行")
            if not _wait_within_budget(wait, "/me の再試行"):
                raise
    raise last


def _get_user_id_from_token_once(token):
    """トークンから実際のuser_idを取得（/me エンドポイント）"""
    url = f"{THREADS_API}/me?fields=id,username&access_token={token}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=_timeout(15)) as resp:
            d = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        # ⚠️ ここを一般エラーにすると、再連携が必要なことが専用通知に届かない
        # （2026-09-12 Sol指摘#7）
        if e.code in (401, 403):
            body = ""
            try:
                body = e.read().decode()[:150]
            except Exception:
                pass
            raise TokenExpiredError(f"トークン切れ HTTP {e.code}: {body}")
        raise
    return str(d["id"]), d.get("username", "")


def supabase_patch(path, data, params):
    url = f"{SUPABASE_URL}/rest/v1/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="PATCH"
    )
    with urllib.request.urlopen(req, timeout=_timeout(15)) as resp:
        return resp.status


def _container_status(creation_id, token):
    """コンテナの公開状態を問い合わせる。PUBLISHED/FINISHED/IN_PROGRESS/ERROR/EXPIRED/None(不明)。

    Metaの定義では FINISHED は「公開の準備ができた」であり公開済みではない。
    PUBLISHED だけが公開済み。ここを混同すると投稿欠落か二重投稿になる（Sol指摘①-3）。"""
    if not creation_id:
        return None
    url = f"{THREADS_API}/{creation_id}?" + urllib.parse.urlencode(
        {"fields": "status,error_message", "access_token": token})
    try:
        with urllib.request.urlopen(url, timeout=_timeout(15)) as resp:
            body = json.loads(resp.read())
        return (body.get("status") or "").upper() or None
    except urllib.error.HTTPError as e:
        # ⚠️ 認証エラーを握り潰さない。再連携が必要なのに「状態不明」として
        # 回収を繰り返すだけになる（2026-09-12 Sol指摘#3）
        if e.code in (401, 403):
            raise TokenExpiredError(f"トークン切れ HTTP {e.code}（コンテナ状態の問い合わせ）")
        print(f"[publish] コンテナ状態の問い合わせ失敗: {str(e)[:80]}")
        return None
    except Exception as e:
        print(f"[publish] コンテナ状態の問い合わせ失敗: {str(e)[:80]}")
        return None


def _norm(t):
    """本文比較用の正規化。空白は「畳む」（削除しない）。
    削除すると "ab c" と "a bc" が同一になり別投稿を取り違える（Sol指摘#5）。"""
    return post_state.norm_text(t)


def _parse_ts(ts):
    """Threadsの timestamp を datetime に。形式は "2026-09-12T03:10:41+0000"（実測）。
    ⚠️ Python 3.10以前の fromisoformat は "+0000"（コロン無し）を読めず例外になる。
    読めないまま unknown に倒すと、公開済み投稿のIDを永久に回収できない。"""
    from datetime import datetime
    t = (ts or "").strip()
    if not t:
        return None
    t = t.replace("Z", "+00:00")
    if len(t) >= 5 and t[-5] in "+-" and t[-3] != ":":
        t = t[:-2] + ":" + t[-2:]
    try:
        return datetime.fromisoformat(t)
    except Exception:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                return datetime.strptime(ts, fmt)
            except Exception:
                pass
    return None


# ⚠️ 本文照合による投稿IDの回収は廃止した（2026-09-12 Sol指摘#2）。
# 「本文が一致する・時刻が新しい・候補が1件」を全部満たしても、
# それが今回公開した投稿である証明にはならない（一覧の反映遅れ・手動投稿）。
# このアプリは threads_manage_replies 権限が無く /replies も replied_to も引けないため、
# コンテナと投稿IDを結びつける手段がAPI側に無いことを実測で確認している。
# したがって：公開応答を失って投稿IDが取れなかった場合、**続きのパートは出さない**。
# 推測で返信先を決めるより、ツリーが途中で終わって通知が飛ぶほうが害が小さい。


def _create_container(user_id, token, text, reply_to_id=None, topic_tag=None, image_url=""):
    """コンテナを作る。コンテナは作っただけでは公開されないので、
    ここでの再試行は二重投稿にならない（安全に何度でも試せる唯一の工程）。"""
    create_url = f"{THREADS_API}/{user_id}/threads"
    payload = {
        "media_type": "IMAGE" if image_url else "TEXT",
        "text": text,
        "access_token": token,
    }
    if image_url:
        # Threads側がこのURLへ取りに来るため、公開URLでなければコンテナ作成が失敗する
        payload["image_url"] = image_url
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    if topic_tag:
        payload["topic_tag"] = topic_tag

    def _post(pl):
        data = urllib.parse.urlencode(pl).encode()
        req = urllib.request.Request(create_url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=_timeout(30)) as resp:
                return json.loads(resp.read())["id"]
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            if e.code in (401, 403):
                raise TokenExpiredError(f"トークン切れ HTTP {e.code}: {body[:150]}")
            raise RuntimeError(f"コンテナ作成失敗 HTTP {e.code}: {body[:150]}")

    try:
        return _post(payload)
    except RuntimeError:
        if "topic_tag" in payload:
            print(f"[topic] topic_tag='{payload['topic_tag']}' が拒否された → トピックなしで再試行")
            return _post({k: v for k, v in payload.items() if k != "topic_tag"})
        raise


def _publish_container(user_id, token, creation_id):
    """コンテナを公開する。応答が返れば投稿IDを返す。

    ⚠️ ここが本体の安全装置：**同じ creation_id は最大1件の投稿しか生まない。**
    だから応答を失っても「同じコンテナで」公開をやり直すのは二重投稿にならない。
    絶対にやってはいけないのは、応答喪失を理由に**新しいコンテナを作る**こと。"""
    publish_url = f"{THREADS_API}/{user_id}/threads_publish"
    data = urllib.parse.urlencode({
        "creation_id": creation_id,
        "access_token": token,
    }).encode()
    req = urllib.request.Request(publish_url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_timeout(30)) as resp:
            return json.loads(resp.read())["id"]
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception:
            pass
        if e.code in (401, 403):
            raise TokenExpiredError(f"トークン切れ HTTP {e.code}: {body[:150]}")
        err = RuntimeError(f"公開要求失敗 HTTP {e.code}: {body}")
        err.code = e.code
        raise err


THREADS_TEXT_LIMIT = 500


def _split_long(text, limit=480):
    """Threadsの500字制限の安全網：limitを超えるテキストを文/段落境界で分割する。
    通常は生成側で350字以内に収まるが、万一の超過でも投稿失敗しないようツリー分割する。"""
    text = str(text)
    if len(text) <= limit:
        return [text]
    parts = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("！"),
                  window.rfind("？"), window.rfind("\n"))
        if cut < int(limit * 0.5):
            cut = limit - 1  # 良い区切りがなければ強制カット
        parts.append(rest[:cut + 1].strip())
        rest = rest[cut + 1:].strip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


def _enforce_threads_limit(texts):
    """各パートが500字以内になるよう必要なら分割。texts: list[str] → list[str]"""
    out = []
    for t in texts:
        out.extend(_split_long(t))
    return out


# Meta(Threads)側の一時障害（HTTP 5xx / 429 / is_transient）は数分続くことがある。
# その場合は段階的に待ち時間を延ばして粘る（30秒→2分→5分）。待っている間に
# Metaが復旧すれば投稿成功する。通常エラー（400等の恒久エラー）は短い待ちで済ます。
# 一時障害時の待機秒（失敗回数ごと）。合計230秒。
# ⚠️ 以前は [30,120,300]＝合計450秒で、ジョブ全体の制限10分に対して1サロンで使い切り、
# 後続サロンが時間切れで投稿できなくなる状態だった（2026-09-12 Sol指摘）。
# 回数は増やさない（増やすと応答喪失時の二重投稿リスクが上がる）。
TRANSIENT_RETRY_WAITS = [20, 60, 150]
QUICK_RETRY_WAIT = 10                    # その他エラー時の待機秒
MAX_POST_ATTEMPTS = len(TRANSIENT_RETRY_WAITS) + 1  # 合計4回試行

# 実行全体で「待機」に使ってよい上限。これを超えたら待たずに次のサロンへ進む。
# 障害が長引いたときに、先頭のサロンだけ粘って残り全員が欠ける事態を防ぐ（2026-09-12 Sol指摘）。
RETRY_BUDGET_SEC = int(os.environ.get("RETRY_BUDGET_SEC", "300"))
# ジョブ全体（workflow の timeout-minutes: 10）に対して余裕を残す締切。
# これが無いと、先頭サロンの通信待ちだけで後続サロンが処理前に打ち切られる（Sol指摘#4）
JOB_BUDGET_SEC = int(os.environ.get("JOB_BUDGET_SEC", "480"))
_job_deadline = None
# 実行を始めたときのJST日付。途中で日付が変わったら新しい投稿はしない
# （夜の実行が23時台に始まり、穴埋め中に日付が変わって翌日分を深夜に出す事故の防止・Sol指摘#1）
_run_jst_date = None
# 回収中は低い層から直接通知しない（まとめの1通へ集約する・2026-09-12 Sol指摘#5）
_quiet_notify = False
# 回収処理に与える締切（unix秒）。ここを過ぎたら待機も公開のやり直しも打ち切る。
# 開始前だけの確認だと、1枠の通信待ちだけで上限を大きく超える（2026-09-12 Sol指摘#6）
_deadline = None


def _date_rolled_over():
    if _run_jst_date is None:
        return False
    now = datetime.now(JST).strftime("%Y-%m-%d")
    if now != _run_jst_date:
        print(f"[date] 実行中に日付が変わりました（{_run_jst_date} → {now}）→ 新しい投稿はしません")
        return True
    return False


def _out_of_time(label=""):
    """回収の締切とジョブ全体の締切の、厳しいほうを見る。"""
    now = time.time()
    for d, why in ((_deadline, "持ち時間"), (_job_deadline, "ジョブ全体の持ち時間")):
        # ⚠️ 残り0秒は「まだ間に合う」ではない（2026-09-12 Sol指摘#4）
        if d is not None and now >= d:
            print(f"[budget] {why}を過ぎました{('（' + label + '）') if label else ''} → ここで打ち切ります")
            return True
    return False


def _time_left():
    """残り時間（秒）。締切が無ければ None。"""
    ds = [d for d in (_deadline, _job_deadline) if d is not None]
    return (min(ds) - time.time()) if ds else None


def _timeout(default):
    """残り時間を超えないHTTPタイムアウト。通信そのものが締切を食い破らないようにする
    （2026-09-12 Sol指摘#4：待機だけ制限しても通信待ちで超過していた）。"""
    left = _time_left()
    if left is None:
        return default
    return max(1, min(default, int(left)))
_retry_spent = 0.0


def _wait_within_budget(wait: int, label: str) -> bool:
    """予算内なら待って True。予算切れなら待たずに False（＝このサロンは諦めて次へ）。"""
    global _retry_spent
    if _out_of_time(label):
        return False
    left = _time_left()
    if left is not None and wait > left:
        print(f"[budget] {label}を待つと持ち時間を超えます（残り{int(left)}秒） → 待たずに打ち切ります")
        return False
    if _retry_spent + wait > RETRY_BUDGET_SEC:
        print(f"[api]   → 待機予算切れ（使用{int(_retry_spent)}秒/{RETRY_BUDGET_SEC}秒）。"
              f"{label}は諦めて次のサロンへ進む（後続を巻き添えにしない）")
        return False
    time.sleep(wait)
    _retry_spent += wait
    return True


# 一時障害とみなすHTTPステータス（待てば直るもの）
TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _is_transient_error(e):
    """Meta側の一時障害（リトライで回復しうる）かどうか。

    ⚠️ 文字列一致だけで判定してはいけない（2026-09-12 実害）。
    自前で組み立てた "…HTTP 504: …" は拾えても、urllib が投げる生の HTTPError は
    str() が "HTTP Error 504: Gateway Timeout" で間に Error が入るため一致せず、
    「待っても直らないエラー」と誤判定して1回で諦めていた
    （ファミリエさんの昼投稿が504で再試行されず未投稿になった）。
    保護されていない通信箇所から生の例外が上がる経路があるので、
    まず例外オブジェクトのステータス番号を見る。"""
    import re
    import socket

    # ① 例外そのものがHTTPエラーなら、番号で判定（文字列の書式に依存しない）
    code = getattr(e, "code", None)
    if isinstance(code, int) and code in TRANSIENT_STATUS:
        return True

    s = str(e)
    # ② 文字列中のステータス番号を拾う（"HTTP 504" / "HTTP Error 504" のどちらも）
    m = re.search(r"HTTP\s*(?:Error\s*)?(\d{3})", s)
    if m and int(m.group(1)) in TRANSIENT_STATUS:
        return True

    # ③ 通信が切れた・応答が返らない系も待てば直る
    if isinstance(e, (socket.timeout, TimeoutError)):
        return True
    if any(w in s.lower() for w in
           ("is_transient", "timed out", "timeout", "connection reset",
            "connection aborted", "temporarily unavailable", "bad gateway")):
        return True
    return False


PUBLISH_RETRY_WAITS = [5, 15, 30]   # 同じコンテナでの公開やり直し（新規コンテナは作らない）


class LedgerSaveError(post_state.StateError):
    """台帳へのパート保存に失敗した。**何を保存しようとしたか**を持ち歩く。
    公開に成功した直後にここで落ちても、受け取った投稿IDと「公開済み」の事実を
    呼び出し側へ伝えて、記録(post_logs)だけは残せるようにする（2026-09-12 Sol指摘#3）。"""

    def __init__(self, msg, part_index, fields):
        super().__init__(msg)
        self.part_index = part_index
        self.fields = fields


def _ledger_set_part(row, i, **fields):
    """台帳へのパート保存。**公開通信の例外と混ぜない**（2026-09-12 Sol指摘#6）。

    受け取った投稿IDを、台帳が一時的に書けないだけで捨ててしまうと、
    続きのパートが恒久的に出せなくなる。ここだけで粘り、最後は投稿IDを通知に載せる。"""
    last = None
    for n in range(1, 4):
        try:
            return post_state.set_part(row, i, **fields)
        except Exception as e:
            last = e
            print(f"[state] パート{i+1}の保存に失敗 {n}/3: {str(e)[:100]}")
            if n < 3:
                time.sleep(2 * n)
    if not _quiet_notify:
        published = fields.get("status") == post_state.PART_PUBLISHED
        what = ("投稿は出ましたが" if published
                else "投稿できたかは未確定ですが")
        _notify_line(f"🚨 とうこさん：{what}、台帳への保存ができませんでした。\n"
                     f"op_id={row.get('op_id')} / パート{i+1} / "
                     f"post_id={fields.get('post_id') or '(未取得)'} / "
                     f"creation_id={fields.get('creation_id') or '-'}\n"
                     "続きの投稿が止まります。この投稿IDを控えてください。")
    raise LedgerSaveError(f"パート{i+1}の台帳保存に失敗: {last}", i, fields)


def _finalize_part(row, i, user_id, token, creation_id, label, lost_before=False):
    """コンテナ creation_id の公開を確定させる。返り値 (row, post_id|None, outcome)。

      outcome "published" … 公開できた（post_id は取れないことがある）
      outcome "failed"    … **確実に未公開**。新しいコンテナを作り直してよい唯一の状態
      outcome "unknown"   … 確定できない。新しいコンテナは作らせない（次の実行へ引き継ぐ）

    同じ creation_id からは最大1件しか投稿が生まれないので、
    公開のやり直し自体は二重投稿にならない。禁止なのは「新しいコンテナを作ること」。"""
    last = ""
    # 「応答が失われた」のか「APIがはっきり断った」のかを分ける。
    # 応答が失われた後は、コンテナ状態が ERROR でも「未公開」と断定できない
    # （公開されたのに状態がERRORを返せば、新しいコンテナを作って二重投稿になる）。
    # ⚠️ 実行をまたいで覚えておく。前回の実行で応答を失ったコンテナは、
    # 今回の要求が400（明確な拒否）でも「前回も公開していない」証拠にはならない
    # （2026-09-12 Sol指摘#1：呼び出しごとに初期化していて実行をまたぐと破れた）
    lost_response = bool(lost_before)
    for attempt in range(len(PUBLISH_RETRY_WAITS) + 1):
        if attempt == 0 and _out_of_time(label):
            break
        if attempt:
            if not _wait_within_budget(PUBLISH_RETRY_WAITS[attempt - 1], f"{label}の公開確定"):
                break

        # ⚠️ 公開要求を「送る前」に、結果不明になり得ることを台帳へ残す。
        # 送った後に残す作りだと、送信直後にプロセスが落ちた窓で記録が残らず、
        # 次の実行が同じコンテナを作り直して二重投稿になる（2026-09-12 Sol指摘#1）。
        # ここが保存できないなら送らない。
        # 「前の要求からの持ち越し」か「今回の送信で初めて生じる不確かさ」かを分ける。
        # 持ち越しは、今回400が返っても消してはいけない（Sol指摘#1）
        carried = lost_response
        if not lost_response:
            row = _ledger_set_part(row, i, lost_response=True)
            lost_response = True
        # ⚠️ 台帳保存に時間がかかって締切を跨ぐことがある。**送る直前**にもう一度確認する
        # （2026-09-12 Sol指摘#4：残り-1秒で公開要求を送っていた）
        if _out_of_time(f"{label}の公開要求"):
            # 送っていないのに「結果不明」を残すと、次の実行がこのコンテナから抜けられない
            # （2026-09-12 Sol指摘#3）。今回初めて立てた印だけ戻す
            if not carried:
                try:
                    row = post_state.set_part(row, i, lost_response=False)
                    lost_response = False
                except Exception as ex:
                    print(f"[state] 未送信の印戻しに失敗（安全側で不明のまま）: {str(ex)[:80]}")
            break

        # ── 公開要求（通信）。この try に台帳保存を入れない ──
        pid = None
        definitive_refusal = False
        try:
            pid = _publish_container(user_id, token, creation_id)
        except TokenExpiredError:
            raise
        except Exception as e:
            last = str(e)[:150]
            code = getattr(e, "code", None)
            # 4xx（429を除く）＝サーバがはっきり断った＝この要求では公開されていない。
            # それ以外（タイムアウト・切断・5xx・429）は「届いたか分からない」
            definitive_refusal = isinstance(code, int) and 400 <= code < 500 and code != 429
            if definitive_refusal and not carried:
                # 今回の送信で生じた不確かさだけが、この400で解消される。
                # 前の要求が未確定なら、この400は何の証拠にもならない
                try:
                    row = post_state.set_part(row, i, lost_response=False)
                    lost_response = False
                except Exception as ex:
                    print(f"[state] 判定の更新に失敗（安全側に倒して続行）: {str(ex)[:80]}")
        if pid:
            row = _ledger_set_part(row, i, status=post_state.PART_PUBLISHED,
                                   creation_id=creation_id, post_id=pid,
                                   published_ts=datetime.now(timezone.utc).isoformat())
            return row, pid, "published"

        # ── 公開できたかをコンテナ自身に聞く ──
        st = _container_status(creation_id, token)
        print(f"[publish] {label} 公開要求失敗（{last}）／コンテナ状態={st or '不明'}")
        if st == "PUBLISHED":
            # 公開されたことは確定。ただし投稿IDは取れない（推測しない）
            row = _ledger_set_part(row, i, status=post_state.PART_PUBLISHED,
                                   creation_id=creation_id, post_id=None)
            return row, None, "published"
        if st in ("ERROR", "EXPIRED") and not lost_response:
            # 応答を失っていない＝この要求では公開されていないと分かっている。
            # ここだけが「新しいコンテナを作り直してよい」唯一の状態
            row = _ledger_set_part(row, i, status=post_state.PART_PENDING,
                                   creation_id=None, post_id=None, created_ts=None,
                                   lost_response=False)
            return row, None, "failed"
        if st in ("ERROR", "EXPIRED"):
            # 応答を失った後は、コンテナ状態が何であれ「公開されていない」と断定しない。
            # 断定して作り直すと、実は公開済みだった場合に二重投稿になる。
            # 出せずに終わる（通知して人に渡す）ほうを選ぶ
            print(f"[publish] {label} コンテナは{st}だが応答を失っている → "
                  "公開済みの可能性を捨てられないので作り直さない")
            break
        # FINISHED / IN_PROGRESS / 不明 → 同じコンテナで公開をやり直す

    row = _ledger_set_part(row, i, status=post_state.PART_UNKNOWN, creation_id=creation_id)
    print(f"[publish] {label}: 公開できたか確定できません → 次の実行が同じコンテナで解決します")
    return row, None, "unknown"


def _original_mismatch(row, original_first):
    """記録しようとしている原文が、投稿開始時に固定したものと一致するか。
    一致しない／固定が無いなら理由の文字列を返す（記録してはいけない）。

    ⚠️ 記録経路が複数あるので、必ずここを通す（2026-09-12 Sol指摘#1・#2：
    記録復旧だけ直しても、ツリー続行の経路から未投稿の原文が記録できた）。"""
    first = post_state.get_part(row, 0) or {}
    if first.get("status") != post_state.PART_PUBLISHED:
        return None      # まだ公開していない＝記録もしない
    fixed = first.get("original_hash")
    if not fixed:
        return "投稿開始時の原文が台帳に残っておらず、記録すべき本文を確認できません"
    if fixed != post_state.part_hash(original_first):
        return "記録しようとした原文が、投稿開始時の原文と違います"
    return None


def _ledger_consistent(row, texts):
    """台帳のパートが、いまの本文と矛盾していないか。矛盾していれば理由の文字列。

    ⚠️ 全パート公開済みの判定だけでは足りない。添字の重複・範囲外・
    「履歴はあるが別の本文」を残したまま先頭だけ処理して完了にしてしまう
    （2026-09-12 Sol指摘#2）。"""
    parts = row.get("parts") or []
    idx = [p.get("i") for p in parts]
    if len(idx) != len(set(idx)):
        return "台帳のパート番号が重複しています"
    by_i = {}
    for p in parts:
        i = p.get("i")
        if not isinstance(i, int) or i < 0 or i >= len(texts):
            return f"台帳に本文と対応しないパート（{i}）が残っています"
        by_i[i] = p
        has_history = bool(p.get("creation_id") or p.get("post_id") or p.get("lost_response")) \
            or p.get("status") in (post_state.PART_CONTAINER, post_state.PART_UNKNOWN,
                                   post_state.PART_PUBLISHED)
        if has_history and p.get("hash") != post_state.part_hash(texts[i]):
            return f"パート{i+1}は別の本文で処理されています"
        # ⚠️ 状態だけ巻き戻った台帳（pending なのに公開履歴あり）は、
        # そのまま進めると新しく出して二重投稿になる（2026-09-12 Sol指摘#2）
        if p.get("status") == post_state.PART_PENDING and (
                p.get("post_id") or p.get("creation_id") or p.get("lost_response")):
            return f"パート{i+1}は未処理の印なのに、公開の履歴が残っています"
    # ⚠️ 履歴は先頭から連続していること。途中だけ残っていると、
    # 新しい親を立てて古い返信を「済み」と読み飛ばし、別々のツリーができる（Sol指摘#1）
    for i in sorted(by_i):
        if i == 0:
            continue
        prev = by_i.get(i - 1)
        if prev is None or prev.get("status") != post_state.PART_PUBLISHED \
                or not prev.get("post_id"):
            return (f"パート{i}が公開済み・投稿IDありになっていないのに、"
                    f"{i+1}部目の履歴があります")
    return None


def threads_post(row, user_id, token, texts, topic_tag=None, image_url="",
                 original_first=None):
    """単発またはツリー投稿。台帳(row)にパートごとの結果を残しながら進む。

    返り値は dict:
      row            … 更新後の台帳行
      first_post_id  … 1部目の投稿ID（回収できなければ None）
      root_published … 1部目が公開されたか（＝post_logs に記録すべきか）
      complete       … 全パートを出し切ったか
      slot_status    … 台帳に残すべき枠の状態（published / unknown / logged は呼び出し側）
      note           … 未完のとき、その理由
    """
    reply_to_id = None
    first_post_id = None
    root_published = False

    def _result(complete, slot_status, note="", error=None):
        return {"row": row, "first_post_id": first_post_id, "root_published": root_published,
                "complete": complete, "slot_status": slot_status, "note": note, "error": error}

    bad = _ledger_consistent(row, texts)
    if bad:
        return _result(False, post_state.STATUS_ATTENTION,
                       f"{bad}。取り違えを避けるため投稿しません")

    for i, text in enumerate(texts):
        # ⚠️ 途中のパートで例外が出ても、親が公開済みならここで投げない。
        # 投げると呼び出し側が post_logs への記録を飛ばし、次の実行が「未投稿」と
        # 誤判定して親をもう一度出す（2026-09-12 Sol指摘#5）。
        try:
            label = f"part {i+1}/{len(texts)}"
            h = post_state.part_hash(text)
            p = post_state.get_part(row, i) or {}
            same = (p.get("hash") == h)
            pid = None

            if same and p.get("status") == post_state.PART_PUBLISHED:
                pid = p.get("post_id")
                print(f"[publish] {label} は公開済み（台帳）→ 出し直しません")
            elif (not same) and (p.get("creation_id") or p.get("post_id")
                                 or p.get("status") in (post_state.PART_CONTAINER,
                                                        post_state.PART_UNKNOWN,
                                                        post_state.PART_PUBLISHED)):
                # ⚠️ 台帳には「別の本文で出した記録」が残っているのに、今回は違う本文が来ている。
                # そのまま新しく出すと、旧投稿と並んで2本出る（2026-09-12 Sol指摘#1）。
                # 履歴は消さずに人へ渡す
                return _result(False, post_state.STATUS_ATTENTION,
                               f"{label}: 台帳に残っている本文と今回の本文が違います"
                               f"（台帳のパートは {p.get('status')}）。取り違えを避けるため出しません")
            else:
                creation_id = p.get("creation_id") if same and p.get("status") in (
                    post_state.PART_CONTAINER, post_state.PART_UNKNOWN) else None
                outcome = "failed"
                for round_no in range(1, MAX_POST_ATTEMPTS + 1):
                    if not creation_id:
                        # ⚠️ 実行中に日付が変わっていたら、新しい投稿は始めない
                        # （夜の実行が23時台に始まり、穴埋め中に日付が変わって
                        #   翌日分を深夜に出す事故の防止・2026-09-12 Sol指摘#1）
                        if _date_rolled_over():
                            return _result(False, post_state.STATUS_HOLD_REPAIR,
                                           f"{label}: 実行中に日付が変わったので出していません")
                        # 実際に公開した時刻。post_logs の posted_at に使う
                        # （回収が翌日に走っても「その日の投稿」として記録するため・Sol指摘#4）
                        created_ts = datetime.now(timezone.utc)
                        try:
                            creation_id = _create_container(
                                user_id, token, text, reply_to_id=reply_to_id, topic_tag=topic_tag,
                                image_url=image_url if i == 0 else "")
                        except TokenExpiredError:
                            raise
                        except Exception as e:
                            print(f"[api] {label} コンテナ作成 {round_no}/{MAX_POST_ATTEMPTS} 失敗: {e}")
                            if round_no >= MAX_POST_ATTEMPTS:
                                raise
                            wait = (TRANSIENT_RETRY_WAITS[min(round_no, len(TRANSIENT_RETRY_WAITS)) - 1]
                                    if _is_transient_error(e) else QUICK_RETRY_WAIT)
                            print(f"[api]   → {wait}秒待って再試行（コンテナ作成は公開しないので安全）")
                            if not _wait_within_budget(wait, f"{label}のコンテナ作成"):
                                raise
                            continue
                        # ⚠️ 公開の前に必ず台帳へ残す。ここで落ちても未公開のコンテナが残るだけ
                        extra = {}
                        if i == 0 and original_first is not None and not p.get("original_hash"):
                            # 記録する原文を台帳に固定する。あとで payload だけ差し替えても
                            # 出していない本文を「使用済み」にしない（2026-09-12 Sol指摘#1）。
                            # ⚠️ 一度固定したら、コンテナを作り直しても上書きしない。
                            # 上書きすると照合の基準ごと入れ替わってしまう
                            extra["original_hash"] = post_state.part_hash(original_first)
                        row = _ledger_set_part(row, i, hash=h, creation_id=creation_id,
                                               status=post_state.PART_CONTAINER, post_id=None,
                                               created_ts=created_ts.isoformat(),
                                               lost_response=False, **extra)
                        time.sleep(3)
                    row, pid, outcome = _finalize_part(row, i, user_id, token, creation_id, label,
                                                   lost_before=(post_state.get_part(row, i) or {}
                                                                ).get("lost_response", False))
                    if outcome in ("published", "unknown"):
                        break
                    creation_id = None   # "failed" ＝確実に未公開。作り直してよい
                if outcome == "unknown":
                    return _result(False, post_state.STATUS_UNKNOWN,
                                   f"{label}の公開が確定できませんでした（次の実行が同じコンテナで解決します）")
                if outcome != "published":
                    raise RuntimeError(f"{label}: 公開できませんでした（未公開を確認済み）")

            if i == 0:
                root_published = True

            if i == 0:
                first_post_id = pid

            if i < len(texts) - 1 and not pid:
                # ⚠️ コンテナIDや「本文が同じ別投稿」を返信先に代用しない。
                # 続きを出さずに人へ渡す（自動では二度とこのツリーを完成させない）
                return _result(False, post_state.STATUS_HOLD_REPAIR,
                               f"{label}は公開済みですが投稿IDが取れず、"
                               f"続き（残り{len(texts)-i-1}部）を出せません")

            reply_to_id = pid
            print(f"[api] {label} 投稿完了: post_id={pid or '(ID未回収)'}")
            if i < len(texts) - 1:
                time.sleep(3)
        except LedgerSaveError as e:
            # 公開には成功したが台帳に書けなかった場合、投稿IDと事実だけは持ち帰る
            if e.fields.get("status") == post_state.PART_PUBLISHED:
                if e.part_index == 0:
                    root_published = True
                    first_post_id = e.fields.get("post_id")
                print(f"[state] {label} 公開は成功・台帳保存に失敗 → 記録だけ残します")
                return _result(False, post_state.STATUS_HOLD_REPAIR,
                               f"{label}は公開できましたが台帳に保存できませんでした"
                               f"（post_id={e.fields.get('post_id') or '未取得'}）", error=e)
            if not root_published:
                raise
            print(f"[api] {label} で中断: {type(e).__name__}: {str(e)[:120]}")
            return _result(False, post_state.STATUS_PUBLISHED,
                           f"1部目は公開済みですが{label}で中断しました（{type(e).__name__}）", error=e)
        except Exception as e:
            if not root_published:
                raise      # 何も公開していない＝そのまま失敗として扱ってよい
            print(f"[api] {label} で中断: {type(e).__name__}: {str(e)[:120]}")
            return _result(False, post_state.STATUS_PUBLISHED,
                           f"1部目は公開済みですが{label}で中断しました（{type(e).__name__}）", error=e)

    return _result(True, post_state.STATUS_PUBLISHED)


RECOVER_NOTE_MARK = "／通知済み:"


def _acquire_with_retry(salon_id, jst_date, slot, attempts=3):
    """台帳の実行権取得。Supabaseの一時障害1回で投稿を止めない。
    それでも取れなければ例外＝**投稿しない**（台帳無しで出すと二重投稿を防げない）。"""
    last = None
    for i in range(1, attempts + 1):
        try:
            return post_state.acquire(salon_id, jst_date, slot)
        except Exception as e:
            last = e
            print(f"[state] 実行権の取得に失敗 {i}/{attempts}: {str(e)[:100]}")
            if i < attempts:
                time.sleep(3 * i)
    raise last


def _to_human(row, note, message, quiet=False):
    """「人が確認するまで自動では触らない(attention)」へ移す。

    ⚠️ 知らせが**届いてから**でないと attention にしない。attention は回収対象から
    外れるので、届かないまま移すと誰も気づけない（2026-09-12 Sol指摘#1〜3）。
    届くまでは hold_repair（回収対象に残る）で待ち、回収のまとめ通知が届いた時点で移す。"""
    if quiet:
        # 回収中。まとめ通知が届いたら _mark_notified() が attention へ移す
        _state_finish(row, post_state.STATUS_HOLD_REPAIR, note=note)
        return False
    if _notify_line(message):
        # ⚠️ 知らせが届いても、まだ自動で記録を戻せるなら回収対象に残す
        # （2026-09-12 Sol指摘#1：通常経路だけ抜けていた）
        fresh = _safe_fetch(row.get("op_id"), row) or row
        if _repairable(fresh):
            print("[state] 記録を戻せる余地があるので、人待ちにせず回収対象に残します")
            _state_finish(row, post_state.STATUS_HOLD_REPAIR, note=note)
            return False
        _state_finish(row, post_state.STATUS_ATTENTION, note=note)
        return True
    print("[state] 知らせを送れなかったので、人待ちにせず次回へ残します")
    _state_finish(row, post_state.STATUS_HOLD_REPAIR, note=note)
    return False


def _state_finish(row, status, note=None):
    """台帳に結末を残す。ここが残らないと次の実行が状況を引き継げない。

    ⚠️ note を書き換えるときも「通知済み」の印は残す。消すと、同じ理由で
    毎回通知が飛ぶ（2026-09-12 Sol指摘#5）。"""
    prev = row.get("note") or ""
    if note is not None and RECOVER_NOTE_MARK in prev:
        mark = RECOVER_NOTE_MARK + prev.split(RECOVER_NOTE_MARK, 1)[1]
        if RECOVER_NOTE_MARK not in note:
            note = note + mark
    for i in range(1, 4):
        try:
            post_state.update(row, status=status, note=note)
            return True
        except Exception as e:
            print(f"[state] 結末の保存に失敗 {i}/3: {str(e)[:100]}")
            # ⚠️ 途中で他の更新が入って手元の行が古くなっていることがある。
            # 取り直さずに同じ行で粘っても永久に通らない
            fresh = _safe_fetch(row.get("op_id"), None)
            if fresh:
                # ⚠️ 取り直しても無条件に上書きしない。別の実行が状態を変えていたら、
                # こちらの古い結末で戻すと停止や完了が破れる（2026-09-12 Sol指摘#3）。
                # 書き直してよいのは「意味が変わっていない（同じ状態のまま）」ときだけ
                if fresh.get("status") != row.get("status"):
                    print(f"[state] 別の実行が {fresh.get('status')} にしています → 上書きしません")
                    return False
                row = fresh
            if i < 3:
                time.sleep(2 * i)
            else:
                _notify_line("🚨 とうこさん：投稿台帳の更新に失敗しました。\n"
                             "次の実行が状況を引き継げず、二重投稿か投稿欠落が起きる恐れがあります。\n"
                             f"op_id={row.get('op_id')} / 残したかった状態={status}")
    return False


class DuplicateLog(Exception):
    """同じ op_id の記録が既にある＝二重記録をDBが拒否した。成功として扱う。"""


def log_post_with_retry(salon_id, slot, text, op_id, posted_at=None, attempts=5):
    """投稿記録を必ず残す。ここが欠けると次の実行が「未投稿」と誤判定して二重投稿になる。

    二重記録の防止は **DBの一意制約（post_logs.op_id）** に任せる。
    アプリ側で「既にあるか」をGETして確かめる方式は、GETが失敗したときに
    「無い」と誤読して二重に入れてしまうし、同時実行も止められない（2026-09-12 Sol指摘#4）。"""
    last = None
    for i in range(1, attempts + 1):
        # 回収中は締切がある。通信の再試行で持ち時間を食い潰すと当日の投稿が遅れる（Sol指摘#6）
        if i > 1 and _out_of_time("記録の再試行"):
            break
        try:
            log_post(salon_id, slot, text, op_id, posted_at)
            if i > 1:
                print(f"[log_post] {i}回目で記録成功")
            return True
        except DuplicateLog:
            print("[log_post] 同じ記録が既にありました（DBが二重を拒否）→ 成功として扱う")
            return True
        except Exception as e:
            last = e
            print(f"[log_post] 記録失敗 {i}/{attempts}: {str(e)[:80]}")
            if i < attempts and not _wait_within_budget(min(2 ** i, 15), "記録の再試行"):
                break
    raise last if last else RuntimeError("log_post失敗")


def log_post(salon_id, slot, text, op_id, posted_at=None):
    try:
        supabase_post("post_logs", {
            "salon_id": salon_id,
            "slot": slot,
            "post_content": text,
            # 回収が翌日に走っても「実際に公開した時刻」で残す（Sol指摘#4）
            "posted_at": posted_at or datetime.now(timezone.utc).isoformat(),
            "op_id": op_id,
        })
    except urllib.error.HTTPError as e:
        # ⚠️ 409＝一意制約違反とは限らない。PostgRESTは外部キー違反も409にする。
        # コードを見ずに成功扱いすると、記録0件のまま完了してしまう（Sol指摘#6）
        if e.code == 409:
            body = ""
            try:
                body = e.read().decode()[:300]
            except Exception:
                pass
            info = {}
            try:
                info = json.loads(body)
            except Exception:
                pass
            if info.get("code") == "23505" and "post_logs_op_id_uniq" in (info.get("message") or ""):
                raise DuplicateLog(op_id)
            raise RuntimeError(f"記録に失敗 HTTP 409（op_idの重複ではない）: {body[:200]}")
        raise


# bemolle/個人はheartbeatが last_run.json / last_run_personal.json を見て投稿確認するため、
# post_saasで投稿したら同ファイルも更新する（古い監視の誤判定→二重投稿を防止）。
# 安全網は維持：post_saasが失敗すれば更新されず、heartbeatが従来どおりリカバリする。
_LAST_RUN_FILES = {
    "bemolle_diet": "last_run.json",
    "aya_kuroki_0929": "last_run_personal.json",
}


def _sync_last_run(salon_name, slot, jst_date=None):
    """⚠️ 過去の枠を回収したときに呼ぶと、heartbeat が「今日はもう投稿済み」と
    誤読して当日のリカバリを止める（2026-09-12 Sol指摘#5）。今日の枠のときだけ書く。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    if jst_date is not None and str(jst_date) != today:
        print(f"[heartbeat-sync] {jst_date} の枠なので last_run は更新しません（今日は{today}）")
        return
    return _sync_last_run_now(salon_name, slot)


def _sync_last_run_now(salon_name, slot):
    fn = _LAST_RUN_FILES.get(salon_name)
    if not fn:
        return
    path = os.path.join(os.path.dirname(__file__), fn)
    try:
        data = {}
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
        data[slot] = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[heartbeat-sync] {fn} の {slot} を更新（{salon_name}・heartbeat誤リカバリ防止）")
    except Exception as e:
        # ⚠️ 黙って続行しない。ここが壊れたままだと heartbeat が「未投稿」と誤判定して
        # 旧プールから二重投稿する。実際に競合マーカーが混入して読めなくなっていた（2026-09-12）
        print(f"[heartbeat-sync] {fn} 更新失敗: {e}")
        _notify_line(f"🚨 とうこさん：{fn} を更新できませんでした（{salon_name} / {slot}）。\n"
                     f"heartbeat が『未投稿』と誤判定して二重投稿する恐れがあります。\n"
                     f"{type(e).__name__}: {str(e)[:120]}")


# ── 月曜夜の宣伝枠（個人アカのみ） ───────────────────────────────
# 個人アカ(@aya_kuroki_0929)の月曜21時だけ、通常の投稿に代えて「とうこさん」の
# 宣伝を画像付きで出す。文章は毎週変える（generate_promo_posts.py が在庫を補充）。
PROMO_SALON = "aya_kuroki_0929"
PROMO_SLOT = "evening"
PROMO_POOL_FILE = os.path.join(os.path.dirname(__file__), "promo_posts_personal.json")
PROMO_USED_FILE = os.path.join(os.path.dirname(__file__), "promo_used_personal.json")


def is_promo_time(salon_name, slot):
    if os.environ.get("FORCE_PROMO") == "1":
        return salon_name == PROMO_SALON and slot == PROMO_SLOT
    if salon_name != PROMO_SALON or slot != PROMO_SLOT:
        return False
    return datetime.now(JST).weekday() == 0  # 0=月曜


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


class PromoCheckFailed(Exception):
    """使用済みを確認できなかった。「使用済みなし」と混同しない。"""


def _promo_used_from_db(salon_id, days=180):
    """すでに出した宣伝文を **DBの投稿記録から** 拾う。

    ⚠️ 使用済みの正本をローカルJSON（gitにpushして残す）だけに置くと、
    push前に実行環境が消えたときに記録が失われ、同じ宣伝文がまた選ばれる
    （2026-09-12 Sol指摘#3）。post_logs は消えないので、こちらも必ず見る。"""
    if not salon_id:
        return set()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = supabase_get("post_logs", {
            "select": "post_content", "salon_id": f"eq.{salon_id}",
            "slot": f"eq.{PROMO_SLOT}", "posted_at": f"gte.{since}", "limit": "500"})
        used = {post_state.norm_text(r.get("post_content")) for r in rows}
        # ⚠️ まだ片づいていない枠（公開したかもしれない・記録が未完）で使った宣伝文も外す。
        # 外さないと、翌週その文をもう一度選んで二重投稿になる（2026-09-12 Sol指摘#1）
        # ⚠️ 未解決の枠は**日付で切らない**。何日前でも、公開したかもしれない本文を
        # もう一度選んではいけない（2026-09-12 Sol指摘#5）
        pending = supabase_get("post_attempts", {
            "select": "payload", "salon_id": f"eq.{salon_id}",
            "slot": f"eq.{PROMO_SLOT}",
            "status": "in.(unknown,published,running,hold_repair,attention)", "limit": "1000"})
        for r in pending:
            pl = r.get("payload") or {}
            txt = pl.get("original_first")
            # ⚠️ promo_used が立っていても、記録が未完なら「片づいていない」。
            # 除外しないと、その本文をもう一度選んで二重投稿になる（Sol指摘#2）
            if pl.get("promo") and txt:
                used.add(post_state.norm_text(txt))
        return used
    except Exception as e:
        # ⚠️「確認できなかった」を「使用済みなし」と扱わない。
        # 扱うと、公開済みの宣伝文をもう一度出す（2026-09-12 Sol指摘#5）
        raise PromoCheckFailed(str(e)[:120])


def pick_promo(salon_id=None):
    """未使用の宣伝文を1本返す。在庫が無ければ None（通常投稿にフォールバック）。"""
    pool = _load_json(PROMO_POOL_FILE, {})
    posts = pool.get("posts") or []
    image_url = pool.get("image_url") or ""
    used = set(_load_json(PROMO_USED_FILE, []))
    used_db = _promo_used_from_db(salon_id)
    for text in posts:
        if text in used or post_state.norm_text(text) in used_db:
            continue
        return {"text": text, "image_url": image_url}
    return None


def mark_promo_used(text):
    used = _load_json(PROMO_USED_FILE, [])
    if text not in used:
        used.append(text)
    with open(PROMO_USED_FILE, "w", encoding="utf-8") as f:
        json.dump(used, f, ensure_ascii=False, indent=2)


# 「未確定のまま終わった枠」は、時間帯ガードにも日付にも縛らずに回収する。
# ここが無いと「次の実行が解決します」が成立しない（op_idに日付が入るため、
# 翌日は別の行になって前日の未完行を誰も見ない・2026-09-12 Sol指摘#5）。
# スロットごとの想定JST時間帯（遅延した予備cronを弾く）。テストから差し替えられるよう外に出す
SLOT_JST_WINDOWS = {"morning": range(5, 11), "noon": range(11, 17), "evening": range(19, 24)}

RECOVER_DAYS = int(os.environ.get("RECOVER_DAYS", "3"))
RECOVER_MAX = int(os.environ.get("RECOVER_MAX", "5"))
# 回収に使ってよい実時間の上限。ここを設けないと、直らない古い枠の通信待ちだけで
# ジョブの制限時間を使い切り、当日の通常投稿が出せなくなる（2026-09-12 Sol指摘#9）
RECOVER_BUDGET_SEC = int(os.environ.get("RECOVER_BUDGET_SEC", "120"))


def _safe_fetch(op_id, fallback=None):
    """台帳の取り直し。失敗しても例外を外へ出さない
    （例外処理の中で落ちると、まとめ通知まで飛ばしてしまう・2026-09-12 Sol指摘#2）。"""
    try:
        return post_state.fetch(op_id) or fallback
    except Exception as e:
        print(f"[state] 取り直しに失敗（手元の情報で続行）: {str(e)[:60]}")
        return fallback


def _settle_if_notified(row, op_id):
    """すでに必要な知らせが届いていて、自動でできることが残っていない枠を人待ちへ移す。

    ⚠️ 通知の重複抑制と「人待ちへ移す」は別の判断。同じにすると、
    2回目以降は通知対象から外れるせいで永久に hold_repair のまま回り続け、
    毎回の回収枠と通信を使い続ける（2026-09-12 Sol指摘）。"""
    cur = _safe_fetch(op_id, row) or row
    if cur.get("status") != post_state.STATUS_HOLD_REPAIR:
        return False
    if _repairable(cur):
        return False
    if not (_notified_kinds(cur) & HUMAN_KINDS):
        return False
    try:
        post_state.update(cur, status=post_state.STATUS_ATTENTION)
        print(f"[recover] {op_id}: 知らせ済みで自動でできることも無い → 人待ちにします")
        return True
    except Exception as e:
        print(f"[state] 人待ちへの移行に失敗（続行）: {str(e)[:60]}")
        return False


def _add_failure(failures, row, op_id, kind, reason):
    """片づけられなかった枠を通知対象に積む。

    ⚠️ 同じ枠・**同じ種類**の失敗を毎回通知しない（回収は毎回走るので鳴り続ける）。
    識別は理由の文字列ではなく `kind`（失敗の種類）で行う。文字列の先頭だけで比べると、
    原因が変わったのに「同じ」と見なして知らせ損ねる（2026-09-12 Sol指摘#2）。
    通知済みの印は、**実際に送れてから**付ける（同#3）。ここでは積むだけ。"""
    if kind in _notified_kinds(row):
        print(f"[recover] {op_id}: 同じ種類（{kind}）は通知済み → 今回は積みません")
        return
    failures.append({"op_id": op_id, "kind": kind, "text": f"{op_id}（{reason}）"})


# 「人が見ないと先へ進めない」失敗の種類。まとめ通知が届いたら attention へ移す
HUMAN_KINDS = {"publisher_mismatch", "publisher_unknown", "payload_missing",
               "original_mismatch", "mismatch", "no_text", "account", "hold", "expired"}


def _repairable(row):
    """自動で記録を戻せる余地が、まだ残っているか。

    ⚠️ 通知が届いたからといって、記録の復旧まで諦めてはいけない。
    諦めると公開済み投稿が永久に記録されず、その本文がまた選ばれる
    （2026-09-12 Sol指摘#1）。"""
    if row.get("logged") and not _promo_pending(row):
        return False
    first = post_state.get_part(row, 0) or {}
    if first.get("expired"):
        return False          # コンテナが消えた＝もう確かめようがない
    if first.get("status") in (post_state.PART_CONTAINER, post_state.PART_UNKNOWN):
        return True           # まだ確かめる余地がある
    if first.get("status") != post_state.PART_PUBLISHED:
        return False
    # ⚠️ 公開済みでも、記録に必要な本文がそろっていなければ自動では戻せない。
    # 「公開済み」だけで判断すると、人の修正が要る行が回収枠を使い続ける
    # （2026-09-12 Sol指摘#2）
    payload = row.get("payload") or {}
    texts = payload.get("texts") or []
    text = payload.get("original_first") or (texts[0] if texts else None)
    if not text:
        return False
    # ⚠️ texts が欠けていても、残っている原文が両方のハッシュに一致するなら安全に戻せる
    # （2026-09-12 Sol指摘#1：欠損だけを理由に諦めると、公開済み本文が使用済みにならない）
    body = texts[0] if texts else text
    if first.get("hash") != post_state.part_hash(body):
        return False
    if _original_mismatch(row, text):
        return False
    return True


def _append_note(note, extra):
    """メモに説明を足す。⚠️「通知済み:」の印より**前**に足す。
    後ろに足すと印の一部として読まれ、同じ理由の通知が何度も飛ぶ（Sol指摘#3）。"""
    note = note or ""
    if RECOVER_NOTE_MARK in note:
        head, mark = note.split(RECOVER_NOTE_MARK, 1)
        return head + extra + RECOVER_NOTE_MARK + mark
    return note + extra


def _notified_kinds(row):
    """この枠で、これまでに知らせた失敗の種類。"""
    note = row.get("note") or ""
    if RECOVER_NOTE_MARK not in note:
        return set()
    return {k for k in note.split(RECOVER_NOTE_MARK, 1)[1].split(",") if k}


def _mark_notified(failures):
    """まとめ通知が**送れたあとに**、通知済みの印を台帳へ付ける。

    ⚠️ 種類は上書きせず**足していく**。上書きすると、原因が交互に変わるだけで
    同じことを何度も知らせてしまう（2026-09-12 Sol指摘#4）。"""
    for f in failures:
        try:
            cur = post_state.fetch(f["op_id"])
            if not cur:
                continue
            kinds = _notified_kinds(cur) | {f["kind"]}
            base = (cur.get("note") or "").split(RECOVER_NOTE_MARK)[0]
            fields = {"note": base + RECOVER_NOTE_MARK + ",".join(sorted(kinds))}
            # 知らせが届いたので、人の確認待ちへ移してよい（届くまでは回収対象に残す）
            if f["kind"] in HUMAN_KINDS \
                    and cur.get("status") == post_state.STATUS_HOLD_REPAIR \
                    and not _repairable(cur):
                fields["status"] = post_state.STATUS_ATTENTION
            post_state.update(cur, **fields)
        except Exception as e:
            print(f"[state] 通知済み印の保存に失敗（続行）: {str(e)[:60]}")


def _repair_safe(row, salon, slot, jst_date, finish_status=None, quiet=False):
    """記録の復旧。返り値は (kind, reason)。片づいたら (None, None)。
    トークン切れは「記録を戻せない」ではなく再連携が必要だと分かる形で返す。"""
    try:
        return _repair_log_only(row, salon, slot, jst_date,
                                finish_status=finish_status, quiet=quiet) or (None, None)
    except TokenExpiredError as e:
        print(f"[recover] トークン切れ（記録の復旧）: {e}")
        _state_finish(row, post_state.STATUS_HOLD_REPAIR, note="記録の復旧中にトークン切れ")
        if not quiet:
            _notify_line(f"🔑 とうこさん：{salon['salon_name']} の {jst_date} {slot} の記録を"
                         "戻そうとしましたが、Threadsとの連携が切れています。再連携が必要です。")
        return "token", "Threadsとの連携が切れています（再連携が必要）"
    return None, None


def _promo_pending(row):
    """宣伝枠なのに「使用済み」の記録がまだ済んでいないか。

    ⚠️ 投稿ログと宣伝の使用済みは別物。片方だけで完了にすると、
    公開済みの宣伝文が在庫に残って再び選ばれる（2026-09-12 Sol指摘#4）。"""
    payload = row.get("payload") or {}
    return bool(payload.get("promo")) and not payload.get("promo_used")


def _mark_promo_done(row, text):
    """宣伝の使用済みを記録し、台帳にも印を残す。成功したら True。

    ⚠️ 入口で原文を照合する。呼び出し経路が複数あるので、ここに集約しないと
    未投稿の宣伝文を使用済みにできてしまう（2026-09-12 Sol指摘#4）。"""
    bad = _original_mismatch(row, text)
    if bad:
        print(f"[promo] 使用済みにしません: {bad}")
        return False
    try:
        mark_promo_used(text)
    except Exception as e:
        print(f"[promo] 在庫の使用済み記録に失敗: {str(e)[:80]}")
        return False
    try:
        pl = dict(row.get("payload") or {})
        pl["promo_used"] = True
        post_state.update(row, payload=pl)
    except Exception as e:
        print(f"[state] 宣伝の使用済み印の保存に失敗: {str(e)[:80]}")
        return False
    return True


def _all_parts_published(row):
    """payload の全パートが、**同じ本文で**公開済みになっているか。

    ⚠️ 部数だけ見ると、台帳と payload が食い違ったときに
    「公開していない別の本文」を記録してしまう（2026-09-12 Sol指摘#3）。
    部数の一致・添字の一意と連続・本文ハッシュの一致まで確認する。"""
    texts = ((row.get("payload") or {}).get("texts")) or []
    parts = row.get("parts") or []
    if not texts or len(parts) != len(texts):
        return False
    idx = [p.get("i") for p in parts]
    if sorted(idx) != list(range(len(texts))):
        return False
    by_i = {p.get("i"): p for p in parts}
    for n, t in enumerate(texts):
        p = by_i[n]
        if p.get("status") != post_state.PART_PUBLISHED:
            return False
        # ⚠️ ハッシュが無い＝どの本文を出したのか分からない。一致とみなさない
        if p.get("hash") != post_state.part_hash(t):
            return False
    return True


def _repair_log_only(row, salon, slot, jst_date, finish_status=None, quiet=False):
    """（トークン切れは呼び出し側で受けて、再連携の通知に回す）"""
    """公開は済んでいるのに post_logs に記録が無い枠を、**投稿せずに**記録だけ戻す。

    「続きを出さない（attention）」と「記録が無い」は別の問題。前者で後者まで止めると、
    公開済みの投稿が永久に記録されず、使用済み判定も集計も狂う（2026-09-12 Sol指摘#4）。"""
    payload = row.get("payload") or {}
    first = post_state.get_part(row, 0) or {}
    text = payload.get("original_first") or (payload.get("texts") or [None])[0]
    st0 = first.get("status")
    if st0 in (post_state.PART_CONTAINER, post_state.PART_UNKNOWN):
        # 公開できたか未確定。**投稿はせず**、コンテナの状態だけ聞いて確定させる
        # （公開済みと分かれば記録だけ戻せる・2026-09-12 Sol指摘#2）
        st = _container_status(first.get("creation_id"), salon["access_token"])
        print(f"[recover] {salon['salon_name']} {jst_date} {slot}: "
              f"1部目の公開が未確定 → コンテナ状態={st or '不明'}")
        if st == "PUBLISHED":
            try:
                row = post_state.set_part(row, 0, status=post_state.PART_PUBLISHED)
                first = post_state.get_part(row, 0) or first
                st0 = post_state.PART_PUBLISHED
            except Exception as e:
                print(f"[state] 公開確定の保存に失敗: {str(e)[:80]}")
                _state_finish(row, post_state.STATUS_HOLD_REPAIR, note=row.get("note"))
                return "state", "公開の確定を保存できませんでした"
        elif st in ("EXPIRED", "ERROR"):
            # コンテナが消えた＝これ以上は何を待っても分からない。人に渡す
            try:
                row = post_state.set_part(row, 0, expired=True)
            except Exception as e:
                print(f"[state] 期限切れ印の保存に失敗: {str(e)[:60]}")
            _state_finish(row, post_state.STATUS_HOLD_REPAIR,
                          note=f"コンテナが{st}のため、公開できたか確認できません（人の確認が必要）")
            return "expired", f"コンテナが{st}で、公開できたか確認できません"
        else:
            # まだ分からない。記録対象に残したまま次回また確認する
            _state_finish(row, post_state.STATUS_HOLD_REPAIR, note=row.get("note"))
            return "unknown", "公開できたかまだ確認できていません"
    if st0 == post_state.PART_PUBLISHED and text and (
            first.get("hash") != post_state.part_hash((payload.get("texts") or [text])[0])
            or _original_mismatch(row, text)):
        # ⚠️ 公開したのは別の本文。ここで payload の本文を記録すると、
        # 出していない本文が「使用済み」になり、出した本文は使用済みにならない
        # （2026-09-12 Sol指摘#1）
        _to_human(row, "公開した本文と台帳の本文が食い違うため記録できません（人の確認が必要）",
                  f"🚨 とうこさん：{salon['salon_name']} の {jst_date} {slot} は、"
                  "公開した本文と台帳の本文が食い違います。記録を戻せないので確認してください。",
                  quiet=quiet)
        return "mismatch", "公開した本文と台帳の本文が食い違います（人の確認が必要）"
    if st0 == post_state.PART_PUBLISHED and not text:
        # ⚠️「本文を復元できない」と「公開していない」は別（2026-09-12 Sol指摘#4）。
        # 公開済みなら記録対象から外さず、人に渡す
        _to_human(row, "公開済みだが本文を復元できず、記録を戻せません（人の確認が必要）",
                  f"🚨 とうこさん：{salon['salon_name']} の {jst_date} {slot} は公開済みですが、"
                  "本文を復元できず記録を戻せません。確認してください。", quiet=quiet)
        return "no_text", "公開済みだが本文を復元できず、記録を戻せません"
    if st0 != post_state.PART_PUBLISHED or not text:
        # 公開していないことが分かっている＝自動でできることは無い。
        # logged を立てて回収対象から外す（毎回この行で枠を使い潰さないため・Sol指摘#6）
        try:
            post_state.update(row, logged=True, status=post_state.STATUS_HOLD_REPAIR,
                              note=_append_note(row.get("note"),
                                                "／記録すべき公開投稿なし（人の確認待ち）"))
        except Exception as e:
            print(f"[state] 人待ち印の保存に失敗: {str(e)[:80]}")
        return "no_text", "記録すべき公開投稿が見つかりません（人の確認が必要）"
    try:
        log_post_with_retry(salon["id"], slot, text, row["op_id"],
                            posted_at=first.get("published_ts") or first.get("created_ts"))
        print(f"[recover] {salon['salon_name']} {jst_date} {slot} の記録だけ戻しました（投稿はしていません）")
        if _promo_pending(row):
            # 宣伝枠は「使用済み」の記録も戻さないと、同じ宣伝文がまた選ばれる
            if not _mark_promo_done(row, text):
                # ⚠️ 元が「人待ち」なら人待ちのまま。記録の失敗で投稿の停止を解除しない
                # （2026-09-12 Sol指摘#2）
                keep = (post_state.STATUS_HOLD_REPAIR
                        if (row.get("status") in (post_state.STATUS_ATTENTION,
                                                  post_state.STATUS_HOLD_REPAIR)
                            or finish_status is None)
                        else post_state.STATUS_PUBLISHED)
                _state_finish(post_state.fetch(row["op_id"]) or row, keep,
                              note="宣伝の使用済み記録を戻せていません")
                return "promo", "宣伝の使用済み記録を戻せていません"
            # 台帳を書き換えたので手元の行は古い。取り直さないと次の更新が弾かれる
            row = post_state.fetch(row["op_id"]) or row
        try:
            # ⚠️ finish_status が無い＝「投稿は止めたまま記録だけ戻した」ケース。
            # ここで attention にすると、まだ何も知らせていないのに回収対象から外れる
            # （2026-09-12 Sol指摘#1）。知らせが届いてから移す（_mark_notified）
            post_state.update(row, logged=True,
                              status=finish_status or post_state.STATUS_HOLD_REPAIR,
                              note=None if finish_status
                              else _append_note(row.get("note"), "／記録は復旧済み"))
        except Exception as e:
            print(f"[state] 記録済み印の保存に失敗: {str(e)[:80]}")
        if not finish_status:
            return "hold", "記録は戻しましたが、続きは自動で出せません（人の確認が必要）"
    except Exception as e:
        # ⚠️ 失敗したのに finish_status（logged）を採用しない。採用すると
        # 記録が無いまま完了扱いになり、二度と回収されない（2026-09-12 Sol指摘#2）
        print(f"[recover] 記録の復旧に失敗: {str(e)[:100]}")
        _state_finish(row,
                      post_state.STATUS_PUBLISHED if finish_status
                      else post_state.STATUS_HOLD_REPAIR,
                      note=f"記録の復旧に失敗: {str(e)[:120]}")
        return "log", f"記録の復旧に失敗: {str(e)[:60]}"
    return None, None


def recover_open_attempts(salons, skip_op_ids=()):
    """公開未確定・記録未完のまま残った枠を、元の本文・元のコンテナで片づける。
    新規投稿はしない（新しい本文を選ばない）。返り値は処理した件数。"""
    global _deadline, _retry_spent, _quiet_notify
    failures = []                    # 片づけられなかった枠（最後にまとめて通知する）
    by_id = {s["id"]: s for s in salons}
    started = time.time()
    # 回収は「当日の投稿より先」に走る。待機予算も締切も通常投稿と分けて持ち、
    # 終わったら必ず元に戻す（回収の消費で当日の再試行余力を削らない・Sol指摘#6）
    saved_spent, _retry_spent = _retry_spent, 0.0
    _quiet_notify = True
    _deadline = started + RECOVER_BUDGET_SEC
    try:
        # 稼働中サロンで先に絞る。絞らないと停止済みサロンの古い行だけで上限に達する
        rows = post_state.open_issues(since_days=RECOVER_DAYS, salon_ids=list(by_id))
    except Exception as e:
        # 黙って続行しない。取りこぼしの回収そのものが動いていないことに気づけなくなる
        print(f"[recover] 未完の枠の取得に失敗: {str(e)[:150]}")
        _notify_line("🚨 とうこさん：未完の投稿枠を調べられませんでした。\n"
                     "取りこぼしの自動回収が動いていません。\n"
                     f"{type(e).__name__}: {str(e)[:150]}")
        _deadline, _retry_spent = None, saved_spent
        _quiet_notify = False
        return 0

    try:
        done = 0
        for r in rows:
            if done >= RECOVER_MAX:
                print(f"[recover] 今回はここまで（残り{len(rows) - done}件は次の実行で）")
                break
            if _out_of_time("回収"):
                print("[recover] 回収の持ち時間を使い切りました → 残りは次の実行で（通常投稿を優先）")
                break
            op_id = r.get("op_id")
            if op_id in skip_op_ids:
                continue
            # ⚠️ 1行の失敗が残り全部の回収を止めないよう、行ごとに例外を閉じ込める
            # （2026-09-12 Sol指摘#4：確認通信1件の失敗で2件目が無通知で止まった）
            try:
                salon = by_id.get(r.get("salon_id"))
                if not salon:
                    continue   # 停止済みサロン。触らない
                slot, jst_date = r.get("slot"), str(r.get("jst_date"))
                try:
                    action, row = post_state.acquire(salon["id"], jst_date, slot, allow_attention=True)
                except Exception as e:
                    print(f"[recover] {op_id} の実行権が取れず（次回へ）: {str(e)[:100]}")
                    _add_failure(failures, r, op_id, "acquire",
                                 f"実行権が取れない: {str(e)[:60]}")
                    continue
                if action in ("hold", "skip"):
                    continue
                if action == "go":
                    # 何も公開していない過去の枠。今さら新しく出さない（時間帯が違う）
                    _state_finish(row, post_state.STATUS_FAILED, note="未公開のまま期限切れ（回収時に確認）")
                    continue
                if action == "repair":
                    # 停止状態(attention)だが記録だけが無い枠。**投稿は一切しない**で記録だけ戻す
                    done += 1
                    kind, reason = _repair_safe(row, salon, slot, jst_date, quiet=True)
                    after = _safe_fetch(op_id, row) or row
                    if _settle_if_notified(after, op_id):
                        continue
                    if kind or not after.get("logged") or _promo_pending(after):
                        _add_failure(failures, after, op_id, kind or "log",
                                     reason or ("宣伝の使用済み記録が残っています"
                                                if _promo_pending(after) else "記録を戻せない"))
                    continue
                if action == "resume" and _all_parts_published(row):
                    # 全パート公開済み＝投稿は起きない。/me に巻き込まれないよう先に片づける
                    done += 1
                    if row.get("logged") and _promo_pending(row):
                        # 記録は済んでいるが宣伝の使用済みが残っている
                        pl = row.get("payload") or {}
                        if _mark_promo_done(row, pl.get("original_first") or "") \
                                and _state_finish(_safe_fetch(op_id, row),
                                                  post_state.STATUS_LOGGED):
                            _sync_last_run(salon["salon_name"], slot, jst_date=jst_date)
                        else:
                            _add_failure(failures, _safe_fetch(op_id, row), op_id,
                                         "promo", "宣伝の使用済み記録または完了印を残せない")
                        continue
                    if row.get("logged"):
                        print(f"[recover] {salon['salon_name']} {jst_date} {slot}: 全公開・記録済み → 完了")
                        if _state_finish(row, post_state.STATUS_LOGGED):
                            _sync_last_run(salon["salon_name"], slot, jst_date=jst_date)
                        else:
                            _add_failure(failures, _safe_fetch(op_id, row), op_id,
                                         "state", "完了の印を台帳に残せませんでした")
                        continue
                    print(f"[recover] {salon['salon_name']} {jst_date} {slot}: 記録だけ戻します")
                    kind, reason = _repair_safe(row, salon, slot, jst_date, quiet=True,
                                                finish_status=post_state.STATUS_LOGGED)
                    after = _safe_fetch(op_id, row) or row
                    if not kind and after.get("logged") and not _promo_pending(after):
                        _sync_last_run(salon["salon_name"], slot, jst_date=jst_date)
                    else:
                        _add_failure(failures, after, op_id, kind or "log",
                                     reason or ("宣伝の使用済み記録が残っています"
                                                if _promo_pending(after) else "記録を戻せない"))
                    continue

                done += 1
                print(f"[recover] {salon['salon_name']} {jst_date} {slot} の続きを片づけます（{r.get('status')}）")
                token = salon["access_token"]
                user_id = uname = None
                try:
                    user_id, uname = get_user_id_from_token(token)
                except TokenExpiredError:
                    print(f"[recover] トークン切れ: {op_id}")
                    _state_finish(row, r.get("status") or post_state.STATUS_UNKNOWN,
                                  note="回収時にトークン切れ")
                    _add_failure(failures, row, op_id, "token",
                                 "Threadsとの連携が切れています（再連携が必要）")
                    continue
                except Exception as e:
                    # 元の状態へ戻す（updated_at が進むので、次は他の行が先に回る）
                    print(f"[recover] /me 失敗（この枠は次回へ）: {str(e)[:80]}")
                    _state_finish(row, r.get("status") or post_state.STATUS_UNKNOWN,
                                  note=f"回収時に /me 失敗: {str(e)[:100]}")
                    _add_failure(failures, row, op_id, "me", f"/me に失敗: {str(e)[:60]}")
                    continue
                label = f"@{uname}" if uname else salon["salon_name"]
                # ⚠️ 通常経路と同じ照合を、例外を握る try の外で行う（Sol指摘#1）。
                # 登録アカウントと実アカウントが違うまま回収すると、別アカウントへ投稿してしまう
                if salon.get("threads_user_id") and str(salon["threads_user_id"]) != str(user_id):
                    msg = (f"登録アカウント({salon['threads_user_id']})とトークンの実アカウント"
                           f"({user_id})が一致しません")
                    print(f"[recover] {msg} → 回収しません")
                    _state_finish(row, post_state.STATUS_HOLD_REPAIR, note=msg)
                    _add_failure(failures, row, op_id, "account", msg)
                    continue
                try:
                    status, detail, kind = _run_slot(row, "resume", salon, user_id, token,
                                                     slot, label, quiet=True)
                    if status == "ok":
                        print(f"[recover] {salon['salon_name']} {jst_date} {slot} を完了しました")
                    else:
                        print(f"[recover] {salon['salon_name']} {jst_date} {slot} は未完のまま: {detail}")
                        # ⚠️ 投稿を止める理由があっても、**公開済みの記録は戻す**。
                        # 戻さないと、その本文が使用済みにならず後日また選ばれる
                        # （2026-09-12 Sol指摘#1）
                        cur = _safe_fetch(op_id, row) or row
                        if _repairable(cur):
                            _repair_safe(cur, salon, slot, jst_date, quiet=True)
                            cur = _safe_fetch(op_id, cur) or cur
                        if _settle_if_notified(cur, op_id):
                            continue
                        _add_failure(failures, cur, op_id,
                                     kind or "incomplete", str(detail)[:60])
                except TokenExpiredError:
                    print(f"[recover] トークン切れ: {op_id}")
                    _state_finish(row, post_state.STATUS_UNKNOWN, note="トークン切れで回収できず")
                    _add_failure(failures, row, op_id, "token",
                                 "Threadsとの連携が切れています（再連携が必要）")
                except Exception as e:
                    print(f"[recover] 回収に失敗: {str(e)[:120]}")
                    _state_finish(row, post_state.STATUS_HOLD_REPAIR,
                                  note=f"回収に失敗: {str(e)[:150]}")
                    _add_failure(failures, row, op_id, f"error:{type(e).__name__}",
                                 f"回収に失敗: {str(e)[:60]}")
            except Exception as e:
                print(f"[recover] {op_id} の処理で想定外のエラー: {str(e)[:150]}")
                _add_failure(failures, _safe_fetch(op_id, r), op_id,
                             f"error:{type(e).__name__}",
                             f"想定外のエラー: {type(e).__name__}: {str(e)[:50]}")
                continue
    finally:
        # ⚠️ 例外が抜けても必ず戻す。戻らないと当日の通常投稿が
        # 回収用の締切で片っ端から打ち切られる
        _deadline, _retry_spent = None, saved_spent
        _quiet_notify = False

    # ⚠️ 失敗をログだけに残さない。回収が効いていないことに誰も気づけなくなる
    # （2026-09-12 Sol指摘#3）。1実行1通にまとめる
    if failures:
        shown = failures[:5]
        sent = _notify_line(
            "⚠️ とうこさん：片づけられなかった投稿枠があります（自動での再投稿はしません）。\n"
            + "\n".join(f"・{x['text']}" for x in shown)
            + (f"\nほか{len(failures) - 5}件（次の実行でお知らせします）"
               if len(failures) > 5 else ""))
        if sent:
            # ⚠️ 本文に載せた分だけ通知済みにする。載せていない行まで印を付けると、
            # その枠の原因を永久に知らせない（2026-09-12 Sol指摘#3）
            _mark_notified(shown)
        else:
            print("[recover] まとめ通知を送れませんでした → 通知済みにせず次回へ残します")
    return done


def _run_slot(row, action, salon, user_id, token, slot, account_label, quiet=False):
    """1枠を最後まで進める。通常の実行からも、過去の未完行の回収からも同じ道を通る。
    返り値 ("ok"|"error", 説明)。"""
    salon_id = salon["id"]
    salon_name = salon["salon_name"]

    # ⚠️ 「投稿を始めたときのアカウント」を台帳に固定する。再連携でサロンの登録IDと
    # トークンを両方入れ替えると、登録＝トークンの照合は通ってしまい、
    # 旧アカウントの親に新アカウントから返信できてしまう（2026-09-12 Sol指摘#1）
    owner = row.get("publisher_user_id")
    if owner and str(owner) != str(user_id):
        msg = (f"この枠は別のアカウント({owner})で始まっています。"
               f"今のアカウントは {user_id} です")
        _to_human(row, msg,
                  f"🚨 とうこさん：{salon_name} の {slot} を続けようとしましたが、{msg}。\n"
                  "別のアカウントへ投稿しないよう止めました。", quiet=quiet)
        return "error", msg, "publisher_mismatch"
    if not owner:
        # ⚠️ すでに公開やコンテナ作成の履歴がある行に、今のアカウントを後付けしない。
        # 後付けすると、再連携後のアカウントで旧枠の続きを出せてしまう（Sol指摘#1）
        if any((p.get("creation_id") or p.get("post_id")
                or p.get("status") in (post_state.PART_CONTAINER, post_state.PART_UNKNOWN,
                                       post_state.PART_PUBLISHED))
               for p in (row.get("parts") or [])):
            msg = "この枠は投稿を始めた記録があるのに、どのアカウントで始めたか分かりません"
            _to_human(row, msg,
                      f"🚨 とうこさん：{salon_name} の {slot} を続けようとしましたが、{msg}。\n"
                      "別のアカウントへ投稿しないよう止めました。", quiet=quiet)
            return "error", msg, "publisher_unknown"
        try:
            row = post_state.update(row, publisher_user_id=str(user_id))
        except Exception as e:
            print(f"[state] 投稿アカウントの記録に失敗: {str(e)[:80]}")
            raise

    promo_fallback = None      # 宣伝を通常投稿に落とした理由（出せてから知らせる）
    payload = (row.get("payload") or {}) if action == "resume" else {}
    if payload.get("texts"):
        # 前回の続き。**同じ本文**でなければ台帳のコンテナと対応が取れない
        texts = payload["texts"]
        original_first = payload.get("original_first") or texts[0]
        image_url = payload.get("image_url") or ""
        topic_tag = payload.get("topic_tag")
        promo = {"text": original_first} if payload.get("promo") else None
        print(f"[RESUME] {salon_name}: {slot} 前回の続きから（{len(texts)}部）")
    elif action == "resume":
        # 続きのはずなのに本文が残っていない。何を出したか分からないので触らない
        _to_human(row, "payload が無く、何を投稿すべきか復元できません",
                  f"🚨 とうこさん：{salon_name} の {slot} が途中で止まっていますが、"
                  "何を投稿すべきかの記録が残っておらず再開できません。\n"
                  "二重投稿を避けるため自動での投稿はしません。", quiet=quiet)
        return "error", "前回の本文が台帳に残っておらず再開できません", "payload_missing"
    else:
        used = get_used_posts(salon_id, slot)
        promo = None
        if is_promo_time(salon_name, slot):
            try:
                promo = pick_promo(salon_id)
            except PromoCheckFailed as e:
                # ⚠️「確認できなかった」と「在庫が空」は別。まとめて在庫切れと言わない
                # （2026-09-12 Sol指摘#5）
                promo = None
                print(f"[promo] 使用済みを確認できないため通常投稿にします: {e}")
                promo_fallback = ("どれを出したか確認できなかったので、"
                                  f"同じ文が二度出ないよう通常の投稿にしました。\n{str(e)[:120]}")
            if promo:
                print(f"[promo] {salon_name}: 月曜夜の宣伝枠として画像付きで投稿します")
            elif promo_fallback is None:
                print(f"[promo] {salon_name}: 宣伝文の在庫が空 → 通常投稿にフォールバック")
                promo_fallback = ("文章の在庫が空だったため、通常の投稿を出しました。"
                                  "promo_posts_personal.json を確認してください。")
        texts = [promo["text"]] if promo else pick_post(salon_name, slot, used)
        # 使用済み判定はプール原文と突合するため、CTA付与・分割前の原文を控えておく
        original_first = texts[0] if isinstance(texts, list) else texts
        # 宣伝文はCTA（LINE誘導）を本文に含んだ完成品。IG CTAもトピックも付けない
        image_url = promo["image_url"] if promo else ""
        if not promo:
            texts = _maybe_add_instagram_cta_saas(texts, salon.get("instagram_url") or "")
        texts = _enforce_threads_limit(texts)  # 安全網：500字超は自動でツリー分割
        topic_tag = None if promo else _select_topic(texts, salon_name)
        # ⚠️ 投稿の前に本文を台帳へ残す。残さないと、途中で止まったとき
        # 次の実行が別の本文を選んでしまい、公開済みのパートと対応が取れなくなる
        # ⚠️ 新しく選び直した本文で始めるので、前回の失敗で残ったパートの記録は捨てる。
        # 残すと、原文の固定（original_hash）が前回の本文のままになり、
        # 今回公開した本文を記録できなくなる（2026-09-12 Sol指摘#1）。
        # ここへ来るのは「何も公開していない」と確定した枠だけなので、捨てて安全。
        row = post_state.update(row, parts=[], payload={
            "texts": texts, "original_first": original_first,
            "topic_tag": topic_tag, "image_url": image_url, "promo": bool(promo)})

    res = threads_post(row, user_id, token, texts, topic_tag=topic_tag, image_url=image_url,
                       original_first=original_first)
    row = res["row"]
    post_id = res["first_post_id"]

    # 投稿はここで成功済み。以降の保存が失敗しても投稿は取り消せないので、
    # 「次回の二重投稿を防ぐ記録(post_logs)」を最優先で確実に残す。
    logged = bool(row.get("logged"))
    if res["root_published"] and not logged:
        bad_original = _original_mismatch(row, original_first)
        if bad_original:
            print(f"[log_post] 記録しません: {bad_original}")
            _to_human(row, bad_original,
                      f"🚨 とうこさん：{salon_name} の {slot} は投稿できましたが、"
                      f"{bad_original}。記録を戻せないので確認してください。", quiet=quiet)
            return "error", bad_original, "original_mismatch"
        try:
            # CTA付与後の本文を記録すると get_used_posts との突合が永遠に外れ、
            # 同じ投稿が数日内に再選択されるため、必ず加工前の原文を記録する
            first = post_state.get_part(row, 0) or {}
            log_post_with_retry(salon_id, slot, original_first, row["op_id"],
                                posted_at=first.get("published_ts") or first.get("created_ts"))
            logged = True
            try:
                row = post_state.update(row, logged=True)
            except Exception as e:
                print(f"[state] 記録済み印の保存に失敗（続行）: {str(e)[:80]}")
        except Exception as e:
            print(f"[log_post] 記録失敗（投稿自体は成功済み）: {e}")
            if not quiet:
                _notify_line(
                    f"🚨 {salon_name} の {slot} 投稿は成功しましたが、投稿記録の保存に5回とも失敗しました。\n"
                    f"台帳には「公開済み・記録未完」として残したので、次の実行は再投稿せず記録だけをやり直します。\n"
                    f"{type(e).__name__}: {str(e)[:150]}")

    # 宣伝在庫の消費記録（失敗しても投稿記録は済んでいるので二重投稿にはならない）
    promo_ok = True
    if promo and res["complete"] and not _original_mismatch(row, original_first):
        if _promo_pending(row):
            promo_ok = _mark_promo_done(row, original_first)
            row = post_state.fetch(row["op_id"]) or row

    # ── 台帳に結末を残す ───────────────────────────────
    if res["complete"] and logged and not _promo_pending(row):
        # 通常投稿に落とした理由は、実際に出せてから知らせる（Sol指摘#5）
        if promo_fallback and not quiet:
            _notify_line(f"⚠️ 月曜夜の宣伝投稿：{promo_fallback}")
        if not _state_finish(row, post_state.STATUS_LOGGED):
            # ⚠️ 台帳に「完了」を残せていないのに成功と言わない（Sol指摘#2）。
            # 投稿と記録は済んでいるので、次の実行は再投稿せず完了印だけ付け直す
            print(f"[OK?] {salon_name}: 投稿と記録は済みましたが完了印を残せませんでした")
            return "error", "完了の印を台帳に残せませんでした（投稿と記録は完了）", "state"
        _sync_last_run(salon_name, slot, jst_date=row.get("jst_date"))
        print(f"[OK] {salon_name}: post_id={post_id}")
        return "ok", None, None

    detail = (res["note"] or ("宣伝の使用済み記録に失敗（投稿自体は公開済み）"
                              if logged and not promo_ok
                              else "投稿記録の保存に失敗（投稿自体は公開済み）"))
    print(f"[INCOMPLETE] {salon_name}: {detail}")
    if res["slot_status"] == post_state.STATUS_ATTENTION:
        # 人の確認が要る停止は、知らせが届いてから attention にする
        _to_human(row, detail,
                  f"⚠️ とうこさん 投稿が途中で止まりました\n\n"
                  f"アカウント：{account_label}\nスロット：{slot}\n{detail}\n\n"
                  "人が確認するまで自動では触りません。", quiet=quiet)
    else:
        _state_finish(row, res["slot_status"], note=detail)
        if res["note"] and not quiet:
            _notify_line(f"⚠️ とうこさん 投稿が途中で止まりました\n\n"
                         f"アカウント：{account_label}\nスロット：{slot}\n{detail}\n\n"
                         "二重投稿を避けるため、次の実行は同じ続きから再開します。")
    # 記録は済ませたうえで、トークン切れだけは呼び出し側に伝える（専用通知のため）
    if isinstance(res.get("error"), TokenExpiredError):
        raise res["error"]
    kind = "incomplete"
    if res["slot_status"] == post_state.STATUS_ATTENTION:
        kind = "hold"
    elif res["slot_status"] == post_state.STATUS_HOLD_REPAIR:
        # 「続きは自動で出せない」への変化は、別の知らせとして扱う（Sol指摘#4）
        kind = "hold_repair"
    return "error", detail, kind


# 取りこぼしの点検で見る日数（今日を含む）。1回のまとめ問い合わせで調べる
GAP_SCAN_DAYS = int(os.environ.get("GAP_SCAN_DAYS", "2"))
# 点検が最後まで終わった日を覚えておくファイル。終わっていない日は、
# 次の実行で遡って見直す（2026-09-12 Sol指摘#2）
GAP_MARK_FILE = os.path.join(os.path.dirname(__file__), "gap_checked.json")
GAP_SCAN_MAX_DAYS = int(os.environ.get("GAP_SCAN_MAX_DAYS", "7"))
# 取りこぼしの点検＋穴埋めに使ってよい実時間
GAPFILL_BUDGET_SEC = int(os.environ.get("GAPFILL_BUDGET_SEC", "150"))
ALL_SLOTS = ("morning", "noon", "evening")
# その日の、いま実行しているスロットより前にあるスロット（古い順）
EARLIER_SLOTS = {"morning": [], "noon": ["morning"], "evening": ["morning", "noon"]}


def _flag_missing(salon, jst_date, slot, note):
    """「出ていない」「確認できなかった」枠を台帳に残す。成功したら True。

    ⚠️ その場の通知1通で終わらせない。送れなければ二度と知らせる機会が無い。
    hold_repair にしておけば、回収の仕組みが**届くまで**繰り返し知らせる。
    ⚠️ 元のメモ（過去の失敗理由・通知済みの印）は消さずに残す。"""
    if _out_of_time("取りこぼしの記録"):
        return False
    op_id = post_state.make_op_id(salon["id"], jst_date, slot)
    try:
        row = post_state.fetch(op_id)
        if row is None:
            post_state._req("POST", post_state.TABLE, body={
                "op_id": op_id, "salon_id": salon["id"], "jst_date": jst_date,
                "slot": slot, "status": post_state.STATUS_HOLD_REPAIR, "parts": [],
                "rev": 0, "note": note})
            return True
        if row.get("status") == post_state.STATUS_FAILED:
            # 未公開が確定している行。履歴も過去の理由も残したまま、知らせる対象へ移す
            post_state.update(row, status=post_state.STATUS_HOLD_REPAIR,
                              note=_append_note(row.get("note"), "／" + note))
            return True
        return True     # すでに回収対象（unknown / published / hold_repair など）
    except Exception as e:
        print(f"[gap] 未投稿の印を残せません（{salon['salon_name']} {jst_date} {slot}）: {str(e)[:80]}")
        return False


def _scan_gaps(salons, days):
    """過去 days 日分の「出ていない枠」を **まとめて1回ずつの問い合わせ**で洗い出す。

    ⚠️ サロン×スロットごとに問い合わせると、件数が増えたときに
    ジョブの制限時間を食い破る（2026-09-12 Sol指摘#2）。
    返り値は [(salon, jst_date, slot)]。調べられなければ例外を投げる（＝黙って「なし」にしない）。"""
    base = datetime.strptime(_run_jst_date or datetime.now(JST).strftime("%Y-%m-%d"), "%Y-%m-%d")
    dates = [(base - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(days)]
    since_utc = (base - timedelta(days=days)).replace(tzinfo=JST).astimezone(
        timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = [s["id"] for s in salons]

    logs, page, offset = [], 1000, 0
    while True:
        chunk = supabase_get("post_logs", {
            "select": "salon_id,slot,posted_at,op_id",
            "salon_id": "in.(" + ",".join(ids) + ")",
            "posted_at": f"gte.{since_utc}",
            "order": "posted_at.asc", "limit": str(page), "offset": str(offset)})
        logs += chunk
        if len(chunk) < page:
            break
        offset += page
        if offset > 20000:      # 想定外の件数。取り切れないなら判断しない
            raise RuntimeError("投稿記録が多すぎて全部を確認できません")
    done = set()
    for r in logs:
        if r.get("op_id"):
            done.add(r["op_id"])
            continue
        d = _parse_ts(r.get("posted_at"))          # 旧形式のログは日付から割り出す
        if d is not None:
            done.add(post_state.make_op_id(r["salon_id"],
                                           d.astimezone(JST).strftime("%Y-%m-%d"), r["slot"]))

    rows = supabase_get("post_attempts", {
        "select": "op_id,status", "salon_id": "in.(" + ",".join(ids) + ")",
        "jst_date": f"gte.{dates[-1]}", "limit": "2000"})
    settled = {r["op_id"] for r in rows
               if r.get("status") in (post_state.STATUS_LOGGED, post_state.STATUS_ATTENTION)}
    known = {r["op_id"] for r in rows}

    today = base.strftime("%Y-%m-%d")
    gaps = []
    for salon in salons:
        started = _parse_ts(salon.get("created_at"))
        start_day = started.astimezone(JST).strftime("%Y-%m-%d") if started else None
        for d in dates:
            if start_day and d < start_day:
                continue        # このサロンが始まる前の日は「未投稿」ではない
            for slot in ALL_SLOTS:
                if d == today and slot not in EARLIER_SLOTS.get(SLOT, []) and slot != SLOT:
                    continue        # 今日のまだ来ていない枠は対象外
                if d == today and slot == SLOT:
                    continue        # 今回の枠は本編が担当
                op_id = post_state.make_op_id(salon["id"], d, slot)
                if op_id in done or op_id in settled:
                    continue
                gaps.append((salon, d, slot, op_id in known))
    return gaps


def check_previous_slot(salons):
    """出ていない枠を洗い出し、**その日のうちなら埋める・過ぎていれば知らせる**。

    確実に動くのは cron-job.org の 7:10 / 12:10 / 21:10 の3回だけ
    （GitHubの定期実行は実測で2〜4時間半遅れる）。その各回で点検する。
    台帳があるので、埋め直しても二重投稿にならない。"""
    global _deadline
    saved_deadline = _deadline
    _deadline = time.time() + GAPFILL_BUDGET_SEC
    try:
        return _check_gaps(salons)
    finally:
        _deadline = saved_deadline


def _gap_days_to_scan():
    """今回さかのぼって見る日数。前回やり残した日があれば、そこまで戻る。"""
    today = _run_jst_date or datetime.now(JST).strftime("%Y-%m-%d")
    last = (_load_json(GAP_MARK_FILE, {}) or {}).get("checked_through")
    days = GAP_SCAN_DAYS
    if last:
        try:
            gap = (datetime.strptime(today, "%Y-%m-%d")
                   - datetime.strptime(last, "%Y-%m-%d")).days
            days = max(days, min(gap + 1, GAP_SCAN_MAX_DAYS))
        except Exception:
            pass
    return days


def _mark_gap_checked(through):
    try:
        with open(GAP_MARK_FILE, "w", encoding="utf-8") as f:
            json.dump({"checked_through": through}, f, ensure_ascii=False)
    except Exception as e:
        print(f"[gap] 点検済みの記録に失敗（続行）: {str(e)[:60]}")


def _check_gaps(salons):
    today = _run_jst_date or datetime.now(JST).strftime("%Y-%m-%d")
    try:
        gaps = _scan_gaps(salons, _gap_days_to_scan())
    except Exception as e:
        # ⚠️「調べられなかった」を「抜けなし」と言わない
        print(f"[gap] 取りこぼしを調べられません: {str(e)[:120]}")
        _notify_line("⚠️ とうこさん：投稿の取りこぼしを調べられませんでした。\n"
                     f"{type(e).__name__}: {str(e)[:150]}\n次の実行でもう一度調べます。")
        raise
    if not gaps:
        print("[gap] 取りこぼしはありません")
        _mark_gap_checked(today)
        return 0

    fillable = [g for g in gaps if g[1] == today]
    old = [g for g in gaps if g[1] != today]
    filled, failed, unflagged = [], [], []

    # 今日の抜けは埋める（同じ日のうちなら出してよい）
    for salon, d, slot, _known in fillable:
        if _out_of_time("取りこぼしの穴埋め") or _date_rolled_over():
            failed.append(f"{salon['salon_name']}({slot})（時間切れ）")
            if not _flag_missing(salon, d, slot, "時間切れで埋められませんでした"):
                unflagged.append(f"{salon['salon_name']}({d} {slot})")
            continue
        try:
            # ⚠️ まとめ取得が取りこぼしていた可能性に備え、埋める直前に1件だけ再確認する
            # （2026-09-12 Sol指摘#1：件数上限で既投稿を「抜け」と誤判定して再投稿）
            op_id = post_state.make_op_id(salon["id"], d, slot)
            if already_posted_today(salon["id"], slot, op_id, jst_date=d):
                continue
            action, row = _acquire_with_retry(salon["id"], d, slot)
            if action in ("hold", "skip"):
                continue
            user_id, uname = get_user_id_from_token(salon["access_token"])
            if salon.get("threads_user_id") and str(salon["threads_user_id"]) != str(user_id):
                failed.append(f"{salon['salon_name']}({slot})（アカウント不一致）")
                continue
            status, detail, _k = _run_slot(row, action, salon, user_id,
                                           salon["access_token"], slot,
                                           f"@{uname}" if uname else salon["salon_name"],
                                           quiet=True)
            (filled if status == "ok" else failed).append(
                f"{salon['salon_name']}({slot})"
                + ("" if status == "ok" else f"（{str(detail)[:40]}）"))
        except Exception as e:
            failed.append(f"{salon['salon_name']}({slot})（{type(e).__name__}: {str(e)[:30]}）")

    # 日をまたいだ抜けは出さない。台帳に残して、届くまで知らせ続ける
    for salon, d, slot, _known in old:
        if _out_of_time("取りこぼしの記録"):
            unflagged.append(f"{salon['salon_name']}({d} {slot})（時間切れ）")
            continue
        if not _flag_missing(salon, d, slot, f"{d} の {slot} は投稿されていません（人の確認が必要）"):
            unflagged.append(f"{salon['salon_name']}({d} {slot})")

    lines = []
    if filled:
        lines.append("埋めた：" + "、".join(filled))
    if failed:
        lines.append("埋められなかった：" + "、".join(failed))
    if old:
        lines.append(f"日をまたいだ未投稿 {len(old)}件："
                     + "、".join(f"{s['salon_name']}({d} {sl})" for s, d, sl, _ in old[:5])
                     + ("ほか" if len(old) > 5 else ""))
    if unflagged:
        lines.append("⚠️ 台帳にも残せませんでした（次の実行でやり直します）："
                     + "、".join(unflagged[:5]))
    _notify_line("⚠️ とうこさん：出ていない投稿がありました。\n" + "\n".join(lines))
    if unflagged:
        raise RuntimeError(f"取りこぼしを台帳に残せませんでした（{len(unflagged)}件）")
    _mark_gap_checked(today)      # 全部を台帳に残せた日まで、点検済みとして進める
    return len(filled)


def main():
    global _job_deadline, _run_jst_date
    _job_deadline = time.time() + JOB_BUDGET_SEC
    _run_jst_date = datetime.now(JST).strftime("%Y-%m-%d")
    post_state.time_left_fn = _time_left   # 台帳の通信も締切に従わせる
    try:
        salons = get_active_salons()
    except Exception as e:
        # Supabase障害等。全サロン未投稿になるのに無通知だと気づけない
        _notify_line(f"🚨 SaaS投稿: サロン一覧の取得に失敗し全サロン未投稿です。\n{type(e).__name__}: {str(e)[:150]}")
        raise
    if not salons:
        print("アクティブなサロンがありません")
        return

    if SALON_FILTER:
        salons = [s for s in salons if s.get("salon_name") == SALON_FILTER]
        print(f"[filter] 対象サロンを {SALON_FILTER} のみに限定（{len(salons)}件）")
        if not salons:
            print(f"[filter] {SALON_FILTER} が見つかりません")
            return

    # ── 先に「片づいていない枠」を回収する ────────────────────────
    # 新規投稿の時間帯ガードとは分けて動かす。未確定のまま残った枠は、
    # 時間帯や日付が変わっても片づける必要がある（新しい本文は選ばない）。
    if not DRY_RUN:
        today = datetime.now(JST).strftime("%Y-%m-%d")
        skip = {post_state.make_op_id(s["id"], today, SLOT) for s in salons}
        try:
            n = recover_open_attempts(salons, skip_op_ids=skip)
            if n:
                print(f"[recover] {n}件の未完の枠を処理しました")
        except Exception as e:
            print(f"[recover] 回収処理でエラー（本編は続行）: {str(e)[:150]}")

    # 時間帯ガード（2026-06-18 → 2026-09-12 改訂）
    # 元の目的は「深夜に遅れて発火した予備cronが、JST日付を跨いで二重投稿する」ことの防止。
    # 台帳(op_id = サロン:日付:スロット)ができたので、二重投稿は台帳が防ぐ。
    # そこでガードは「まだ来ていないスロットを先に出さない」だけに絞り、
    # **時間帯を過ぎた遅れの発火は、その日の取りこぼしを埋める実行として通す**。
    # これをしないと、昼・夜は事実上1回しか投稿の機会がない
    # （GitHubの定期実行は実測で2〜4時間半遅れる）。
    if not SALON_FILTER:
        jst_hour = datetime.now(JST).hour
        win = SLOT_JST_WINDOWS.get(SLOT)
        if win is not None and jst_hour < win.start:
            print(f"[SKIP-ALL] {SLOT} はまだ時間前（現在 {jst_hour}時JST／{win.start}時から）"
                  "→ 先出しはしません")
            return
        if win is not None and jst_hour not in win:
            print(f"[LATE] {SLOT} の時間帯を過ぎています（現在 {jst_hour}時JST）"
                  "→ その日の取りこぼしを埋める実行として続けます")

    # ⚠️ 手動指定(ONLY_SALON)でも本番の台帳と既存ログの照合を通す。
    # 障害復旧で手動実行するときこそ二重投稿が起きやすい（2026-09-12 Sol指摘#3/#7）
    if DRY_RUN:
        post_state.force_memory()
        print("[state] DRY_RUNのため、台帳はこの実行内だけ（Supabaseに残しません）")

    results = {"ok": [], "error": [], "token_expired": [], "held": []}

    for salon in salons:
        if _date_rolled_over():
            print("[date] 日付が変わったので、残りのサロンは次の実行に回します")
            _notify_line("⚠️ とうこさん：処理の途中で日付が変わったため、"
                         "残りのサロンは次の実行に回しました。")
            break
        if _out_of_time("サロンの処理"):
            remaining = [s["salon_name"] for s in salons
                         if s["salon_name"] not in results["ok"]
                         and s["salon_name"] not in results["held"]]
            print(f"[budget] ジョブの持ち時間切れ → 未処理: {remaining}")
            _notify_line("⚠️ とうこさん：時間切れで最後まで処理できませんでした。\n"
                         f"未処理：{'、'.join(remaining[:10])}\n"
                         "次の実行（予備）が続きから片づけます。")
            results["error"].append("時間切れ: " + "、".join(remaining[:10]))
            break
        salon_id = salon["id"]
        salon_name = salon["salon_name"]
        user_id = salon["threads_user_id"]
        token = salon["access_token"]

        account_label = salon_name
        try:
            if DRY_RUN:
                # POSTS_DIR からのプール読込が成功したことだけ確認し、投稿・記録・台帳更新はしない。
                # /me も叩かない（DRY_RUNは本番に一切影響しない）
                used = get_used_posts(salon_id, SLOT)
                texts = pick_post(salon_name, SLOT, used)   # DRY_RUNでは補充workflowを起動しない
                n = len(texts) if isinstance(texts, list) else 1
                print(f"[DRY-RUN] {salon_name}: {SLOT} プール読込OK（{n}部）→ 投稿スキップ / POSTS_DIR={POSTS_DIR}")
                results["ok"].append(salon_name)
                continue

            # ── 台帳で実行権を取る ─────────────────────────────
            # ここが「公開できたか未確定」を次の実行へ引き継ぐ入口。
            # post_logs だけを見ていた頃は、未確定＝未投稿と誤読して二重投稿していた。
            # ⚠️ /me より先に取る。全パート公開済みで記録だけ足りない枠を、
            # Threads側の障害に巻き込まれず片づけるため（2026-09-12 Sol指摘#4）
            # ⚠️ 対象日は実行開始時に固定する。途中で日付が変わってから
            # 「翌日の枠」を作ると、まだ来ていない正規の投稿機会を潰す（Sol指摘#1）
            jst_date = _run_jst_date or datetime.now(JST).strftime("%Y-%m-%d")
            action, row = _acquire_with_retry(salon_id, jst_date, SLOT)

            if action == "hold":
                print(f"[HOLD] {salon_name}: {SLOT} は別の実行が処理中 → 投稿しません")
                results["held"].append(salon_name)
                continue
            if action == "skip":
                print(f"[SKIP] {salon_name}: {SLOT} は台帳で完了済み（重複実行を防止）")
                # SKIP時もリポジトリのlast_runを同期する。同期しないと、前回jobの
                # 失敗等でlast_runが古いままの場合にheartbeatが「未投稿」と誤判定し
                # 旧プールから二重投稿するリカバリを発火してしまう。
                _sync_last_run(salon_name, SLOT)
                results["ok"].append(salon_name)
                continue
            if action == "resume" and _all_parts_published(row):
                # 全パート公開済み＝もう投稿することは無い。/me を待たずに片づける
                if row.get("logged") and _promo_pending(row):
                    pl = row.get("payload") or {}
                    if _mark_promo_done(row, pl.get("original_first") or "") \
                            and _state_finish(_safe_fetch(row["op_id"], row),
                                              post_state.STATUS_LOGGED):
                        _sync_last_run(salon_name, SLOT, jst_date=jst_date)
                        results["ok"].append(salon_name)
                    else:
                        results["error"].append(
                            f"{salon_name}: 宣伝の使用済み記録または完了印を残せない")
                    continue
                if row.get("logged"):
                    print(f"[{salon_name}] {SLOT}: 全パート公開・記録済み → 完了にします")
                    if _state_finish(row, post_state.STATUS_LOGGED):
                        _sync_last_run(salon_name, SLOT, jst_date=jst_date)
                        results["ok"].append(salon_name)
                    else:
                        results["error"].append(f"{salon_name}: 完了の印を台帳に残せませんでした")
                    continue
                print(f"[{salon_name}] {SLOT}: 全パート公開済み → 記録だけ戻します")
                _repair_safe(row, salon, SLOT, jst_date,
                             finish_status=post_state.STATUS_LOGGED)
                cur = post_state.fetch(row["op_id"]) or {}
                if cur.get("logged"):
                    _sync_last_run(salon_name, SLOT, jst_date=jst_date)
                    results["ok"].append(salon_name)
                else:
                    results["error"].append(f"{salon_name}: 記録の復旧に失敗")
                continue

            # /me でusernameを取得（通知に使う）。user_id未設定なら同時に保存
            try:
                fetched_id, uname = get_user_id_from_token(token)
                mismatch = bool(user_id and fetched_id and str(user_id) != str(fetched_id))
                if not user_id and not DRY_RUN:
                    supabase_patch("salons", {"threads_user_id": fetched_id}, {"id": f"eq.{salon_id}"})
                    print(f"[{salon_name}] user_id={fetched_id} (@{uname}) を Supabase に保存")
                user_id = fetched_id
            except TokenExpiredError:
                # ⚠️ 一般エラーで包み直さない。再連携が必要なことが専用通知に届かなくなる
                # （2026-09-12 Sol指摘#7）
                raise
            except Exception as e:
                uname = ""
                fetched_id = ""
                mismatch = False
                print(f"[{salon_name}] /me 失敗: {e}")
                # ⚠️ 実アカウントを確認できないまま登録値で投稿しない。
                # 照合を通らずに公開へ進めてしまう（2026-09-12 Sol指摘#1）
                raise RuntimeError(f"実アカウントを確認できませんでした（/me 失敗）: {str(e)[:120]}")

            account_label = f"@{uname}" if uname else salon_name

            # ⚠️ 登録アカウントとトークンの実アカウントが違うなら投稿しない。
            # 黙って実アカウントを採用すると、Aのサロンの本文をBのアカウントへ出せてしまう
            # （2026-09-12 Sol指摘#3）。/me の失敗を握る try の中に置くと握り潰されるので外に出す
            if not fetched_id:
                raise RuntimeError("実アカウントIDを取得できませんでした（/me が空）")
            if mismatch:
                raise RuntimeError(
                    f"登録アカウント({salon.get('threads_user_id')})とトークンの実アカウント"
                    f"({fetched_id} @{uname})が一致しません。"
                    "連携をやり直したなら登録を更新してください")

            # ⚠️ post_logs だけを見る旧チェックはここへ移した。台帳より先に置くと、
            # 「1部目は公開・記録済みだが返信が未送」の枠を「投稿済み」と誤読して
            # 続きを永久に出せなくなる（台帳の resume を潰す）。
            # ⚠️ 対象サロンを絞っただけで照合を外さない。手動の復旧実行こそ
            # 追加投稿になりやすい（2026-09-12 Sol指摘#3）
            if action == "go" and already_posted_today(salon_id, SLOT, row["op_id"]):
                print(f"[SKIP] {salon_name}: {SLOT} は本日投稿済み（post_logs）")
                try:
                    post_state.update(row, status=post_state.STATUS_LOGGED,
                                      note="post_logs に既存の記録あり")
                except Exception as e:
                    print(f"[state] 完了印の保存に失敗（続行）: {str(e)[:80]}")
                _sync_last_run(salon_name, SLOT)
                results["ok"].append(salon_name)
                continue

            status, detail, _kind = _run_slot(row, action, salon, user_id, token,
                                              SLOT, account_label)
            if status == "ok":
                results["ok"].append(salon_name)
            else:
                results["error"].append(f"{salon_name}: {detail}")

        except TokenExpiredError as e:
            print(f"[TOKEN_EXPIRED] {salon_name}: {e}")
            results["token_expired"].append(salon_name)
            _notify_line(f"🔑 とうこさん トークン切れ\n\nアカウント：{account_label}\nスロット：{SLOT}\n\nThreadsとの再連携が必要です。")
        except Exception as e:
            print(f"[ERROR] {salon_name}: {e}")
            results["error"].append(f"{salon_name}: {e}")
            _notify_line(f"⚠️ とうこさん 投稿エラー\n\nアカウント：{account_label}\nスロット：{SLOT}\nエラー：{str(e)[:100]}")

    # ── 本来の枠を出し切ってから、1つ前のスロットの取りこぼしを埋める ────────
    # ⚠️ 先に穴埋めをすると、確実に動く今回の投稿機会を潰す（2026-09-12 Sol指摘#2）。
    # 専用の締切（GAPFILL_BUDGET_SEC）を持たせ、残り時間が無ければ次の実行に回す
    if not (DRY_RUN or SALON_FILTER):
        try:
            check_previous_slot(salons)
        except Exception as e:
            # ⚠️ ログに出すだけにしない。ジョブの失敗にして、監視にも届かせる
            # （2026-09-12 Sol指摘#3）
            print(f"[gap] 取りこぼしの確認でエラー: {str(e)[:150]}")
            _notify_line("⚠️ とうこさん：投稿の取りこぼしを確認・記録できませんでした。\n"
                         f"{type(e).__name__}: {str(e)[:150]}")
            results["error"].append(f"取りこぼしの確認: {type(e).__name__}")

    print(f"\n完了: 成功={len(results['ok'])} 失敗={len(results['error'])} トークン切れ={len(results['token_expired'])}")

    # 自アカ(bemolle/個人)が失敗していなければ healthcheck ping 許可の印を置く。
    # クライアント1件の失敗でHC pingが欠落→deadman workerの「GH Actions障害」誤報を防ぐ。
    own_accounts = {"bemolle_diet", "aya_kuroki_0929"}
    failed_names = set(results["token_expired"]) | {e.split(":", 1)[0] for e in results["error"]}
    if not (own_accounts & failed_names) and not DRY_RUN:
        try:
            with open("hc_ok", "w") as f:
                f.write("1")
        except Exception:
            pass

    if results["token_expired"]:
        sys.exit(3)  # token expiry → workflow側で専用通知
    if results["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
