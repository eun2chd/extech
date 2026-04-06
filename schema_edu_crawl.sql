-- edu / edu_applicant 용 크롤 진행 테이블 + 배치 upsert RPC
-- 전제: public.edu, public.edu_applicant 테이블이 이미 존재 (사용자 정의 스키마)
-- Supabase SQL Editor에서 실행

-- ── 진행 (member_crawl_progress 와 별도)
create table if not exists public.edu_list_crawl_progress (
  id text primary key,
  next_page integer not null default 1 check (next_page >= 1),
  updated_at timestamptz not null default now()
);

alter table public.edu_list_crawl_progress enable row level security;

insert into public.edu_list_crawl_progress (id, next_page)
values ('edu_list', 1)
on conflict (id) do nothing;

grant select, insert, update on public.edu_list_crawl_progress to service_role;

-- 신청자 목록: 현재 크롤 중인 교육 seq + 목록 페이지
create table if not exists public.edu_applicant_crawl_progress (
  id text primary key,
  target_edu_seq integer,
  next_page integer not null default 1 check (next_page >= 1),
  updated_at timestamptz not null default now()
);

alter table public.edu_applicant_crawl_progress enable row level security;

insert into public.edu_applicant_crawl_progress (id, target_edu_seq, next_page)
values ('default', null, 1)
on conflict (id) do nothing;

grant select, insert, update on public.edu_applicant_crawl_progress to service_role;

-- ── 교육 목록 배치 upsert (seq 기준)
create or replace function public.upsert_edu_batch(p_rows jsonb)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  n int := 0;
begin
  insert into public.edu (
    seq,
    title,
    region,
    edu_start_date,
    edu_end_date,
    edu_time,
    apply_start_date,
    apply_end_date,
    capacity,
    current_count,
    category,
    created_at,
    updated_at,
    crawled_at
  )
  select
    (r->>'seq')::integer,
    coalesce(nullif(trim(r->>'title'), ''), '[제목없음]'),
    nullif(trim(r->>'region'), ''),
    case
      when r->>'edu_start_date' is null or trim(r->>'edu_start_date') = '' then null
      else (r->>'edu_start_date')::date
    end,
    case
      when r->>'edu_end_date' is null or trim(r->>'edu_end_date') = '' then null
      else (r->>'edu_end_date')::date
    end,
    nullif(trim(r->>'edu_time'), ''),
    case
      when r->>'apply_start_date' is null or trim(r->>'apply_start_date') = '' then null
      else (r->>'apply_start_date')::date
    end,
    case
      when r->>'apply_end_date' is null or trim(r->>'apply_end_date') = '' then null
      else (r->>'apply_end_date')::date
    end,
    nullif(trim(r->>'capacity'), '')::integer,
    nullif(trim(r->>'current_count'), '')::integer,
    nullif(trim(r->>'category'), ''),
    case
      when r->>'created_at' is null or trim(r->>'created_at') = '' then null
      else (r->>'created_at')::timestamptz
    end,
    case
      when r->>'updated_at' is null or trim(r->>'updated_at') = '' then null
      else (r->>'updated_at')::timestamptz
    end,
    now()
  from jsonb_array_elements(p_rows) as r
  on conflict (seq) do update set
    title = excluded.title,
    region = excluded.region,
    edu_start_date = excluded.edu_start_date,
    edu_end_date = excluded.edu_end_date,
    edu_time = excluded.edu_time,
    apply_start_date = excluded.apply_start_date,
    apply_end_date = excluded.apply_end_date,
    capacity = excluded.capacity,
    current_count = excluded.current_count,
    category = excluded.category,
    updated_at = coalesce(excluded.updated_at, now()),
    crawled_at = now();

  get diagnostics n = row_count;
  return n;
end;
$$;

grant execute on function public.upsert_edu_batch(jsonb) to service_role;

-- ── 신청자 배치 upsert (edu.seq 로 edu_id 조회, (edu_id,user_id) 유일)
create or replace function public.upsert_edu_applicant_batch(
  p_edu_seq integer,
  p_rows jsonb
)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  n int := 0;
begin
  insert into public.edu_applicant (
    edu_id,
    user_id,
    name,
    phone,
    branch,
    type,
    apply_status,
    exam_status,
    payment_status,
    applicant_no,
    created_at,
    updated_at,
    crawled_at
  )
  select
    e.id,
    trim(r->>'user_id'),
    nullif(trim(r->>'name'), ''),
    nullif(trim(r->>'phone'), ''),
    nullif(trim(r->>'branch'), ''),
    nullif(trim(r->>'type'), ''),
    nullif(trim(r->>'apply_status'), ''),
    nullif(trim(r->>'exam_status'), ''),
    nullif(trim(r->>'payment_status'), ''),
    nullif(trim(r->>'applicant_no'), '')::integer,
    case
      when r->>'created_at' is null or trim(r->>'created_at') = '' then null
      else (r->>'created_at')::timestamptz
    end,
    case
      when r->>'updated_at' is null or trim(r->>'updated_at') = '' then null
      else (r->>'updated_at')::timestamptz
    end,
    now()
  from jsonb_array_elements(p_rows) as r
  inner join public.edu e on e.seq = p_edu_seq
  where length(trim(r->>'user_id')) > 0
  on conflict (edu_id, user_id) do update set
    name = excluded.name,
    phone = excluded.phone,
    branch = excluded.branch,
    type = excluded.type,
    apply_status = excluded.apply_status,
    exam_status = excluded.exam_status,
    payment_status = excluded.payment_status,
    applicant_no = excluded.applicant_no,
    updated_at = coalesce(excluded.updated_at, now()),
    crawled_at = now();

  get diagnostics n = row_count;
  return n;
end;
$$;

grant execute on function public.upsert_edu_applicant_batch(integer, jsonb) to service_role;

-- Edge 가 edu 조회·RPC upsert 시 필요 (이미 부여돼 있으면 무시)
grant select, insert, update on public.edu to service_role;
grant select, insert, update, delete on public.edu_applicant to service_role;
