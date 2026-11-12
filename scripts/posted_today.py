#!/usr/bin/env python3
"""今日のその枠で、稼働中のアカウントが全部投稿済みかを Supabase で確かめる。

なぜ要るか：
heartbeat は last_run.json を見て「投稿したか」を判断している。ところがこの
ファイルは git に置いてあり、GitHub への push が失敗すると更新されない。
2026-11-13、GitHub が Internal Server Error を返して push が3回とも弾かれ、
12件とも投稿できていたのに記録だけが昨日のまま残った。このままだと heartbeat が
「未投稿」と誤って判断し、同じ投稿をもう一度出すところだった。

投稿したかどうかの正本は Supabase の post_logs（投稿のたびに1行入る）。
記録ファイルが古いときは、こちらを見て確かめる。

  python3 scripts/posted_today.py morning
  → 終了コード 0：稼働中のアカウントが全部、今日その枠で投稿済み
    終了コード 1：1つでも未投稿、または確認できなかった（安全側に倒す）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    slot = sys.argv[1] if len(sys.argv) > 1 else ""
    if slot not in ("morning", "noon", "evening"):
        print(f"[posted_today] 枠の指定が不正です: {slot!r}")
        return 1
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")):
        print("[posted_today] Supabaseの接続情報が無いので確認できません（安全側に倒します）")
        return 1
    try:
        from post_saas import already_posted_today, get_active_salons
    except Exception as e:                     # noqa: BLE001
        print(f"[posted_today] 読み込みに失敗: {str(e)[:150]}")
        return 1

    try:
        salons = get_active_salons()
    except Exception as e:                     # noqa: BLE001
        print(f"[posted_today] 稼働中のアカウントを取れません: {str(e)[:150]}")
        return 1
    if not salons:
        print("[posted_today] 稼働中のアカウントが0件（確認できないので安全側に倒します）")
        return 1

    missing = []
    for s in salons:
        name = s.get("salon_name") or s.get("id")
        try:
            done = already_posted_today(s["id"], slot)
        except Exception as e:                 # noqa: BLE001
            print(f"[posted_today] {name}: 確認に失敗 {str(e)[:100]}")
            return 1
        if not done:
            missing.append(str(name))

    if missing:
        print(f"[posted_today] {slot}: 未投稿 {len(missing)}件 → {', '.join(missing[:5])}")
        return 1
    print(f"[posted_today] {slot}: 稼働中の{len(salons)}件すべて投稿済み（Supabaseの記録で確認）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
