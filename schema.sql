-- Run in Supabase SQL editor (or migration) once.
create table if not exists public.crawl_rows (
  id uuid primary key default gen_random_uuid(),
  external_id text,
  row_data jsonb not null default '{}'::jsonb,
  scraped_at timestamptz not null default now(),
  unique (external_id)
);

create index if not exists crawl_rows_scraped_at_idx on public.crawl_rows (scraped_at desc);

-- Optional: enable RLS so only the service role (used by this crawler) can access data.
-- The service role key bypasses RLS when inserting from GitHub Actions.
alter table public.crawl_rows enable row level security;

alter role service_role set timezone to 'Asia/Seoul';
