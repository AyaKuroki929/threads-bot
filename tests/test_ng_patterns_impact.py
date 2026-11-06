#!/usr/bin/env python3
"""NG語を足したときに、在庫の投稿がどれだけ落ちるかを実データで測る。

2026-11-06の事故：ダッシュ「——」「—」をNG語に足したところ、生成AIがダッシュを
自然に使うため、ファミリエの morning で生成した15本すべてが落ちて在庫を1本も
足せなかった。NG語を足す前に「既存在庫で何本落ちるか」を測っていれば事前に分かった
（ファミリエは42/429＝10%・うらかたさんは15/456＝3%がダッシュ入りだった）。

ここで見るのは **NG語だけ**。形や長さの基準（1部目の字数など）は後から変わっていて
既存在庫の半分近くが引っかかるため、全体の落ちる率では検知できない。

⚠️ **1本でも落ちたら失敗**にしている。今の在庫でNG語に当たる投稿は0本なので、
増えたということは直前に足したNG語が効きすぎている（＝生成しても捨てられて在庫が枯れる）。
その場合は「落として捨てる」のではなく、botlib.strip_dashes のように**直して通す**実装にする。
本当に捨てるべきNG語（薬機法違反・プロンプト漏れ等）を足したときだけ、LIMIT_POSTS を
意図して上げる（上げた理由をコメントに残す）。
"""
import glob
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
from botlib import validate_post_content, strip_dashes, NG_CONTENT_PATTERNS  # noqa: E402

LIMIT_POSTS = 0        # 今の在庫でNG語に当たるのは0本。増えたら足したNG語が効きすぎている


def main():
    total = bad = 0
    reasons = {}
    for path in sorted(glob.glob(os.path.join(BASE, "posts_saas", "posts_*.json"))):
        name = os.path.basename(path)[6:-5]
        if name.startswith(("toko_test", "て", "テスト")):
            continue
        try:
            d = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(d, dict):
            continue
        for slot in ("morning", "noon", "evening"):
            for p in d.get(slot, []):
                total += 1
                # 投稿側と同じ順序：先に直せるものを直してから検証する
                r = validate_post_content(strip_dashes(p))
                if r and "短すぎる" not in r and "長すぎる" not in r and "part数" not in r:
                    bad += 1
                    key = r.split("「")[0]
                    reasons[key] = reasons.get(key, 0) + 1
    if not total:
        print("在庫が読めませんでした（公開リポに posts_saas が無い？）", file=sys.stderr)
        return 1
    pct = bad * 100.0 / total
    print(f"在庫 {total}本中、NG語で落ちるのは {bad}本（{pct:.1f}%）／NG語の数 {len(NG_CONTENT_PATTERNS)}個")
    for k, n in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}本")
    if bad > LIMIT_POSTS:
        print(f"\n::error::NG語で落ちる投稿が{bad}本（上限{LIMIT_POSTS}本）あります。"
              f"直前に足したNG語が効きすぎていないか確認してください。"
              f"捨てるのではなく botlib.strip_dashes のように直して通す実装にするか、"
              f"本当に捨てるべきNG語なら tests/test_ng_patterns_impact.py の LIMIT_POSTS を"
              f"理由つきで上げてください（2026-11-06 ダッシュを捨てて生成15本が全滅した再発防止）",
              file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
