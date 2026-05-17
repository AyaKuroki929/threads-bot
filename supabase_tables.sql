-- とうこさん Supabase テーブル定義
-- Supabase ダッシュボード → SQL Editor で実行してください

-- サロン情報テーブル（メイン）
create table if not exists salons (
  id               uuid primary key default gen_random_uuid(),
  salon_name       text not null unique,
  threads_username text,
  session_data     text,           -- 廃止（Playwright セッション・現在は未使用）
  access_token     text,           -- 公式 Threads API アクセストークン（OAuth コールバックで保存）
  threads_user_id  text,           -- 公式 Threads API ユーザーID（post_saas.py で使用）
  stripe_customer_id text,         -- Stripe顧客ID（解約・支払失敗の自動検知用）
  stripe_subscription_id text,     -- Stripe サブスクリプションID
  token_expires_at timestamptz,    -- アクセストークン有効期限（OAuth コールバックで保存）
  is_active        boolean not null default true,
  created_at       timestamptz not null default now()
);

-- 既存テーブルへのカラム追加（初回 CREATE 後に差分適用する場合）
-- ALTER TABLE salons ADD COLUMN IF NOT EXISTS stripe_customer_id text;
-- ALTER TABLE salons ADD COLUMN IF NOT EXISTS stripe_subscription_id text;
-- ALTER TABLE salons ADD COLUMN IF NOT EXISTS token_expires_at timestamptz;

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
