-- legacy_edu / legacy_edu_applicant 용 크롤 진행 테이블 + 배치 upsert RPC
-- 전제: public.legacy_edu, public.legacy_edu_applicant 가 있음 → 없으면 먼저 schema_edu.sql 실행
-- 타임존: RPC 는 Asia/Seoul; 신청자 created_at/updated_at 문자열은 한국 현지 시각으로 해석
-- Supabase SQL Editor에서 실행

-- 기존 RPC 제거 후 재생성 (시그니처 동일)
drop function if exists public.upsert_edu_applicant_batch(integer, jsonb);
drop function if exists public.upsert_edu_batch(jsonb);

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

-- ── 교육 목록 배치 upsert (seq 기준) → legacy_edu
create or replace function public.upsert_edu_batch(p_rows jsonb)
returns integer
language plpgsql
security invoker
set search_path = public
set timezone = 'Asia/Seoul'
as $$
declare
  n int := 0;
begin
  insert into public.legacy_edu (
    seq,
    display_no,
    region,
    title,
    unit,
    edu_period,
    apply_period,
    capacity,
    category,
    registered_at,
    crawled_at
  )
  select
    (r->>'seq')::integer,
    nullif(trim(r->>'display_no'), ''),
    nullif(trim(r->>'region'), ''),
    coalesce(nullif(trim(r->>'title'), ''), '[제목없음]'),
    nullif(trim(r->>'unit'), ''),
    nullif(trim(r->>'edu_period'), ''),
    nullif(trim(r->>'apply_period'), ''),
    nullif(trim(r->>'capacity'), ''),
    nullif(trim(r->>'category'), ''),
    nullif(trim(r->>'registered_at'), ''),
    now()
  from jsonb_array_elements(p_rows) as r
  on conflict (seq) do update set
    display_no = excluded.display_no,
    region = excluded.region,
    title = excluded.title,
    unit = excluded.unit,
    edu_period = excluded.edu_period,
    apply_period = excluded.apply_period,
    capacity = excluded.capacity,
    category = excluded.category,
    registered_at = excluded.registered_at,
    crawled_at = now();

  get diagnostics n = row_count;
  return n;
end;
$$;

grant execute on function public.upsert_edu_batch(jsonb) to service_role;

-- ── 신청자 배치 upsert (legacy_edu.seq 로 edu_id 조회) → legacy_edu_applicant
create or replace function public.upsert_edu_applicant_batch(
  p_edu_seq integer,
  p_rows jsonb
)
returns integer
language plpgsql
security invoker
set search_path = public
set timezone = 'Asia/Seoul'
as $$
declare
  n int := 0;
begin
  insert into public.legacy_edu_applicant (
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
    case
      when trim(coalesce(r->>'applicant_no', '')) ~ '^\d+$'
      then trim(r->>'applicant_no')::integer
      else null
    end,
    case
      when r->>'created_at' is null or trim(r->>'created_at') = '' then null
      else (
        trim(r->>'created_at')::timestamp without time zone
        at time zone 'Asia/Seoul'
      )
    end,
    case
      when r->>'updated_at' is null or trim(r->>'updated_at') = '' then null
      else (
        trim(r->>'updated_at')::timestamp without time zone
        at time zone 'Asia/Seoul'
      )
    end,
    now()
  from jsonb_array_elements(p_rows) as r
  inner join public.legacy_edu e on e.seq = p_edu_seq
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

grant select, insert, update on public.legacy_edu to service_role;
grant select, insert, update, delete on public.legacy_edu_applicant to service_role;

-- schema_edu.sql 을 건너뛴 경우에도 동일 설정 (이미 설정됐으면 그대로)
alter role service_role set timezone to 'Asia/Seoul';
