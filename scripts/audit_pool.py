#!/usr/bin/env python3
"""在庫を「数」で確かめる。目で2〜3本見て判断しないための道具。

なぜ要るか（2026-11-13）：
ルールを直すたびに、試し生成を2〜3本だけ見て「良さそう」と判断し、全アカウントを
作り直していた。作り直した後で在庫全体を測ると、別の不具合が見つかる。
 ・呼びかけの例に宣言型を混ぜた → 216本中29本が堅い一言目
 ・関西弁を「3文に1回」に直した → 134本中103本が標準語だけ
 ・【地域名】を目安で書いた     → 75本中0〜4本
 ・【豊中市】を指定した         → 安全装置が全部捨てていた
そのたびに作り直しでお金がかかった。作り直す前と後に、必ずこれで測る。

  python3 scripts/audit_pool.py posts_saas/posts_bemolle_diet.json
  python3 scripts/audit_pool.py <在庫.json> --strict   # 基準を割ったら終了コード1

見るもの：
  宣言型の一言目（0本であること）／1行目の重複／講義語／
  関西弁アカウントの関西弁率（100%であること）／【地域名】始まりの本数
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SLOTS = ("morning", "noon", "evening")
LECTURE = ("の本質", "の構造", "ポイントは", "が大切です", "していきましょう",
           "いかがでしょうか", "重要なのは", "大切なのは")


def audit(pool: dict, safe_name: str = "") -> dict:
    """在庫1つ分の数字を返す。"""
    from botlib import stiff_opening_reason, is_interactive, is_teaching, INTERACT_ACCOUNTS
    try:
        from generate_saas_posts import style_reason
    except Exception:                      # noqa: BLE001
        def style_reason(_p, _n):          # 読めない環境では話し方の検査だけ省く
            return None

    posts = [p for s in SLOTS for p in pool.get(s, [])]
    firsts = [(p[0] if isinstance(p, list) else str(p)).split("\n")[0] for p in posts]
    texts = ["\n".join(p) if isinstance(p, list) else str(p) for p in posts]
    seen, dup = set(), 0
    for f in firsts:
        if f in seen:
            dup += 1
        seen.add(f)
    return {
        "total": len(posts),
        "per_slot": {s: len(pool.get(s, [])) for s in SLOTS},
        "stiff": sum(1 for p in posts if stiff_opening_reason(p)),
        "style_ng": sum(1 for p in posts if style_reason(p, safe_name)),
        "dup_first": dup,
        "lecture": sum(1 for t in texts if any(w in t for w in LECTURE)),
        "area": sum(1 for f in firsts if f.startswith("【")),
        "exclaim": sum(1 for f in firsts if f.rstrip().endswith("！")),
        # 相手が答えられる問いかけで終わる投稿（交流の回の目安）
        "interact": sum(1 for p in posts if is_interactive(p)),
        "teach": sum(1 for p in posts if is_teaching(p)),
        "interact_target": safe_name in INTERACT_ACCOUNTS,
    }


def problems(m: dict) -> list:
    """基準を割っている項目。空なら合格。"""
    out = []
    if m["total"] == 0:
        return ["在庫が0本"]
    if m["stiff"]:
        out.append(f"宣言型の一言目が{m['stiff']}本（0本であること）")
    if m["style_ng"]:
        out.append(f"話し方が合わない投稿が{m['style_ng']}本（関西弁の指定など・0本であること）")
    if m["dup_first"] > max(2, m["total"] // 10):
        out.append(f"1行目の重複が{m['dup_first']}本（全体の1割まで）")
    if m["lecture"] > max(2, m["total"] // 10):
        out.append(f"講義語の入った投稿が{m['lecture']}本（全体の1割まで）")
    # 2026-11-27 鷺さん「ずっとノウハウばかり」。個人・ベモーレは3本に1本を交流の回にする。
    # ⚠️「問いかけで終わる本数」では警告しない。実績（個人の直近45本）で、問いで終わる
    # 11本はコメント0件、コメントが付いた3本は問いなしだった。問いの本数を増やしても
    # コメントは増えない。ここでは「教える言葉が入った投稿」が多すぎるときだけ知らせる。
    if m.get("interact_target") and m["teach"] * 10 > m["total"] * 6:
        out.append(f"教える言葉が入った投稿が{m['teach']}本（{m['teach'] * 100 // m['total']}%）"
                   f"＝6割を超えている（ノウハウに寄っている）")
    return out


def line(name: str, m: dict) -> str:
    return (f"{name:28} {m['total']:4}本 朝{m['per_slot']['morning']:3} 昼{m['per_slot']['noon']:3} "
            f"夜{m['per_slot']['evening']:3}｜宣言型{m['stiff']:2} 話し方NG{m['style_ng']:3} "
            f"重複{m['dup_first']:2} 講義語{m['lecture']:2}｜【地域】{m['area']:2} 「！」{m['exclaim']:3}"
            f" 問い締め{m['interact']:3} 教える言葉{m['teach']:3}")


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    strict = "--strict" in sys.argv
    if not args:
        print(__doc__)
        return 1
    bad = 0
    for path in args:
        try:
            pool = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"{path}: 読めません（{e}）")
            bad += 1
            continue
        name = os.path.basename(path)[len("posts_"):-len(".json")]
        m = audit(pool, name)
        print(line(name, m))
        for p in problems(m):
            print(f"   ⚠️ {p}")
            bad += 1
    return 1 if (strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
