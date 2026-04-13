-- 이력서 크롤용 테이블 전부 삭제 (public). 이후 schema_resume.sql 로 재생성.
-- 자식 → 부모 순서. 예전 접두사 없는 테이블도 함께 제거.

DROP TABLE IF EXISTS
    public.crawl_resume_iecex,
    public.crawl_resume_certificates,
    public.crawl_resume_trainings,
    public.crawl_resume_projects,
    public.crawl_resume_careers,
    public.crawl_resume_educations,
    public.crawl_resume_details,
    public.crawl_resumes,
    public.resume_iecex,
    public.resume_certificates,
    public.resume_trainings,
    public.resume_projects,
    public.resume_careers,
    public.resume_educations,
    public.resume_details,
    public.resumes
CASCADE;

DROP INDEX IF EXISTS public.idx_resumes_user_id;
DROP INDEX IF EXISTS public.idx_resumes_legacy_seq;
DROP INDEX IF EXISTS public.idx_resumes_seq;
DROP INDEX IF EXISTS public.idx_crawl_resumes_user_id;
DROP INDEX IF EXISTS public.idx_crawl_resumes_seq;
