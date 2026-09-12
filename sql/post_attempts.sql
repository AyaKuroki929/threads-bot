-- とうこさんSaaS：投稿の「実行権と結果」を1スロット1行で残す台帳
-- これが無いと、公開できたか確認できない状態（504等）を次の実行に引き継げず、
-- ①二重投稿 ②投稿欠落 のどちらかが必ず起きる。
--
-- 貼り付け先：Supabase ダッシュボード → 左メニュー「SQL Editor」→ New query → 貼り付けて Run
-- 既存データには触りません（表の追加と、post_logs への列＋一意制約の追加だけ）。

-- ── ① 投稿台帳 ───────────────────────────────────────────
create table if not exists public.post_attempts (
  id          uuid primary key default gen_random_uuid(),
  op_id       text not null unique,          -- '<salon_id>:<JSTの日付>:<slot>' 固定ID＝原子的な実行権
  salon_id    uuid not null references public.salons(id),
  jst_date    date not null,
  slot        text not null,
  rev         integer not null default 0,    -- 楽観ロック用。更新は rev 一致が条件
  status      text not null,                 -- running / unknown / published / logged / failed / attention
  parts       jsonb not null default '[]'::jsonb,  -- [{i, hash, creation_id, post_id, status}]
  payload     jsonb,                         -- {texts, original_first, topic_tag, image_url}
  logged      boolean not null default false,
  publisher_user_id text,               -- 投稿を始めたThreadsアカウント。再開時に照合する
  note        text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- 「最後に進捗があった時刻」は必ずDBの時計で打つ。
-- 実行側の時計を信じると、時刻ズレで「他の実行が処理中か」の判定が壊れる。
create or replace function public.post_attempts_touch()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

drop trigger if exists post_attempts_touch_trg on public.post_attempts;
create trigger post_attempts_touch_trg
  before update on public.post_attempts
  for each row execute function public.post_attempts_touch();

create index if not exists post_attempts_slot_idx
  on public.post_attempts (salon_id, jst_date, slot);
create index if not exists post_attempts_open_idx
  on public.post_attempts (status, jst_date)
  where status in ('unknown', 'published', 'running', 'attention');

-- ── ② post_logs の二重記録をDBで拒否する ──────────────────────
-- 応答だけ失われた再送・同時実行の両方を、アプリの照合ではなくDB制約で止める。
-- 既存行は op_id が NULL のままで良い（部分一意インデックスなのでNULLは何件でも許される）。
-- 既に post_attempts を作った後でも足せるように（再実行しても安全）
alter table public.post_attempts add column if not exists publisher_user_id text;

alter table public.post_logs add column if not exists op_id text;
create unique index if not exists post_logs_op_id_uniq
  on public.post_logs (op_id) where op_id is not null;
