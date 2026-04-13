-- 이력서 크롤러용 PostgreSQL 스키마 (테이블명 crawl_ 접두사)
-- 초기화: schema_resume_drop.sql 실행 후 본 파일 실행.

-- 1) 기본 이력서
CREATE TABLE public.crawl_resumes (
    id              BIGSERIAL PRIMARY KEY,
    seq             VARCHAR(50) UNIQUE,
    list_no         VARCHAR(32),
    list_row_json   JSONB,
    user_id         VARCHAR(255),
    name            VARCHAR(512),
    first_name      VARCHAR(255),
    last_name       VARCHAR(255),
    en_first_name   VARCHAR(255),
    en_last_name    VARCHAR(255),
    birth           DATE,
    country_code    VARCHAR(32),
    country_name    VARCHAR(255),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_crawl_resumes_user_id ON public.crawl_resumes (user_id);

COMMENT ON COLUMN public.crawl_resumes.seq IS '관리자 목록/상세 URL의 외부 seq (부모 생성·매핑 키)';
COMMENT ON COLUMN public.crawl_resumes.list_no IS '목록 두 번째 td 등에서 읽은 번호(표시용)';
COMMENT ON COLUMN public.crawl_resumes.list_row_json IS '목록 tr 셀 스냅샷(선택)';
COMMENT ON TABLE public.crawl_resumes IS 'LIST: seq로 부모 행 생성 → DETAIL: id로 자식 + 본문 컬럼 갱신';

-- 2) 언어 등 1:1 상세
CREATE TABLE public.crawl_resume_details (
    id              BIGSERIAL PRIMARY KEY,
    resume_id       BIGINT NOT NULL UNIQUE REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    lang1           VARCHAR(64),
    lang1_level     VARCHAR(64),
    lang2           VARCHAR(64),
    lang2_level     VARCHAR(64),
    lang3           VARCHAR(64),
    lang3_level     VARCHAR(64)
);

CREATE INDEX idx_crawl_resume_details_resume_id ON public.crawl_resume_details (resume_id);

-- 3) 학력
CREATE TABLE public.crawl_resume_educations (
    id                      BIGSERIAL PRIMARY KEY,
    resume_id               BIGINT NOT NULL REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    school_name             VARCHAR(512),
    major                   VARCHAR(512),
    degree                  VARCHAR(255),
    final_education         VARCHAR(255),
    graduation              DATE
);

CREATE INDEX idx_crawl_resume_educations_resume_id ON public.crawl_resume_educations (resume_id);

-- 4) 경력
CREATE TABLE public.crawl_resume_careers (
    id                  BIGSERIAL PRIMARY KEY,
    resume_id           BIGINT NOT NULL REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    company_name        VARCHAR(512),
    start_date          DATE,
    end_date            DATE,
    department_name     VARCHAR(512),
    rank_title          VARCHAR(255),
    duty                TEXT,
    job_code            VARCHAR(64)
);

CREATE INDEX idx_crawl_resume_careers_resume_id ON public.crawl_resume_careers (resume_id);

-- 5) 전문경력
CREATE TABLE public.crawl_resume_projects (
    id                  BIGSERIAL PRIMARY KEY,
    resume_id           BIGINT NOT NULL REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    company_name        VARCHAR(512),
    start_date          DATE,
    end_date            DATE,
    duty                TEXT,
    memo                TEXT
);

CREATE INDEX idx_crawl_resume_projects_resume_id ON public.crawl_resume_projects (resume_id);

-- 6) 교육(훈련이수)
CREATE TABLE public.crawl_resume_trainings (
    id              BIGSERIAL PRIMARY KEY,
    resume_id       BIGINT NOT NULL REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    name            VARCHAR(512),
    center          VARCHAR(512),
    start_date      DATE,
    end_date        DATE,
    memo            TEXT
);

CREATE INDEX idx_crawl_resume_trainings_resume_id ON public.crawl_resume_trainings (resume_id);

-- 7) 자격증
CREATE TABLE public.crawl_resume_certificates (
    id              BIGSERIAL PRIMARY KEY,
    resume_id       BIGINT NOT NULL REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    name            VARCHAR(512),
    publisher       VARCHAR(512),
    issue_date      DATE
);

CREATE INDEX idx_crawl_resume_certificates_resume_id ON public.crawl_resume_certificates (resume_id);

-- 8) IECEx 자격증
CREATE TABLE public.crawl_resume_iecex (
    id              BIGSERIAL PRIMARY KEY,
    resume_id       BIGINT NOT NULL REFERENCES public.crawl_resumes (id) ON DELETE CASCADE,
    iece_code       VARCHAR(128),
    iece_pcode      VARCHAR(128),
    iece_date       DATE
);

CREATE INDEX idx_crawl_resume_iecex_resume_id ON public.crawl_resume_iecex (resume_id);
