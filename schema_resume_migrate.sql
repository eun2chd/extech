-- 기존 public.crawl_* 이력서 테이블에 컬럼이 빠져 있거나 타입이 다를 때 보정용.
-- (신규 설치는 schema_resume_drop.sql → schema_resume.sql 만으로 충분.)
--
-- 오류: column "name" of relation "crawl_resumes" does not exist
-- 오류: invalid input syntax for type uuid: "jjhoon"
--   → user_id 가 UUID(auth.users 등)로 잡혀 있으면, 크롤러의 rl_userid(문자)를 넣을 수 없음.
--   아래 DO 블록이 uuid 이면 VARCHAR(255) 로 바꿈. FK가 user_id 에 걸려 있으면 먼저 제거해야 할 수 있음.

DO $mig$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'crawl_resumes'
          AND column_name = 'user_id'
          AND data_type = 'uuid'
    ) THEN
        ALTER TABLE public.crawl_resumes
            ALTER COLUMN user_id TYPE VARCHAR(255) USING (user_id::text);
    END IF;
END
$mig$;

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS name VARCHAR(512);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS first_name VARCHAR(255);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS last_name VARCHAR(255);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS en_first_name VARCHAR(255);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS en_last_name VARCHAR(255);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS birth DATE;

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS country_code VARCHAR(32);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS country_name VARCHAR(255);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS list_row_json JSONB;

-- 외부 seq → VARCHAR(50) UNIQUE (legacy_resume_seq INTEGER 가 있으면 이관)
ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS seq VARCHAR(50);

ALTER TABLE public.crawl_resumes
    ADD COLUMN IF NOT EXISTS list_no VARCHAR(32);

DO $seqcopy$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'crawl_resumes'
          AND column_name = 'legacy_resume_seq'
    ) THEN
        UPDATE public.crawl_resumes
        SET seq = legacy_resume_seq::text
        WHERE seq IS NULL
          AND legacy_resume_seq IS NOT NULL;
    END IF;
END
$seqcopy$;

DROP INDEX IF EXISTS public.idx_crawl_resumes_legacy_seq;

ALTER TABLE public.crawl_resumes
    DROP CONSTRAINT IF EXISTS crawl_resumes_legacy_resume_seq_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_crawl_resumes_seq
    ON public.crawl_resumes (seq);

-- 정리(선택): INTEGER 컬럼 제거
-- ALTER TABLE public.crawl_resumes DROP COLUMN IF EXISTS legacy_resume_seq;
