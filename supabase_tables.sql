-- とうこさん Supabase テーブル定義
-- Supabase ダッシュボード → SQL Editor で実行してください

-- サロン情報テーブル（メイン）
create table if not exists salons (
  id               uuid primary key default gen_random_uuid(),
  salon_name       text not null unique,
  threads_username text,
  session_data     text,           -- Playwright セッション（JSON文字列）
  access_token     text,           -- 公式APIトークン（将来用・今は未使用）
  threads_user_id  text,           -- 公式APIユーザーID（将来用・今は未使用）
  stripe_customer_id text,           -- Stripe顧客ID（解約・支払失敗の自動検知用）
  is_active        boolean not null default true,
  created_at       timestamptz not null default now()
);

-- stripe_customer_id を後から追加する場合（既存テーブルへの ALTER）
-- ALTER TABLE salons ADD COLUMN IF NOT EXISTS stripe_customer_id text;

-- 投稿ログテーブル（使用済み投稿の管理）
create table if not exists post_logs (
  id           uuid primary key default gen_random_uuid(),
  salon_id     uuid not null references salons(id) on delete cascade,
  slot         text not null,      -- 'morning' or 'evening'
  post_content text not null,
  posted_at    timestamptz not null default now()
);

-- LINEユーザーテーブル（フォロワー管理）
create table if not exists line_users (
  line_user_id text primary key,
  display_name text,
  created_at   timestamptz not null default now()
);

-- インデックス（投稿ログの検索を高速化）
create index if not exists idx_post_logs_salon_slot on post_logs(salon_id, slot);
