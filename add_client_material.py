"""クライアント素材の登録を1コマンドで確実にやる（シート学習＋投稿プール＋検証）

彩さんにクライアントから「生の声」「体験談」「哲学」が届いたら、必ずこのスクリプトで登録する。

なぜ1コマンドか：
  2026-07-18、つばめの巣のお客様の声2件を「投稿化とシート登録をします」と宣言しながら、
  プール追加だけしてシート書き込みを実行せず「登録済み✅」と報告する事故があった。
  工程が分かれていると片方を忘れても気づけない。このスクリプトは
  ①シート自由記入欄へ追記 → 読み直して実在検証
  ②（あれば）投稿プールへ追加 → 再読込検証 → private repoへ [sheet-ok] 付きでpush → リモート反映検証
  を1回で実行し、どちらかが失敗したら全体を失敗させる。

使い方:
  python3 add_client_material.py --salon piccolo \
      --material "【お客様の声（YYYY-MM-DD 提供）】…投稿に活用する。" \
      --post evening "投稿本文…" --post morning "別の投稿本文…"

  --salon    : ThreadsのID断片（シートのID列に部分一致。例 piccolo / tubame / yumika）
  --material : シートの自由記入欄に追記する学習素材（必須。日付と出典を【】で書く）
  --post     : slot(morning/noon/evening) と投稿本文のペア。複数指定可・省略可
  --dry-run  : 書き込みせず、対象行・追記内容・投稿の検証だけ行う
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

SPREADSHEET_ID = "1Af6ZnH7Ghzn1APpVrFy5nFlftNaIX5YOeSvfFjSdU-U"
SA_FILE = str(Path(__file__).parent / "google_service_account.json")
FREE_COL = "その他・自由記入（伝えておきたいこと）"
TID_COL = "Threadsのアカウント名（@から始まるID）"
POSTS_REPO = "AyaKuroki929/saas-posts"
VALID_SLOTS = {"morning", "noon", "evening"}
MAX_LEN = 350  # generate側の単発上限に合わせる


def normalize_tid(raw: str) -> str:
    s = str(raw).strip().lstrip("@＠")
    return s.lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--salon", required=True)
    ap.add_argument("--material", required=True)
    ap.add_argument("--post", nargs=2, action="append", default=[], metavar=("SLOT", "TEXT"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # 事前検証（書き込む前に全部確かめる）
    if not a.material.strip().startswith("【"):
        sys.exit("❌ --material は【日付・出典】から書き始める（例:【お客様の声（2026-08-03 ◯◯さん提供）】…）")
    for slot, text in a.post:
        if slot not in VALID_SLOTS:
            sys.exit(f"❌ slot が不正: {slot}（morning/noon/evening）")
        if len(text) > MAX_LEN:
            sys.exit(f"❌ {slot} の投稿が{len(text)}字（上限{MAX_LEN}字）")

    import gspread
    gc = gspread.service_account(filename=SA_FILE, scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ])
    ws = gc.open_by_key(SPREADSHEET_ID).get_worksheet(0)
    headers = ws.row_values(1)
    tid_idx = headers.index(TID_COL) + 1
    free_idx = headers.index(FREE_COL) + 1
    ids = ws.col_values(tid_idx)
    rows = [i + 1 for i, v in enumerate(ids) if a.salon.lower() in v.lower()]
    if len(rows) != 1:
        sys.exit(f"❌ --salon '{a.salon}' に一致する行が {len(rows)} 件（1件になるよう指定する）: "
                 f"{[ids[r-1] for r in rows]}")
    row = rows[0]
    tid = normalize_tid(ids[row - 1])
    safe_name = re.sub(r"[^\w\-]", "_", tid)
    print(f"対象: {ids[row-1]}（シート{row}行目 / posts_{safe_name}.json）")

    if a.dry_run:
        print(f"[DRY_RUN] シート追記予定: {a.material[:60]}…")
        for slot, text in a.post:
            print(f"[DRY_RUN] {slot} へ追加予定（{len(text)}字）")
        print("[DRY_RUN] 書き込みは行いません")
        return 0

    # ① シート自由記入欄へ追記 → 読み直し検証
    cur = ws.cell(row, free_idx).value or ""
    new = (cur.rstrip() + "\n\n" if cur.strip() else "") + a.material.strip()
    ws.update_cell(row, free_idx, new)
    back = ws.cell(row, free_idx).value or ""
    if a.material.strip() not in back:
        sys.exit("❌ シート書き込みの読み直し検証に失敗（追記が実在しない）")
    print(f"✅ シート学習: 追記＋読み直し検証OK（自由記入欄 {len(back)}字）")

    # ② 投稿プールへ追加 → push → リモート検証
    if a.post:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "saas-posts"
            subprocess.run(["gh", "repo", "clone", POSTS_REPO, str(repo), "--", "-q", "--depth", "1"],
                           check=True)
            pool = repo / f"posts_{safe_name}.json"
            if not pool.exists():
                sys.exit(f"❌ プールが無い: {pool.name}（シートには追記済み。プール名を確認して再実行）")
            d = json.loads(pool.read_text(encoding="utf-8"))
            for slot, text in a.post:
                existing = [x if isinstance(x, str) else x[0] for x in d.get(slot, [])]
                if text in existing:
                    print(f"⏭️ {slot}: 同一投稿が既にあるためスキップ")
                    continue
                d.setdefault(slot, []).append(text)
                print(f"✅ プール: {slot} へ追加（{len(text)}字 → {len(d[slot])}本）")
            pool.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            # 再読込検証
            d2 = json.loads(pool.read_text(encoding="utf-8"))
            for slot, text in a.post:
                assert text in [x if isinstance(x, str) else x[0] for x in d2.get(slot, [])]
            subprocess.run(["git", "-C", str(repo), "add", pool.name], check=True)
            r = subprocess.run(["git", "-C", str(repo),
                                "-c", "user.name=Aya Kuroki",
                                "-c", "user.email=nailsalon.flat@gmail.com",
                                "commit", "-q", "-m",
                                f"[sheet-ok] {safe_name}: クライアント素材を投稿化（シート学習済み）"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print("⏭️ プール: 変更なし（全てスキップ済み）")
            else:
                subprocess.run(["git", "-C", str(repo), "push", "-q", "origin", "main"], check=True)
                local = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                       capture_output=True, text=True).stdout.strip()
                remote = subprocess.run(["git", "-C", str(repo), "ls-remote", "origin", "main"],
                                        capture_output=True, text=True).stdout.split()[0]
                if local != remote:
                    sys.exit("❌ push後のリモート検証に失敗")
                print(f"✅ プール: push＋リモート反映検証OK（{local[:7]}）")

    print("\n🏁 完了: シート学習と投稿プールの両方が実物検証済みです")
    return 0


if __name__ == "__main__":
    sys.exit(main())
