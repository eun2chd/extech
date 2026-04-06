-- 교육 1 : N 신청자 — 도메인 이름으로 조회하기 위한 뷰
-- 물리 테이블·RPC·크롤러는 그대로 public.legacy_edu, public.legacy_edu_applicant 를 사용합니다.
-- 전제: schema_edu.sql 실행 완료
--
-- 관계:
--   legacy_edu.seq  = 관리자 신청자 목록 URL 의 el_seq
--     예: /admin/edu/edu_apply_list.html?el_seq=<seq>
--   legacy_edu_applicant.edu_id → legacy_edu.id (FK, on delete cascade)
--   한 교육(id/seq)당 신청자 여러 행 (uniq: edu_id + user_id)
--
-- 크롤러는 edu_list 행 HTML 안의 el_seq= 를 우선해 legacy_edu.seq 에 넣도록 맞춤
-- (체크박스 value 만 쓰면 el_seq 와 달라 신청자 URL 이 어긋날 수 있음)

create or replace view public.edu as
select
  id,
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
from public.legacy_edu;

comment on view public.edu is
  '교육 마스터 1쪽. seq = edu_apply_list.html?el_seq= 값 (크롤러 extract 우선순위: el_seq 링크)';

create or replace view public.edu_applicants as
select
  a.id,
  a.edu_id,
  e.seq as edu_seq,
  a.user_id,
  a.name,
  a.phone,
  a.branch,
  a.type,
  a.apply_status,
  a.exam_status,
  a.payment_status,
  a.applicant_no,
  a.created_at,
  a.updated_at,
  a.crawled_at
from public.legacy_edu_applicant a
inner join public.legacy_edu e on e.id = a.edu_id;

comment on view public.edu_applicants is
  '교육 신청자 N쪽. edu_seq 로 특정 교육의 신청자만 필터 (예: where edu_seq = 2194)';

grant select on public.edu to authenticated, service_role;
grant select on public.edu_applicants to authenticated, service_role;
