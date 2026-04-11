-- 교육신청관리 목록(edu_apply_list) + 신청 폼 상세(edu_apply_form) 1:1
-- 개인정보·재직(회사) 정보만 상세 테이블에 저장 (첨부파일 등 제외)
-- PostgreSQL / Supabase SQL Editor 에서 실행

create table if not exists public.edu_apply (
  id bigserial primary key,

  seq integer unique,
  list_no integer,

  branch varchar(50),
  category varchar(50),

  edu_name varchar(200),
  edu_start_date date,
  edu_end_date date,

  user_name varchar(100),
  user_id varchar(100),

  phone varchar(30),

  apply_status varchar(100),
  exam_status varchar(100),
  payment_status varchar(100),

  resume_seq integer,

  created_at timestamp,
  created_at_sys timestamp not null default now()
);

create index if not exists idx_edu_apply_user_id on public.edu_apply (user_id);
create index if not exists idx_edu_apply_seq on public.edu_apply (seq);
create index if not exists idx_edu_apply_resume_seq on public.edu_apply (resume_seq);

create table if not exists public.edu_apply_user (
  id bigserial primary key,

  edu_apply_id bigint not null unique
    references public.edu_apply (id) on delete cascade,

  price integer,

  user_login_id varchar(50),

  first_name varchar(50),
  last_name varchar(50),

  passport_first_name varchar(100),
  passport_last_name varchar(100),

  birth varchar(20),
  email varchar(150),

  phone_hp varchar(30),
  phone_tel varchar(30),

  addr_postal varchar(10),
  addr1 varchar(500),
  addr2 varchar(200),

  company_name varchar(200),
  company_department varchar(100),
  company_rank varchar(100),

  company_addr_postal varchar(10),
  company_addr1 varchar(500),
  company_addr2 varchar(200),

  created_at_sys timestamp not null default now()
);

create index if not exists idx_edu_apply_user_edu_apply_id on public.edu_apply_user (edu_apply_id);

alter table public.edu_apply enable row level security;
alter table public.edu_apply_user enable row level security;

-- 기존에 짧은 길이로 만든 경우 보강 (이미 있으면 스킵)
alter table public.edu_apply alter column edu_name type varchar(200);
alter table public.edu_apply alter column user_name type varchar(100);
alter table public.edu_apply alter column user_id type varchar(100);
alter table public.edu_apply alter column phone type varchar(30);
alter table public.edu_apply alter column apply_status type varchar(100);
alter table public.edu_apply alter column exam_status type varchar(100);
alter table public.edu_apply alter column payment_status type varchar(100);

alter table public.edu_apply add column if not exists resume_seq integer;

alter table public.edu_apply_user add column if not exists phone_hp varchar(30);
alter table public.edu_apply_user add column if not exists phone_tel varchar(30);
alter table public.edu_apply_user add column if not exists addr_postal varchar(10);
alter table public.edu_apply_user add column if not exists addr1 varchar(500);
alter table public.edu_apply_user add column if not exists addr2 varchar(200);
alter table public.edu_apply_user add column if not exists company_name varchar(200);
alter table public.edu_apply_user add column if not exists company_department varchar(100);
alter table public.edu_apply_user add column if not exists company_rank varchar(100);
alter table public.edu_apply_user add column if not exists company_addr_postal varchar(10);
alter table public.edu_apply_user add column if not exists company_addr1 varchar(500);
alter table public.edu_apply_user add column if not exists company_addr2 varchar(200);

alter table public.edu_apply_user alter column passport_first_name type varchar(100);
alter table public.edu_apply_user alter column passport_last_name type varchar(100);
alter table public.edu_apply_user alter column birth type varchar(20);
alter table public.edu_apply_user alter column email type varchar(150);
