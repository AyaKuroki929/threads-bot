#!/usr/bin/env python3
"""ベモーレ自動化ダッシュボード生成スクリプト

各リポジトリの状態ファイルを読み、「今どうなっているか」が一目でわかる
self-contained HTML（dashboard.html）を出力する。
定期的に走らせれば最新状態のダッシュボードに更新できる。

使い方: python3 dashboard.py  → dashboard.html を出力
"""
import json, os, html
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
TB = os.path.dirname(os.path.abspath(__file__))                      # threads-bot
ROOT = os.path.dirname(TB)
IG = os.path.join(ROOT, "instagram-stories-bemolle")


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def jst_str(iso, fmt="%m/%d %H:%M"):
    """ISO文字列(UTCまたはローカル)をJST表示に。失敗時は原文。"""
    if not iso:
        return "—"
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            # last_run.json 等はJSTローカル表記なのでそのまま扱う
            return dt.strftime(fmt)
        return dt.astimezone(JST).strftime(fmt)
    except Exception:
        return str(iso)[:16]


def ago(iso):
    try:
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        h = (datetime.now(JST) - dt.astimezone(JST)).total_seconds() / 3600
        if h < 1:
            return f"{int(h*60)}分前"
        if h < 48:
            return f"{int(h)}時間前"
        return f"{int(h/24)}日前"
    except Exception:
        return ""


# ── データ収集 ───────────────────────────────────────────────
now = datetime.now(JST)
cards = []   # (group, name, status, status_label, metric, sub)
alerts = []  # 要対応サマリー


def status_pill(key):
    return {"good": "稼働中", "warn": "要注意", "crit": "停止/制限", "idle": "待機"}[key]


# --- いいね（ベモーレ・個人） ---
for suf, label in [("", "ベモーレ"), ("_personal", "個人")]:
    pause = load(os.path.join(TB, f"like_pause{suf}.json"))
    liked = load(os.path.join(TB, f"liked_posts{suf}.json"), {}) or {}
    alertst = load(os.path.join(TB, f"like_alert_state{suf}.json"), {}) or {}
    n_liked = len(liked)
    if pause:
        until = jst_str(pause.get("until"), "%m/%d %H:%M")
        st, stl = "crit", f"休止〜{until}"
        sub = pause.get("reason", "")
        alerts.append((f"いいね（{label}）が休止中", f"Meta制限の疑いで {until} まで自動休止。操作不要・自動再開します。", "crit"))
    else:
        st, stl = "good", "稼働中"
        sub = f"最終検知: {ago(alertst.get('last_session_ts'))}" if alertst.get("last_session_ts") else "正常"
    cards.append(("❤️ 自動いいね", f"{label}", st, stl, f"{n_liked}", "アカウントにいいね済み", sub))


# --- Threads投稿（プール残・最終投稿） ---
last_run = load(os.path.join(TB, "last_run.json"), {}) or {}
posts = load(os.path.join(TB, "posts.json"), {}) or {}
pool_total = sum(len(v) for v in posts.values()) if posts else 0
last_post_times = [v for k, v in last_run.items() if k in ("morning", "noon", "evening")]
latest_post = max(last_post_times) if last_post_times else None
pst = "good" if pool_total > 20 else ("warn" if pool_total > 0 else "crit")
if pool_total <= 20 and pool_total > 0:
    alerts.append(("投稿ストックが少なめ", f"残り{pool_total}本。月曜の自動生成で補充されます。", "warn"))
cards.append(("📱 Threads投稿", "ベモーレ本体", pst, "稼働中" if pst=="good" else "残少",
              f"{pool_total}", "投稿ストック（本）",
              f"最終投稿: {jst_str(latest_post)}（{ago(latest_post)}）" if latest_post else ""))


# --- GBP ---
gbp = load(os.path.join(IG, "gbp_state.json"), {}) or {}
gbp_last = gbp.get("last_date", "")
cards.append(("🌐 集客・口コミ", "GBP週2投稿", "good", "稼働中",
              gbp_last or "—", "最終投稿日",
              "Make.com経由で自動投稿（日・水 8:30）"))

# --- 口コミWorker ---
cards.append(("🌐 集客・口コミ", "口コミ導線＋自動返信", "good", "稼働中",
              "24h", "常時受付",
              "QR→アンケート→AI下書き／★5は自動返信"))

# --- IGストーリー ---
cards.append(("📱 SNS投稿", "IGストーリー", "good", "稼働中",
              "毎朝", "7:00 自動投稿",
              "Threads投稿もストーリーに展開（8-9時）"))

# --- 基盤: cookie_refresh ---
cr_log_ts = ""
for p in [os.path.expanduser("~/threads_bot/.cookie_refresh_last_run")]:
    try:
        with open(p) as f:
            cr_log_ts = datetime.fromtimestamp(int(f.read().strip()), JST).strftime("%m/%d %H:%M")
    except Exception:
        pass
cards.append(("🔌 基盤", "Cookie自動更新", "good",
              "稼働中", cr_log_ts or "定期", "最終更新" if cr_log_ts else "毎日12/19時(Mac)",
              "ThreadsのログインCookieをSecretに反映"))


# ── 全体ステータス ───────────────────────────────────────────
n_crit = sum(1 for a in alerts if a[2] == "crit")
n_warn = sum(1 for a in alerts if a[2] == "warn")
if n_crit:
    overall = ("crit", f"{n_crit}件が制限/停止中（対応は自動・確認だけ）")
elif n_warn:
    overall = ("warn", f"{n_warn}件が要注意")
else:
    overall = ("good", "すべて正常に稼働中")


# ── HTML生成 ─────────────────────────────────────────────────
def esc(s):
    return html.escape(str(s))

# グループ順
group_order = ["📱 SNS投稿", "📱 Threads投稿", "❤️ 自動いいね", "🌐 集客・口コミ", "🔌 基盤"]
groups = {}
for c in cards:
    groups.setdefault(c[0], []).append(c)

cards_html = []
for g in group_order:
    if g not in groups:
        continue
    items = []
    for (_, name, st, stl, metric, munit, sub) in groups[g]:
        items.append(f"""
        <div class="card s-{st}">
          <div class="card-top">
            <span class="cname">{esc(name)}</span>
            <span class="pill p-{st}">{esc(stl)}</span>
          </div>
          <div class="metric"><span class="num">{esc(metric)}</span><span class="unit">{esc(munit)}</span></div>
          <div class="sub">{esc(sub)}</div>
        </div>""")
    cards_html.append(f"""
      <section class="grp">
        <h2>{esc(g)}</h2>
        <div class="cards">{''.join(items)}</div>
      </section>""")

alerts_html = ""
if alerts:
    rows = "".join(
        f'<li class="al a-{sev}"><span class="al-t">{esc(t)}</span><span class="al-d">{esc(d)}</span></li>'
        for (t, d, sev) in sorted(alerts, key=lambda x: 0 if x[2]=="crit" else 1)
    )
    alerts_html = f'<div class="attention"><div class="att-h">要対応・注意</div><ul>{rows}</ul></div>'
else:
    alerts_html = '<div class="attention ok"><div class="att-h">要対応なし</div><p>今、あなたが手を動かす必要のあるものはありません。</p></div>'

body = f"""<style>
  :root {{
    --ground:#F8F5F6; --card:#FFFFFF; --ink:#2C2328; --muted:#8A7B81;
    --line:#ECE4E6; --brand:#6E4152;
    --good:#4F7D57; --warn:#B8862E; --crit:#B04A42;
    --good-bg:#EDF3EE; --warn-bg:#F7EFDD; --crit-bg:#F6E7E5;
  }}
  *{{box-sizing:border-box;}}
  .wrap{{
    max-width:900px; margin:0 auto; padding:26px 18px 60px;
    font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
    color:var(--ink); background:var(--ground); -webkit-font-smoothing:antialiased;
    font-variant-numeric:tabular-nums;
  }}
  .serif{{font-family:"Hiragino Mincho ProN","Yu Mincho",serif;}}
  header.top{{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:18px;}}
  .title{{font-size:1.5rem;font-weight:600;letter-spacing:.02em;color:var(--brand);}}
  .asof{{font-size:.78rem;color:var(--muted);}}
  .tabs{{display:flex;gap:10px;margin-bottom:22px;flex-wrap:wrap;}}
  .tab{{display:inline-block;padding:11px 18px;border-radius:12px;font-size:.95rem;font-weight:600;
    text-decoration:none;border:1px solid var(--line);color:var(--brand);background:var(--card);}}
  .tab-on{{background:var(--brand);color:#fff;border-color:var(--brand);}}
  a.tab:hover{{background:#F1EAEC;}}
  .overall{{display:flex;align-items:center;gap:12px;padding:16px 18px;border-radius:14px;margin-bottom:22px;
    border:1px solid var(--line);background:var(--card);}}
  .dot{{width:12px;height:12px;border-radius:50%;flex:0 0 auto;}}
  .o-good .dot{{background:var(--good);}} .o-warn .dot{{background:var(--warn);}} .o-crit .dot{{background:var(--crit);}}
  .overall .lbl{{font-size:1.05rem;font-weight:600;}}
  .o-good{{border-color:#D4E4D8;}} .o-warn{{border-color:#EAD9B4;}} .o-crit{{border-color:#E7C7C3;}}

  .attention{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:26px;}}
  .attention.ok .att-h{{color:var(--good);}}
  .att-h{{font-size:.8rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin-bottom:10px;font-weight:600;}}
  .attention ul{{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px;}}
  .al{{padding-left:12px;border-left:3px solid var(--line);}}
  .al.a-crit{{border-color:var(--crit);}} .al.a-warn{{border-color:var(--warn);}}
  .al-t{{display:block;font-weight:600;font-size:.95rem;}}
  .al-d{{display:block;font-size:.85rem;color:var(--muted);margin-top:2px;line-height:1.6;}}
  .attention.ok p{{margin:0;color:var(--muted);font-size:.9rem;}}

  .grp{{margin-bottom:24px;}}
  .grp h2{{font-size:.82rem;letter-spacing:.1em;color:var(--muted);font-weight:600;margin:0 0 10px;}}
  .cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 16px;position:relative;overflow:hidden;}}
  .card::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:transparent;}}
  .card.s-good::before{{background:var(--good);}} .card.s-warn::before{{background:var(--warn);}}
  .card.s-crit::before{{background:var(--crit);}} .card.s-idle::before{{background:var(--muted);}}
  .card-top{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:10px;}}
  .cname{{font-weight:600;font-size:.98rem;}}
  .pill{{font-size:.72rem;padding:3px 9px;border-radius:999px;white-space:nowrap;font-weight:600;}}
  .p-good{{background:var(--good-bg);color:var(--good);}}
  .p-warn{{background:var(--warn-bg);color:var(--warn);}}
  .p-crit{{background:var(--crit-bg);color:var(--crit);}}
  .p-idle{{background:#EEE9EA;color:var(--muted);}}
  .metric{{display:flex;align-items:baseline;gap:7px;margin-bottom:6px;}}
  .num{{font-size:1.7rem;font-weight:600;letter-spacing:.01em;}}
  .unit{{font-size:.78rem;color:var(--muted);}}
  .sub{{font-size:.8rem;color:var(--muted);line-height:1.6;}}
  footer{{margin-top:30px;font-size:.76rem;color:var(--muted);line-height:1.8;border-top:1px solid var(--line);padding-top:16px;}}
  @media (max-width:520px){{ .cards{{grid-template-columns:1fr;}} .num{{font-size:1.5rem;}} }}
</style>

<div class="wrap">
  <header class="top">
    <div class="title serif">ベモーレ 自動化ダッシュボード</div>
    <div class="asof">{now.strftime('%Y年%m月%d日 %H:%M')} 時点</div>
  </header>
  <nav class="tabs">
    <span class="tab tab-on">📊 ダッシュボード</span>
    <a class="tab" href="manual.html">📖 運用マニュアル（困ったとき）</a>
  </nav>

  <div class="overall o-{overall[0]}">
    <span class="dot"></span>
    <span class="lbl">{esc(overall[1])}</span>
  </div>

  {alerts_html}

  {''.join(cards_html)}

  <footer>
    このダッシュボードは各自動化の状態ファイルから生成したスナップショットです（{now.strftime('%m/%d %H:%M')}時点）。<br>
    最新化するには <code>python3 dashboard.py</code> を再実行してください。詳しい運用は AUTOMATION_OPS_MANUAL.md を参照。
  </footer>
</div>"""

with open(os.path.join(TB, "dashboard.html"), "w", encoding="utf-8") as f:
    f.write(body)
print(f"dashboard.html を生成しました（全体: {overall[0]} / 要対応 {len(alerts)}件）")


# ── 運用マニュアルも同じサイトにHTML化（manual.html）─────────────
manual_md_path = os.path.join(TB, "AUTOMATION_OPS_MANUAL.md")
manual_src = ""
try:
    with open(manual_md_path, encoding="utf-8") as f:
        manual_src = f.read()
except Exception:
    manual_src = ""

if manual_src:
    try:
        import markdown  # CIでpip install。表・コードに対応
        manual_body = markdown.markdown(manual_src, extensions=["tables", "fenced_code", "toc"])
    except Exception:
        # markdown未導入環境は最低限そのまま表示（読めればよい）
        manual_body = "<pre style='white-space:pre-wrap'>" + esc(manual_src) + "</pre>"

    manual_html = f"""<style>
  :root {{ --ground:#F8F5F6; --card:#FFFFFF; --ink:#2C2328; --muted:#8A7B81;
    --line:#ECE4E6; --brand:#6E4152; --good:#4F7D57; --warn:#B8862E; --crit:#B04A42; }}
  *{{box-sizing:border-box;}}
  body{{margin:0;background:var(--ground);}}
  .doc{{max-width:820px;margin:0 auto;padding:26px 20px 70px;
    font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN","Yu Gothic",Meiryo,system-ui,sans-serif;
    color:var(--ink);line-height:1.8;-webkit-font-smoothing:antialiased;}}
  .tabs{{display:flex;gap:10px;margin-bottom:24px;flex-wrap:wrap;}}
  .tab{{display:inline-block;padding:11px 18px;border-radius:12px;font-size:.95rem;font-weight:600;
    text-decoration:none;border:1px solid var(--line);color:var(--brand);background:var(--card);}}
  .tab-on{{background:var(--brand);color:#fff;border-color:var(--brand);}}
  a.tab:hover{{background:#F1EAEC;}}
  .doc h1{{font-family:"Hiragino Mincho ProN",serif;color:var(--brand);font-size:1.55rem;
    border-bottom:2px solid var(--line);padding-bottom:12px;}}
  .doc h2{{font-size:1.15rem;margin-top:34px;color:var(--ink);border-left:4px solid var(--brand);padding-left:10px;}}
  .doc h3{{font-size:1rem;color:var(--brand);margin-top:22px;}}
  .doc table{{border-collapse:collapse;width:100%;margin:14px 0;font-size:.9rem;display:block;overflow-x:auto;}}
  .doc th,.doc td{{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top;}}
  .doc th{{background:#F1EAEC;font-weight:600;white-space:nowrap;}}
  .doc code{{background:#F1EAEC;padding:2px 6px;border-radius:5px;font-size:.86em;}}
  .doc blockquote{{border-left:3px solid var(--brand);margin:14px 0;padding:6px 14px;color:var(--muted);background:var(--card);}}
  .doc hr{{border:0;border-top:1px solid var(--line);margin:28px 0;}}
  .doc li{{margin:4px 0;}}
</style>
<div class="doc">
  <nav class="tabs">
    <a class="tab" href="index.html">📊 ダッシュボード</a>
    <span class="tab tab-on">📖 運用マニュアル</span>
  </nav>
  {manual_body}
</div>"""
    with open(os.path.join(TB, "manual.html"), "w", encoding="utf-8") as f:
        f.write(manual_html)
    print("manual.html も生成しました")
