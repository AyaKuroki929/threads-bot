#!/usr/bin/env python3
"""状態ファイルを origin/main の中身と突き合わせ、スロットごとに新しい方を残す。

pull の前にこれを掛けておくと、同じ時刻に2つの実行が重なっても
①競合しない（手元の中身がすでに相手の値を含んでいる）
②古い方で上書きして記録を失わない
の両方になる。2026-11-17 の競合マーカー混入は、この工程が無く
`--autostash` に任せていたことが原因だった。

対象は {スロット: "YYYY-MM-DD HH:MM:SS"} の last_run 系と、
{スロット: {"posted_at": ...}} の last_posted 系だけ。他の形は触らない。
"""
import json
import subprocess
import sys


def remote(path):
    subprocess.run(["git", "fetch", "-q", "origin", "main"], capture_output=True)
    r = subprocess.run(["git", "show", f"FETCH_HEAD:{path}"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception as e:
        print(f"[merge_state] {path}: origin/main 側が読めないので手元を使う（{e}）")
        return None


def newer(a, b):
    """新しい方を返す。形が違えば手元(a)を優先する。"""
    if isinstance(a, str) and isinstance(b, str):
        return a if a >= b else b
    if isinstance(a, dict) and isinstance(b, dict):
        pa, pb = a.get("posted_at", ""), b.get("posted_at", "")
        return a if pa >= pb else b
    return a


def main(paths):
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                local = json.load(f)
        except Exception as e:
            print(f"[merge_state] {path}: 手元が読めないので触らない（{e}）")
            continue
        if not isinstance(local, dict):
            continue
        rem = remote(path)
        if not isinstance(rem, dict):
            continue
        changed = []
        for k, rv in rem.items():
            if k not in local:
                local[k] = rv
                changed.append(f"{k}（手元に無かったので足す）")
            else:
                picked = newer(local[k], rv)
                if picked is not local[k]:
                    local[k] = picked
                    changed.append(f"{k}（origin の方が新しい）")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(local, f, ensure_ascii=False, indent=2)
        print(f"[merge_state] {path}: " + (", ".join(changed) if changed else "手元が最新"))
    return 0


if __name__ == "__main__":
    sys.exit(main([a for a in sys.argv[1:] if not a.startswith("--")]))
