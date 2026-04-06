-- 교육 1 : N 신청자 — 물리 테이블 2개 (정규화)
--   legacy_edu              … 교육 마스터 (고유 seq = 신청자 목록 URL 의 el_seq 와 동일해야 함)
--   legacy_edu_applicant    … 신청자 (edu_id → legacy_edu.id)
-- 조회용 별칭 뷰: schema_edu_views.sql (public.edu, public.edu_applicants)
--
-- 목록 행은 셀 문자열 그대로 저장. 예외: unit 은 교육명 선두 (…) 안만 괄호 제거 후 추출
-- 시간: crawled_at 등은 timestamptz(UTC 저장). service_role 타임존 Asia/Seoul 로 naive 해석·표시 정렬
-- Supabase SQL Editor에서 **먼저** 실행한 뒤 schema_edu_crawl.sql 실행

create table if not exists public.legacy_edu (
  id bigserial primary key,
  seq integer not null unique,
  display_no text,
  region text,
  title text not null,
  unit text,
  edu_period text,
  apply_period text,
  capacity text,
  category text,
  registered_at text,
  crawled_at timestamptz not null default now()
);

create index if not exists idx_legacy_edu_seq on public.legacy_edu (seq);

create table if not exists public.legacy_edu_applicant (
  id bigserial primary key,
  edu_id bigint not null references public.legacy_edu (id) on delete cascade,
  user_id text not null,
  name text,
  phone text,
  branch text,
  type text,
  apply_status text,
  exam_status text,
  payment_status text,
  applicant_no integer,
  created_at timestamptz,
  updated_at timestamptz,
  crawled_at timestamptz not null default now(),
  constraint uniq_legacy_applicant unique (edu_id, user_id)
);

create index if not exists idx_legacy_edu_applicant_edu_id
  on public.legacy_edu_applicant (edu_id);

alter table public.legacy_edu enable row level security;
alter table public.legacy_edu_applicant enable row level security;

-- ── 이미 예전 스키마로 테이블만 만든 경우: 컬럼 정리
alter table public.legacy_edu add column if not exists display_no text;
alter table public.legacy_edu add column if not exists region text;
alter table public.legacy_edu add column if not exists title text;
alter table public.legacy_edu add column if not exists unit text;
alter table public.legacy_edu add column if not exists edu_period text;
alter table public.legacy_edu add column if not exists apply_period text;
alter table public.legacy_edu add column if not exists capacity text;
alter table public.legacy_edu add column if not exists category text;
alter table public.legacy_edu add column if not exists registered_at text;
alter table public.legacy_edu add column if not exists crawled_at timestamptz;

-- 예전 created_at → registered_at (컬럼이 있을 때만)
do $$
begin
  if exists (
    select 1 from information_schema.columns c
    where c.table_schema = 'public'
      and c.table_name = 'legacy_edu'
      and c.column_name = 'created_at'
  ) then
    execute $u$
      update public.legacy_edu
      set registered_at = coalesce(registered_at, created_at::text)
      where registered_at is null and created_at is not null
    $u$;
  end if;
end $$;

alter table public.legacy_edu drop column if exists edu_start_date;
alter table public.legacy_edu drop column if exists edu_end_date;
alter table public.legacy_edu drop column if exists edu_time;
alter table public.legacy_edu drop column if exists apply_start_date;
alter table public.legacy_edu drop column if exists apply_end_date;
alter table public.legacy_edu drop column if exists created_at;
alter table public.legacy_edu drop column if exists updated_at;

update public.legacy_edu set crawled_at = now() where crawled_at is null;
alter table public.legacy_edu alter column crawled_at set default now();

-- title NOT NULL 보강
update public.legacy_edu set title = '[제목없음]' where title is null or trim(title) = '';

alter table public.legacy_edu alter column title set not null;

alter table public.legacy_edu_applicant add column if not exists name text;
alter table public.legacy_edu_applicant add column if not exists phone text;
alter table public.legacy_edu_applicant add column if not exists branch text;
alter table public.legacy_edu_applicant add column if not exists type text;
alter table public.legacy_edu_applicant add column if not exists apply_status text;
alter table public.legacy_edu_applicant add column if not exists exam_status text;
alter table public.legacy_edu_applicant add column if not exists payment_status text;
alter table public.legacy_edu_applicant add column if not exists applicant_no integer;
alter table public.legacy_edu_applicant add column if not exists created_at timestamptz;
alter table public.legacy_edu_applicant add column if not exists updated_at timestamptz;
alter table public.legacy_edu_applicant add column if not exists crawled_at timestamptz;

update public.legacy_edu_applicant set crawled_at = now() where crawled_at is null;
alter table public.legacy_edu_applicant alter column crawled_at set default now();

-- 예전에 capacity 가 integer 였다면 text 로 (이미 text 면 무해)
do $$
begin
  if to_regclass('public.legacy_edu') is not null then
    alter table public.legacy_edu
      alter column capacity type text using capacity::text;
  end if;
end $$;

alter table public.legacy_edu drop column if exists current_count;

-- 크롤러·REST(service_role) 기본 타임존: naive timestamp 해석·표시를 한국(서울) 기준
alter role service_role set timezone to 'Asia/Seoul';
