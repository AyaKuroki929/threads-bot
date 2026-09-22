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

-- 支払い失敗リマインドの送信台帳（2026-09-22・うらかたさんと同じ流れをとうこさん側に移植）
-- 1行 = 請求書 × 通番。主キーで同じ行は2度作れない＝同時に2人が動いても承認依頼・送信は1回だけ。
-- status: pending_notify（彩さんへの承認依頼を送る前）/ pending（承認待ち）/ sending（送信中）
--         / sent（本人へ送付済み）/ unknown（送ったか不明・人の確認待ち）/ escalated（手動フォロー通知済み）
create table if not exists payment_reminder_attempts (
  invoice_id    text not null,
  attempt       int  not null,
  status        text not null,
  nonce         text,                 -- 承認URLの合言葉の元（送ったら消す）
  lock_id       text,                 -- 送信権を取った実行の印
  customer_id   text,
  line_user_id  text,
  created_at    timestamptz not null default now(),
  sent_at       timestamptz,
  note          text,
  primary key (invoice_id, attempt)
);
