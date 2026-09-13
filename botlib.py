#!/usr/bin/env python3
"""threads-bot 共通基盤（botlib）

LINE通知・JSON状態ファイルなど、各スクリプトに重複していた処理を集約する。
標準ライブラリのみに依存（pip追加インストール不要＝どのワークフローからも使える）。

移行済み: like_auto.py / oauth_reminder.py / research_threads.py /
  post_saas.py（_notify_line）/ post_api.py（状態JSON3関数）
移行しない（意図的）:
- generate_posts.py / generate_saas_posts.py … 投稿プールのJSON読み込みは
  「壊れていたら落とす」設計が正（黙って空データで続行するとプールを壊すため）。
  load_json のデフォルト返却とは意味が異なるので置き換えない
- preview_gen.py … LINE/状態JSONを持たない
- saas_app/（Vercel関数）… デプロイ単位が別のため対象外
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

LINE_API = "https://api.line.me/v2/bot/message"


# ── JSON状態ファイル ──────────────────────────────────────────
def load_json(path: str, default):
    """JSONファイルを読む。無い場合は default。壊れている場合も default だが、
    「ファイルなし」と区別できるよう stderr に警告を出す（静かな状態巻き戻りの検知用）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        print(f"[botlib] JSON破損を検出（defaultで継続）: {path}: {e}", file=sys.stderr)
        return default


def save_json(path: str, data) -> None:
    """一時ファイル→os.replace のアトミック書き込み。
    直書きだと書き込み途中の kill でファイルが壊れ、次回読み込みが黙って
    初期状態に巻き戻る（used_posts喪失→過去ネタの重複投稿の温床）。"""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── 生成コンテンツの最終バリデーション ─────────────────────────
# Claude生成の投稿がプールに入る直前の防波堤。プロンプト側で禁止していても
# 生成グリッチ・指示逸脱は起きるため、コード側で機械的に落とす。
NG_CONTENT_PATTERNS = (
    # 薬機法・景表法系の断定/保証（エステ・ダイエット商材で特にリスク）
    r"治[るりしせ]|治療|完治",
    r"(シミ|しみ|シワ|しわ|セルライト)が消え",
    r"絶対(に)?(痩せ|効果|結果|変わ)",
    r"必ず(痩せ|効果|結果)",
    r"100\s*[%％]痩せ|１００\s*[%％]",
    r"効果を保証|保証します",
    # 生成失敗・プロンプト漏れの痕跡
    r"申し訳ありません|作成できません|できかねます|As an AI|I cannot",
    # ハングル等のグリッチ（音節・字母・互換字母）
    r"[ᄀ-ᇿ㄰-㆏ꥠ-꥿가-퟿]",
)


def validate_post_content(post, *, min_len: int = 20, max_len: int = 500,
                          tree_parts: tuple = (2, 3)) -> str:
    """投稿1本（str または ツリー=list[str]）を検証し、問題なければ "" を、
    問題があれば理由を返す。呼び出し側は理由が返ったらプールに入れない。"""
    import re as _re
    parts = post if isinstance(post, list) else [post]
    if isinstance(post, list) and not (tree_parts[0] <= len(parts) <= tree_parts[1]):
        return f"ツリーのpart数が不正({len(parts)})"
    for p in parts:
        if not isinstance(p, str):
            return f"文字列でない要素({type(p).__name__})"
        s = p.strip()
        if len(s) < min_len:
            return f"短すぎる({len(s)}字)"
        if len(s) > max_len:
            return f"長すぎる({len(s)}字)"
        for pat in NG_CONTENT_PATTERNS:
            m = _re.search(pat, s)
            if m:
                return f"NG表現「{m.group(0)}」"
    return ""


# ── LINE通知 ──────────────────────────────────────────────────
def _default_token() -> str:
    """管理者通知の既定トークン: Claude通知Bot優先 → とうこさんLINEにフォールバック"""
    return os.environ.get("ADMIN_NOTIFY_LINE_TOKEN") or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")


# とうこさんLINEチャンネルでの管理者(黒木さん)User ID。
# broadcast禁止時のpush宛先。Secret LINE_ADMIN_USER_ID があれば優先し、
# 無い場合のフォールバック値は SAAS_SPEC.md に記載済みの公開情報（トークン無しでは何もできないID）。
_ADMIN_UID_FALLBACK = "Ucf261a250763ff136250262e4639e9ee"


def line_broadcast(text: str, token: str = "", *, timeout: int = 10) -> bool:
    """管理者向けLINE通知。失敗しても例外を投げず、ログを残して False。
    token 未指定時は ADMIN_NOTIFY_LINE_TOKEN → LINE_CHANNEL_ACCESS_TOKEN の順で解決。

    ⚠️ broadcastを使うのは Claude通知Bot（友だち=管理者のみ）の時だけ。
    とうこさんLINE等の顧客が友だちにいるチャンネルでは、broadcastすると
    管理者アラートが顧客全員に配信されてしまうため、管理者への push に自動で切り替える
    （2026-07-12 総点検指摘「broadcast→push化」対応）。"""
    token = token or _default_token()
    if not token:
        print(f"[line] トークン無しのため未送信: {text[:60]}", file=sys.stderr)
        return False
    try:
        admin_bot = os.environ.get("ADMIN_NOTIFY_LINE_TOKEN", "")
        if admin_bot and token == admin_bot:
            # Claude通知Bot: 友だちは管理者1人だけなのでbroadcastで安全
            url = f"{LINE_API}/broadcast"
            payload = {"messages": [{"type": "text", "text": text}]}
        else:
            # 顧客が友だちにいるチャンネル（とうこさんLINE等）: 管理者へ直接push
            uid = os.environ.get("LINE_ADMIN_USER_ID", "") or _ADMIN_UID_FALLBACK
            url = f"{LINE_API}/push"
            payload = {"to": uid, "messages": [{"type": "text", "text": text}]}
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=timeout)
        # 障害通知(🚨/⚠️/🔑)を送れた印。workflowの failure() 通知ステップは
        # この印があれば重複送信をスキップする（1障害＝broadcast複数通の浪費を防ぐ）
        try:
            if any(m in text for m in ("🚨", "⚠️", "🔑")):
                with open(".line_notified", "w") as f:
                    f.write("1")
        except Exception:
            pass
        return True
    except Exception as e:
        print(f"::error::[line] 通知送信失敗（処理は継続・通知は届いていない）: {e}", file=sys.stderr)
        return False


def line_push(user_id: str, text: str, token: str, *, timeout: int = 10) -> int:
    """特定ユーザーへのLINE push（クライアント宛て等）。失敗時は例外を投げる
    （宛先指定の送信は黙って失敗させない）。戻り値はHTTPステータス。"""
    body = json.dumps({"to": user_id, "messages": [{"type": "text", "text": text}]}).encode()
    req = urllib.request.Request(
        f"{LINE_API}/push",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status


# ── 判断材料投稿の事実照合 ─────────────────────────────────────
# 「このサロンを選ぶ判断材料」投稿は、場所・料金・営業時間という**確かめられる事実**を
# 書く。プロンプトで禁じても生成はときどき逸脱するので、ヒアリング内容と機械で
# 突き合わせて、書いていない数字が客先のアカウントに出ないようにする（2026-09-13 Sol指摘#1）。
# ⚠️ 判定できない書き方（漢数字の金額・住所・駅名）は「通す」ではなく「落とす」。
# 落として困るのは在庫が1本減ることだけだが、通して困るのは客先に嘘が出ること。

_ZEN = str.maketrans("０１２３４５６７８９，：．－−‐―〜～", "0123456789,:.----~~")
# ¥12,000 / 12,000円 / 1万円 / 3千円
# ⚠️ 「1.5万円」を「5万円」と読むと、登録が5万円のサロンで1.5万円が通ってしまう
# （2026-09-13 Sol 6巡目）。数字の途中から拾わないよう (?<![0-9.]) を付ける。
_MONEY_RE = re.compile(
    r"[¥￥]\s*(?<![0-9.])([0-9][0-9,]*)"
    r"|(?<![0-9.])([0-9]+(?:\.[0-9]+)?)\s*万\s*([0-9][0-9,]*)?\s*(千)?\s*(?:円|(?![人回件本歩年台軒名日月分秒個枚色歳倍kgcm]))"
    r"|(?<![0-9.])([0-9]+(?:\.[0-9]+)?)\s*千\s*(?:円|(?![人回件本歩年台軒名日月分秒個枚色歳倍kgcm]))"
    r"|(?<![0-9.])([0-9]+(?:\.[0-9]+)?)\s*百\s*円"
    r"|(?<![0-9.])([0-9][0-9,]*)\s*円")
# 「一万円」「五千円」など、メニュー欄と突き合わせようがない書き方。
# ⚠️ 先頭を漢数字（一〜九・十・百）に限る。そうしないと「1万円」の「万円」にも当たって
# 正しい投稿まで落ち、判断材料が永久に空になる（2026-09-13 Sol 3巡目）。
_KANJI_MONEY_RE = re.compile(
    r"(?<![0-9])[〇一二三四五六七八九十百][〇一二三四五六七八九十百千万億]{0,7}\s*円")
# 「徒歩3分」「車で5分」など、距離の言い切り
_ACCESS_RE = re.compile(r"(徒歩|車で|バスで)\s*(?:約)?\s*([0-9]+)\s*分")
# 時刻（9:30 / 9時 / 9時30分 / 9時半 / 午後6時）
_TIME_RE = re.compile(
    r"(午前|午後)?\s*([0-9]{1,2})\s*(?::\s*([0-9]{2})|時\s*(?:(半)|([0-9]{1,2})\s*分)?)")
# 営業時間の話をしている文か（「受け付ける」も拾う）
_BIZ_WORD_RE = re.compile(r"営業|受付|受け付|オープン|開店|閉店|定休|お待ちして|承って|お受け")
_LASTCALL_WORD_RE = re.compile(r"最終受付|最終のご案内|受付終了|受付|受け付")
_LAST_RE = re.compile(r"最終受付|最終のご案内|受付終了|最後に受け付|最後の受付")
# 「営業時間は9:30〜17:30です」のように、範囲そのものを営業時間だと言っている文
_RANGE_CTX_RE = re.compile(r"営業|受付|受け付|オープン|開店|閉店|承って|お受け|やって|ご案内")
_OPEN_WORD_RE = re.compile(r"営業|オープン|開店|受付|受け付|お待ちして")
_CLOSE_WORD_RE = re.compile(r"営業|オープン|やって|閉店|お待ちして|承って|お受け")
# 「七時」「十時半」など、営業時間欄と突き合わせられない書き方
_KANJI_TIME_RE = re.compile(r"[〇一二三四五六七八九十]{1,3}\s*時")
# 「9時から18時まで」「9:00〜18:00」のような時間の範囲
_TIME_PREFIX = r"(?:午前|午後|朝|昼|夕方|夕|夜|深夜)?\s*"
_TIME_RANGE_RE = re.compile(
    _TIME_PREFIX + r"[0-9]{1,2}\s*(?::[0-9]{2}|時(?:半|[0-9]{1,2}分)?)"
    r"\s*(?:から|〜|~|-|–|ー|より)\s*" + _TIME_PREFIX +
    r"[0-9]{1,2}\s*(?::[0-9]{2}|時(?:半|[0-9]{1,2}分)?)")
# 住所・駅名など、ヒアリングに無ければ確かめようがない場所の言い切り。
# ⚠️ 前の文字を丸ごと巻き込むと「当店は渋谷区」で照合が外れ、末尾だけで照合すると
# 「架空谷区」の「谷区」や「9丁目」の「丁目」で通ってしまう（2026-09-13 Sol 4・5巡目）。
# そこで、地名の語尾から**助詞・句読点に当たるまで前へ1文字ずつ遡って**地名を1つに切り出す。
# 「5-12-18」「99-99」のような番地表記も照合対象にする。
# ⚠️ 「3-5回」「1-2ヶ月」は範囲であって住所ではないので、単位が続く物は除く。
# ⚠️ 「区別」「市販」「町内」のように、語尾の直後に字が続いて別の言葉になる物は地名ではない
_PLACE_RE = re.compile(
    r"[ぁ-んァ-ヶー一-龥々0-9]{1,8}(?:駅|丁目|番地|番[0-9]{1,4}号)"
    r"|[ぁ-んァ-ヶー一-龥々0-9]{1,8}市(?![販場民長立制営議役外])"
    r"|[ぁ-んァ-ヶー一-龥々0-9]{1,8}区(?![別分域画間切内])"
    r"|[ぁ-んァ-ヶー一-龥々0-9]{1,8}町(?![内中会民長工])"
    r"|[ぁ-んァ-ヶー一-龥々0-9]{1,8}村(?![社民長])"
    r"|[0-9]{1,4}(?:-[0-9]{1,4}){1,2}")
# 「3-12回」「10-20分」は範囲であって住所ではない。単位が続く物は住所として見ない
_RANGE_UNIT_RE = re.compile(
    r"^\s*(?:回|本|ヶ月|ヵ月|か月|カ月|分|人|日|週|年|度|割|倍|名|kg|cm|mm|%|％|時|:|万|千|円)")
_HIRAGANA_HEAD_RE = re.compile(r"^[ぁ-ん]+")
# 地名に見えるが地名ではない普通の言葉（落として在庫を減らすだけなので明示的に除く）
_PLACE_STOPWORDS = {
    "都市", "地方都市", "地区", "下町", "市街", "市販", "市場", "区別", "区分",
    "町内", "町中", "村社会", "繁華街", "各市", "各区",
    "この町", "その町", "うちの町", "この市", "この村", "この区",
}


def _money_tokens(text: str) -> set:
    """本文に出てくる金額を、円単位の数字（文字列）の集合で返す。"""
    out = set()
    for m in _MONEY_RE.finditer((text or "").translate(_ZEN)):
        yen, man, man_sub, man_sen, sen, hyaku, plain = m.groups()
        if yen:
            out.add(yen.replace(",", ""))
        elif man:
            v = float(man) * 10000
            if man_sub:
                v += float(man_sub.replace(",", "")) * (1000 if man_sen else 1)
            out.add(str(int(v)))
        elif sen:
            out.add(str(int(float(sen) * 1000)))
        elif hyaku:
            out.add(str(int(float(hyaku) * 100)))
        elif plain:
            out.add(plain.replace(",", ""))
    return out


def _time_tokens(text: str) -> set:
    """本文に出てくる時刻を H:MM の形にそろえて返す。

    「午後6時」「9時半」も、営業時間欄の「18:00」「9:30」と同じ形にしてから比べる
    （そうしないと正しい書き方まで落ちる。2026-09-13 Sol 3巡目）。"""
    out = set()
    for m in _TIME_RE.finditer((text or "").translate(_ZEN)):
        ampm, hh, mm_colon, han, mm_fun = m.groups()
        hour = int(hh)
        minute = 30 if han else int(mm_colon or mm_fun or 0)
        if ampm == "午後" and hour < 12:
            hour += 12
        if hour > 24 or minute > 59:
            continue
        out.add(f"{hour}:{minute:02d}")
    return out


def _place_matches(text: str) -> list:
    """本文の地名らしい部分を「そのまま」返す（前の文字も付いたまま）。

    ⚠️ 「3-12回」「10-20分」は範囲表記。正規表現の否定先読みで外すと、数字を短く
    取り直して「3-1」を住所として拾ってしまう（2026-09-13 Sol 7巡目）。
    だから拾ってから、後ろに単位が続く物を落とす。"""
    text = (text or "").translate(_ZEN)
    out = []
    for m in _PLACE_RE.finditer(text):
        token = m.group(0).strip()
        if token[0].isdigit() and _RANGE_UNIT_RE.match(text[m.end():]):
            continue        # 数字だけの並び＋単位＝範囲表記
        out.append(token)
    return out


# 番地（5-12-18 / 99-99）と丁目・番地表記は、末尾一致を許すと
# 「99-12-18」が登録の「5-12-18」の一部と一致して通ってしまう（2026-09-13 Sol 7巡目）。
# こういう数字の住所は、丸ごと同じでなければ通さない。
_ADDR_NUM_RE = re.compile(r"[0-9]{1,4}-[0-9]{1,4}(?:-[0-9]{1,4})?")
_CHOME_RE = re.compile(r"[0-9]{1,4}\s*(?:丁目|番地|番(?:[0-9]{1,4})号)")


def _place_known(match: str, known_place: str) -> bool:
    """切り出した地名が、ヒアリングの所在地に書かれているか。

    ⚠️ 「当店は渋谷区」のように前の文字が混ざるので末尾から切って照合するが、
    2文字まで許すと「架空谷区」が「谷区」で通ってしまう。
    **3文字以上の切り出しだけ**を見る（元が2文字ならその形のまま照合する）。
    ⚠️ 数字の住所（5-12-18 / 1丁目）は末尾一致を許さず、丸ごと一致だけを通す。"""
    if _ADDR_NUM_RE.fullmatch(match):
        return match in set(_ADDR_NUM_RE.findall(known_place))
    if _CHOME_RE.search(match):
        # ⚠️ 1つ目だけ見ると「渋谷区1丁目99番99号」の 1丁目 だけで通ってしまう。
        # 出てくる番地表記は**全部**登録どおりであることを要求する（Sol 7巡目）
        theirs = {x.replace(" ", "") for x in _CHOME_RE.findall(known_place)}
        for mine in _CHOME_RE.findall(match):
            if mine.replace(" ", "") not in theirs:
                return False
        # 数字が合っていても、その前の地名までそろっているか見る
        head = match[:_CHOME_RE.search(match).start()]
        return not head or any(head[i:] in known_place
                               for i in range(len(head) - min(3, len(head)) + 1))
    # ⚠️ 末尾一致を無条件に許すと「東渋谷区」が「渋谷区」で通る（Sol 7巡目）。
    # 切り出せるのは、直前が助詞（ひらがな）のところだけにする。
    if match in known_place:
        return True
    lowest = min(3, len(match))
    for i in range(1, len(match) - lowest + 1):
        if "ぁ" <= match[i - 1] <= "ん" and match[i:] in known_place:
            return True
    return False


def _ordered_times(text: str) -> list:
    """本文に出てくる時刻を、出てきた順に分（数値）で返す。前後の逆転を見るため。"""
    out = []
    for m in _TIME_RE.finditer((text or "").translate(_ZEN)):
        ampm, hh, mm_colon, han, mm_fun = m.groups()
        hour = int(hh)
        minute = 30 if han else int(mm_colon or mm_fun or 0)
        if ampm == "午後" and hour < 12:
            hour += 12
        if hour > 24 or minute > 59:
            continue
        out.append(hour * 60 + minute)
    return out


def _hours_span(hours: str):
    """営業時間欄（9:30〜18:00 など）を (開始の分, 終了の分) にする。読めなければ None。"""
    times = _ordered_times(hours)
    # ⚠️ 最後の時刻を閉店時刻にしてはいけない。「9:30〜17:30（最終受付16:30）」だと
    # 16:30が閉店になり、正しい17:30が「営業時間の外」で落ちる（2026-09-13 実データで発生）
    if len(times) >= 2 and min(times) < max(times):
        return min(times), max(times)
    return None


def _allowed_money(salon: dict) -> set:
    """このサロンの投稿に書いてよい金額（ヒアリングに実在する数字だけ）。"""
    price_ok = str(salon.get("価格を投稿に記載してもOKですか？", "")).strip()
    menu = " ".join([
        str(salon.get("提供メニューと価格帯（箇条書きでOK）", "")),
        str(salon.get("一番の売りメニュー・最も結果が出やすい施術", "")),
    ])
    if price_ok == "はい（具体的な金額を投稿に出してOK）":
        return _money_tokens(menu)
    if price_ok == "体験・初回コースの価格のみOK":
        # 「初回」「体験」の直後、次の区切りまでに書かれている金額だけを許す。
        # ⚠️ 単純に「40字以内」にすると「初回8,800円／通常14,850円」の通常価格まで
        # 許可に入る（2026-09-13 Sol指摘）。区切り記号と「通常」で必ず切る。
        allowed = set()
        for m in re.finditer(r"(初回|体験)", menu):
            window = menu[m.start():m.start() + 40]
            # ⚠️ 区切りに「,」を入れてはいけない。「8,800円」の中のカンマで切れて
            # 金額そのものが消える（許可がゼロになり、正しい初回価格まで落ちる）
            cut = re.search(r"[／/、・\n（(]|通常|定価|回目|以降|以後", window[2:])
            if cut:
                window = window[:cut.start() + 2]
            allowed |= _money_tokens(window)
        return allowed
    return set()        # 「いいえ」も、読み取れない回答も、金額は書かせない


def _hours_facts(hours: str):
    """営業時間欄を (開店, 閉店, 最終受付 or None) の分数にする。読めなければ None。"""
    h = (hours or "").translate(_ZEN)
    last = None
    m = re.search(r"最終受付|最終のご案内|受付終了", h)
    if m:
        t = _ordered_times(h[m.end():m.end() + 14])
        last = t[0] if t else None
        h = h[:m.start()]
    times = _ordered_times(h)
    # ⚠️ 最後の時刻を閉店にしてはいけない。「9:30〜17:30（最終受付16:30）」で
    # 16:30が閉店になり、正しい17:30が落ちる（2026-09-13 実データで発生）
    if len(times) >= 2 and min(times) < max(times):
        return min(times), max(times), last
    return None


def _fmt(mins: int) -> str:
    return f"{mins // 60}:{mins % 60:02d}"


def _hours_violation(text: str, hours: str):
    """営業時間について書いていることが、ヒアリングと合っているか。

    ⚠️ 文単位で見る。全文をまとめて見ると「朝7時に家を出て」のような生活の時刻まで
    営業時間として落ちる（2026-09-13 Sol指摘）。
    ⚠️ ただし「最終受付についてご案内します。」「夜22時です。」のように
    次の文へ話がまたぐことがある。時刻を含まない営業の文は、次の文へ文脈を持ち越す。
    ⚠️ 「◯時から◯時まで」の範囲表記は、開店・閉店そのものの言い切りとして扱う。"""
    norm = (text or "").translate(_ZEN)
    facts = _hours_facts(hours)
    allowed = _time_tokens(hours)

    def _check(t, kind):
        if facts is None:
            return None if t in allowed else f"ヒアリングに無い時刻（{t}）"
        open_m, close_m, last_m = facts
        if kind == "最終受付":
            if last_m is None:
                return f"ヒアリングに最終受付の記載が無い（{t}）"
            return None if t == _fmt(last_m) else f"ヒアリングと違う最終受付（{t}）"
        if kind == "開店":
            return None if t == _fmt(open_m) else f"ヒアリングと違う開店時刻（{t}）"
        if kind == "閉店":
            return None if t == _fmt(close_m) else f"ヒアリングと違う閉店時刻（{t}）"
        h, mm = t.split(":")
        if not (open_m <= int(h) * 60 + int(mm) <= close_m):
            return f"営業時間の外の時刻（{t}）"
        return None

    carry = False       # 直前の文が「営業の話だが時刻が無い」だったか
    for sent in re.split(r"[。！？\n]", norm):
        if not sent.strip():
            continue
        biz = bool(_BIZ_WORD_RE.search(sent))
        times_here = list(_TIME_RE.finditer(sent))
        if not (biz or carry):
            carry = False
            continue
        # ⚠️ 漢数字の時刻は「時刻が無い文」に見えるので、持ち越し判定より先に見る
        if _KANJI_TIME_RE.search(sent):
            return "漢数字の時刻（営業時間欄と突き合わせられない）"
        if biz and not times_here:
            carry = True            # 「最終受付についてご案内します。」→ 次の文へ持ち越す
            continue
        carry = False

        # 「9:30〜17:30」「9時から18時まで」の範囲表記は開店・閉店の言い切り
        rng = _TIME_RANGE_RE.search(sent)
        if rng and _RANGE_CTX_RE.search(sent):
            # ⚠️ 文字列で並べ替えると "17:30" < "9:30" になり、開店と閉店が入れ替わる
            order = _ordered_times(rng.group(0))
            if len(order) >= 2 and order[0] >= order[-1]:
                return f"時間の前後が逆（{rng.group(0)}）"
            if len(order) >= 2:
                for t, kind in ((_fmt(order[0]), "開店"), (_fmt(order[-1]), "閉店")):
                    r = _check(t, kind)
                    if r:
                        return r
                continue

        for m in times_here:
            got = _time_tokens(m.group(0))
            if not got:
                continue
            t = sorted(got)[0]
            after = sent[m.end():m.end() + 10]
            before = sent[max(0, m.start() - 10):m.start()]
            around = before + after
            kind = None
            if _LAST_RE.search(around) or ("まで" in after and _LASTCALL_WORD_RE.search(after)):
                kind = "最終受付"
            elif re.search(r"開店|オープン|開き|開けて", after[:6]) or \
                    (("から" in after or "より" in after) and _OPEN_WORD_RE.search(sent)):
                kind = "開店"
            elif re.search(r"閉店|終了", after[:6]) or \
                    ("まで" in after and _CLOSE_WORD_RE.search(sent)):
                kind = "閉店"
            r = _check(t, kind)
            if r:
                return r
    return None


# 予約先URLなどのリンク。中に数字とハイフンが入っているため、住所や金額として
# 拾ってしまう（2026-09-13 実害：つばめの巣の予約URL 2008022680-0gNljz7e の
# 「2680-0」を番地と誤認し、判断材料が15本すべて落ちた）
_URL_RE = re.compile(r"https?://\S+|\S+\.(?:jp|com|net|link|ee|me|be|co)\S*", re.I)


def _strip_urls(text: str) -> str:
    return _URL_RE.sub(" ", text or "")


def judge_fact_violation(text: str, salon: dict):
    """判断材料投稿が、ヒアリングに無い事実を書いていないか。違反なら理由を返す。"""
    # ⚠️ リンクの中身は本文の主張ではない。先に外す
    text = _strip_urls(str(text or ""))

    if _KANJI_MONEY_RE.search(text):
        return "漢数字の金額（メニュー欄と突き合わせられない）"
    allowed = _allowed_money(salon)
    for money in _money_tokens(text):
        if money not in allowed:
            return f"ヒアリングに無い金額（{money}円）"

    location = str(salon.get("所在地（最寄り駅・徒歩時間）", ""))
    loc_norm = location.translate(_ZEN)
    loc_access = {(m.group(1), m.group(2))
                  for m in _ACCESS_RE.finditer(loc_norm)}
    for m in _ACCESS_RE.finditer(text.translate(_ZEN)):
        if (m.group(1), m.group(2)) not in loc_access:
            return f"ヒアリングに無い所要時間（{m.group(0)}）"
    # 駅名・丁目・市区町村は、所在地欄に同じ書き方が無ければ確かめようがない
    known_place = loc_norm + " " + str(salon.get("サロン名", ""))
    for place in _place_matches(text):
        # 「この地区」のような普通の言い回しは、先頭のひらがなを外した形で除外語を見る
        stripped = _HIRAGANA_HEAD_RE.sub("", place)
        if place in _PLACE_STOPWORDS or stripped in _PLACE_STOPWORDS:
            continue
        if not _place_known(place, known_place):
            return f"ヒアリングに無い場所（{place}）"

    reason = _hours_violation(text, str(salon.get("営業時間", "")))
    if reason:
        return reason

    if re.search(r"instagram|インスタ", text, re.I):
        return "本文にInstagram誘導が入っている（投稿時に自動で付くため二重になる）"
    return None


def judge_fact_violations(post, salon):
    """単発でもツリーでも使える事実照合。違反理由（無ければ None）。

    ⚠️ 各部を別々に見るだけだと、1部目「最後に受け付けるのは」→
    2部目「夜22時です」のように**部をまたいだ嘘**が通る（2026-09-13 Sol指摘#1）。
    各部に加えて、つなげた全文にも当てる。"""
    parts = [post] if isinstance(post, str) else [x for x in (post or []) if isinstance(x, str)]
    for part in parts:
        reason = judge_fact_violation(part, salon)
        if reason:
            return reason
    if len(parts) > 1:
        return judge_fact_violation("\n\n".join(parts), salon)
    return None
