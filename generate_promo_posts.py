#!/usr/bin/env python3
"""月曜21時の宣伝枠（個人アカ）の文章在庫を補充する。

未使用の在庫が2本を切ったら、Claudeで3本生成して promo_posts_personal.json に足す。
週1本消費なので、在庫4本＝約1ヶ月分。毎日走らせても在庫が足りていれば何もしない。
"""

import json
import os
import sys

import anthropic

BASE = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(BASE, "promo_posts_personal.json")
USED_FILE = os.path.join(BASE, "promo_used_personal.json")
MIN_STOCK = 2
GENERATE_N = 3
LINE_URL = "https://lin.ee/88vrtbQ"

PROMPT = f"""あなたは美容サロンオーナー・黒木彩さん本人として、Threadsの投稿を書きます。
自分が作ったThreads自動投稿サービス「とうこさん」を、同業のサロンオーナーに紹介する投稿です。

# 事実（ここに無いことは書かない）
- Threadsに1日3回（朝7時・昼12時・夜9時）自動投稿するサービス。名前は「とうこさん」
- 最初にサロンの情報をフォームで渡すだけ。あとはAIが文章を作って自動で上がる
- 月2,750円（税込・サブスク）。最低利用期間3ヶ月。彩さんが知る限りいちばん安い
- 写真・画像は付けられない。ビフォーアフターや当日の告知は自分で手動投稿する必要がある
- 使っている方で、フォロワーが月50人くらい増えている方もいる
  （※平均ではなく一部の方の数字。「使っている方は月50人増えます」のように全体の実績として書かない）
- インスタは広告を回さないと見てもらえなくなった。Threadsはまだ広告なしで知らない人に届く
- ただしThreadsは投稿を止めると表示が落ちる。続けている人だけが拾われる
- 彩さん自身も、サロンの仕事（施術・片付け・発注）で手一杯になり、投稿が続かなかった
- 彩さんは美容サロン（ベモーレ）のオーナー。エンジニアではなく、自分が困ったから自分で作った
- 案内先のLINE（友だち追加）では、メニューからそのまま申し込みができる。申し込む前に質問だけ送ってもらってもよく、彩さんが読んで返している

# 文体（最重要）
- 話し言葉のですます調。サロンオーナー仲間に話しかける温度
- AIが書いたような文章は絶対に避ける。特に次を禁止する：
  - まとめの一文で締める（「〜ということです」「〜だと思っています」で全体を総括する）
  - 気の利いた一文・うまいことを言う一文（「それが証拠です」等）
  - 対句できれいに落とす（「Aではなく、Bでした」）
  - 決め台詞（「条件は一つだけ。」等）
  - 体言止めの多用、同じ語尾の3連続
- 語尾の「ね」は1投稿に1回まで。「うち」は使わない
- 最後は総括せず、リンクの案内で終える
- 事実に無い出来事・数字・時期・会話を作らない

# 形式
- 350〜450字（必ず500字未満）
- 段落を空行で分ける
- 最後はLINEへの誘導1〜2文で締め、最後の行は必ず {LINE_URL}
  - 「中身はこちらに置いてあります」のような場所の案内だけの誘導は禁止（読者が動く理由にならない・2026-07-28彩さん指摘）
  - ①誰への声かけか（例：止まったままのアカウントがある方は）②追加すると何ができるか（そのまま申し込める／質問だけでも大丈夫）のどちらか、または両方を入れる
  - 誘導の言い回しは毎回変える（「質問だけでも大丈夫です」を毎本くり返さない）

# 切り口
毎週出すので、既存の投稿と切り口が被らないようにする。使える切り口の例：
広告費との比較／続かなかった話／作った理由／Threadsが今まだ穴場である話／
できないこと（画像が付けられない）を正直に言う話／時間が取れないサロンオーナーあるある

# すでに使った投稿（切り口・書き出しを被らせない）
{{existing}}

上のルールで、新しい投稿を{GENERATE_N}本書いてください。
出力は次のJSONだけ。説明文は書かないでください。
{{"posts": ["本文1", "本文2", "本文3"]}}
"""


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    pool = _load(POOL_FILE, {})
    posts = pool.get("posts") or []
    used = set(_load(USED_FILE, []))
    unused = [p for p in posts if p not in used]

    print(f"[promo] 在庫: 未使用{len(unused)}本 / 全{len(posts)}本")
    if len(unused) >= MIN_STOCK and "--force" not in sys.argv:
        print("[promo] 在庫は足りています。生成しません")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("[promo] ANTHROPIC_API_KEY が無いため生成できません")
        return 1

    existing = "\n\n---\n\n".join(posts[-6:]) if posts else "（まだありません）"
    prompt = PROMPT.replace("{existing}", existing)

    client = anthropic.Anthropic()
    last_err = None
    for model in ("claude-sonnet-5", "claude-sonnet-4-6"):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            # 先頭が thinking ブロックのことがあるため、text ブロックだけを拾う
            raw = "".join(b.text for b in resp.content
                          if getattr(b, "type", "") == "text").strip()
            if not raw:
                raise RuntimeError("応答にテキストが含まれていません")
            break
        except Exception as e:
            last_err = e
            print(f"[promo] {model} で生成失敗: {e}")
    else:
        raise RuntimeError(f"宣伝文の生成に失敗しました: {last_err}")

    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[raw.find("{"):]
    new_posts = json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("posts") or []

    added = []
    for p in new_posts:
        p = p.strip()
        if not p or p in posts:
            continue
        if len(p) >= 500:
            print(f"[promo] 500字以上のため不採用（{len(p)}字）")
            continue
        if LINE_URL not in p:
            print("[promo] LINEリンクが無いため不採用")
            continue
        posts.append(p)
        added.append(p)

    if not added:
        raise RuntimeError("採用できる宣伝文が1本もありませんでした")

    pool["posts"] = posts
    with open(POOL_FILE, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=2)
    print(f"[promo] {len(added)}本を追加しました（在庫 未使用{len(unused) + len(added)}本）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
