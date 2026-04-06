-- members_crawled + batch insert (seq 중복 시 건너뜀)
-- Supabase SQL Editor에서 실행

create table if not exists public.members_crawled (
  id bigserial primary key,
  seq integer not null,
  num integer,
  login_id text,
  social_type text,
  name text,
  phone text,
  email text,
  join_date date,
  status text,
  memo text,
  created_at timestamptz not null default now(),
  constraint members_crawled_seq_key unique (seq),
  constraint members_crawled_social_type_check check (
    social_type is null or social_type in ('kakao', 'naver')
  )
);

create index if not exists members_crawled_join_date_idx
  on public.members_crawled (join_date desc);

alter table public.members_crawled enable row level security;

-- service_role 은 RLS 우회 (크롤러·Actions)

create or replace function public.insert_members_crawled_batch(p_rows jsonb)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  n int := 0;
begin
  insert into public.members_crawled (
    seq, num, login_id, social_type, name, phone, email, join_date, status, memo
  )
  select
    (r->>'seq')::integer,
    nullif(r->>'num', '')::integer,
    nullif(r->>'login_id', ''),
    nullif(trim(r->>'social_type'), ''),
    nullif(r->>'name', ''),
    nullif(r->>'phone', ''),
    nullif(r->>'email', ''),
    case
      when r->>'join_date' is null or trim(r->>'join_date') = '' then null
      else (r->>'join_date')::date
    end,
    nullif(r->>'status', ''),
    nullif(r->>'memo', '')
  from jsonb_array_elements(p_rows) as r
  on conflict (seq) do nothing;

  get diagnostics n = row_count;
  return n;
end;
$$;

grant execute on function public.insert_members_crawled_batch(jsonb) to service_role;
