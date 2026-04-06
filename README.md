# 레거시 PHP 관리자 크롤러 → Supabase

관리자 계정으로 로그인한 뒤 `*_list_ok.php` 같은 엔드포인트에서 내려주는 **HTML 목록(주로 `<table>`)** 을 읽어, Supabase에 저장합니다.

- **기본 모드:** `crawl_rows` JSON upsert (`SAVE_TO_MEMBERS_CRAWLED=false`)
- **회원 풀패스 모드:** `SAVE_TO_MEMBERS_CRAWLED=true` — `page=1,2,…` 로 목록을 순회하다 **`번호` 열이 `1`인 행이 보이는 페이지**에서 멈추고, `members_crawled`에 RPC 배치 삽입(`seq` 유니크 중복은 무시). 다음 실행은 다시 1페이지부터 반복.

**운영 스케줄(권장):** GitHub Actions는 **`SUPABASE_ANON_KEY` + `SUPABASE_PROJECT_REF` 두 개만** Secrets에 두고, `member-crawl` **Edge Function**에 관리자·크롤 URL·`service_role` 등을 넣습니다. (생일 지급 워크플로와 같은 패턴.)

---

## 사전 준비

- **Python** 3.10 이상 권장 (GitHub Actions 워크플로는 3.12 사용)
- **Supabase** 프로젝트
- 대상 사이트의 **관리자 로그인 URL**, **목록 데이터 URL**(`list_ok` 등), 로그인 폼 **필드 이름**

---

## 1. Supabase 스키마

1. 대시보드 → **SQL Editor**
2. 사용하는 모드에 맞게 실행:
   - **레거시 JSON 적재:** `schema.sql` → `crawl_rows`
   - **회원 목록 적재:** `schema_members_crawled.sql` → `members_crawled` + `insert_members_crawled_batch` RPC (`seq` 유니크, 중복 삽입은 무시) + **`member_crawl_progress`** (Edge가 다음에 읽을 목록 `page` 저장)
   - **교육·신청자 (Edge `edu-crawl`):** `public.edu` / `public.edu_applicant` 테이블을 **먼저** 만들어 둔 뒤 `schema_edu_crawl.sql` 실행 → **`edu_list_crawl_progress`**, **`edu_applicant_crawl_progress`**, `upsert_edu_batch`, `upsert_edu_applicant_batch` (진행 테이블은 `member_crawl_progress`와 **별도**)

3. **Project Settings → API**에서 URL·키를 복사해 둡니다.  
   - **Edge `member-crawl`:** 함수 Secrets에 `SUPABASE_SERVICE_ROLE_KEY`(RPC용) 저장. `SUPABASE_URL` / `SUPABASE_ANON_KEY`는 Edge 런타임에 자동 주입되는 경우가 많습니다.  
   - **로컬 Python:** `.env`에 `service_role` 사용 시 동일하게 취급합니다.

---

## 2. 로컬 실행

### 2.1 의존성 설치

프로젝트 루트에서:

```bash
pip install -r requirements.txt
```

### 2.2 환경 변수 파일

`.env.example`을 복사해 **`.env`** 파일을 만들고 값을 채웁니다.

```bash
copy .env.example .env
```

( macOS / Linux: `cp .env.example .env` )

**`.env`는 Git에 커밋하지 마세요.** (이미 `.gitignore`에 포함)

### 2.3 실행

```bash
python -m crawler.run
```

**DB 없이 긁은 데이터만 보기:** `.env`에 `SKIP_SUPABASE=true` 를 넣으면 Supabase에 쓰지 않습니다. 터미널에 JSON이 출력되고, 동시에 **`_debug/last_crawl.json`**에 UTF-8로 저장됩니다(한글은 VS Code 등에서 열면 깨지지 않음). PowerShell에서 한글이 깨지면 터미널 UTF-8 설정 후 실행하거나 JSON 파일만 보면 됩니다. 나중에 저장할 때는 `SKIP_SUPABASE=false`로 바꾸고 `schema.sql`로 테이블을 만든 뒤 실행하면 됩니다.

정상일 때 대략 다음 순서로 동작합니다.

1. `BASE_URL` + `LOGIN_PATH`로 로그인 POST  
2. 세션 쿠키 유지  
3. `BASE_URL` + `LIST_OK_PATH`로 목록 요청(GET 또는 POST)  
4. HTML에서 `<table>` 파싱  
5. `external_id` 기준으로 Supabase `upsert`

### 2.4 HTML 덤프로 같이 분석 (`probe`)

로그인만 한 뒤 지정 URL의 HTML을 `_debug/probe_last.html`에 저장하고, 터미널에 앞부분 미리보기를 출력합니다. `LIST_OK_PATH`가 아직 없어도 동작합니다.

```bash
python -m crawler.probe
```

다른 페이지를 보려면 `.env`에 `PROBE_TARGET_URL`(전체 URL)을 넣거나, 한 번만 환경 변수로 넘깁니다.

---

## 3. 환경 변수 설명

### 필수

| 변수 | 설명 |
|------|------|
| `BASE_URL` | 사이트 루트 URL (끝 `/` 없이도 됨, 코드에서 정리) |
| `LOGIN_PATH` | 로그인 처리 경로 (예: `/admin/login/login_ok.php`) |
| `LIST_OK_PATH` | 목록 **HTML 또는 API** 경로. `?page=1` 같은 쿼리 포함 가능 (예: `/admin/member/member_list.html?...` 또는 `..._list_ok.php`) |
| `ADMIN_USER` | 관리자 아이디 |
| `ADMIN_PASSWORD` | 관리자 비밀번호 |
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase `service_role` 키 |

### 선택 (기본값 있음)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LOGIN_USER_FIELD` | `user_id` | 로그인 폼 아이디 `input`의 `name` |
| `LOGIN_PASS_FIELD` | `user_pw` | 로그인 폼 비밀번호 `input`의 `name` |
| `LOGIN_EXTRA_FIELDS_JSON` | `{}` | 로그인 POST에 같이 넣을 추가 필드(JSON 객체) |
| `LIST_HTTP_METHOD` | `GET` | 목록 요청 방식. POST만 허용되면 `POST` |
| `LIST_POST_BODY_JSON` | (없음) | POST일 때 본문(JSON 객체). 예: `{"page":1}` |
| `TABLE_SELECTOR` | `table` | BeautifulSoup `select_one`용 선택자 (목록 테이블 지정) |
| `ROW_ID_HEADER` | (자동 추정) | upsert용 고유값이 되는 **헤더가 정규화된 컬럼명**에 맞춰 지정 가능 |
| `SUPABASE_TABLE` | `crawl_rows` | 저장 테이블 이름 |
| `VERIFY_TLS` | `true` | 자체서명 HTTPS 등으로 실패 시 `false` (보안상 가급적 사이트 쪽 정상 인증 권장) |
| `SKIP_SUPABASE` | `false` | `true`면 DB 없이 JSON만 출력·`_debug/last_crawl.json` 저장 |
| `FETCH_MEMBER_MEMO` | `false` | `true`면 목록의 `_seq`로 `member_form.html`을 추가 GET, `textarea#m_memo` → 각 행 `sub.메모` |
| `MEMBER_FORM_PATH` | `/admin/member/member_form.html` | 메모가 있는 회원 수정 폼 경로 |
| `MEMBER_FORM_EXTRA_QUERY` | (ex-tech 목록과 동일 쿼리 기본값) | `mode=modify&seq=` 뒤에 붙는 쿼리스트링 (`select_key=…` 등) |
| `MEMO_REQUEST_DELAY_MS` | `0` | 메모 요청 사이 간격(ms). 서버 부담 줄일 때 사용 |
| `SAVE_TO_MEMBERS_CRAWLED` | `false` | `true`면 위 회원 풀패스 + `members_crawled` RPC 저장 (`SKIP_SUPABASE`는 `false`여야 함) |
| `MAX_LIST_PAGES` | `2000` | 안전 상한. `번호=1`을 못 만나면 이 페이지 수에서 중단 |

`ROW_ID_HEADER`를 비우면, 컬럼명에 `번호`, `신청번호`, `id` 등이 포함된 칼럼이나 첫 번째 칼럼 값으로 `external_id`를 추정합니다. **고유 번호 컬럼이 명확하면 `ROW_ID_HEADER`를 지정하는 것이 가장 안전합니다.**

---

## 4. GitHub Actions → Edge `member-crawl` (운영 권장)

워크플로: `.github/workflows/crawl.yml`

- **역할:** 3분마다 `curl`로 `member-crawl` Edge Function에 `POST`만 보냅니다. (Python 러너 없음)  
  한 번의 호출은 기본 **목록 1페이지만** 크롤·저장하고, DB `member_crawl_progress.next_page`에 이어서 읽을 페이지를 둡니다. 다음 스케줄/수동 실행이 **3페이지부터** 이어갑니다.
- **Repository Secrets (2개):**
  | Secret | 설명 |
  |--------|------|
  | `SUPABASE_SERVICE_ROLE_KEY` | `curl` 시 **`Authorization: Bearer`** 와 **`apikey:`** 에 **동일 값** (게이트웨이 401 방지). 저장소 비공개·Secrets 보호 필수. |
  | `SUPABASE_PROJECT_REF` | 프로젝트 ref만 (예: `abcdxyz`) |

  `SUPABASE_ANON_KEY`만 쓰려면 Edge 함수가 ANON Bearer를 허용하므로 가능하지만, Supabase 쪽에서 **`apikey` 헤더 없이 401** 나는 경우가 있어 `apikey: <ANON>` 도 같이 보내는 것을 권장합니다.

### 4.1 Edge Function `member-crawl` 시크릿

대시보드 **Edge Functions → member-crawl → Secrets** (또는 CLI)에 예시:

| 이름 | 설명 |
|------|------|
| `CRAWL_BASE_URL` | 예: `http://www.ex-techkorea.com/admin` |
| `CRAWL_LOGIN_PATH` | 예: `/admin/login/login_proc.php` |
| (회원 목록 URL) | **`member-crawl` 코드에 상수로 고정** (`MEMBER_LIST_PATH`). 다른 사이트면 코드 수정 |
| `CRAWL_ADMIN_USER` / `CRAWL_ADMIN_PASSWORD` | 관리자 계정 |
| `CRAWL_LOGIN_USER_FIELD` | 기본 `m_id` |
| `CRAWL_LOGIN_PASS_FIELD` | 기본 `m_pass` |
| `CRAWL_TABLE_SELECTOR` | 기본 `table.list_table` |
| `CRAWL_FETCH_MEMO` | `true` / `false` |
| `CRAWL_MEMO_DELAY_MS` | 기본 `0` |
| `CRAWL_MAX_LIST_PAGES` | 기본 `2000` |
| `CRAWL_PAGES_PER_RUN` | 기본 `1` — 호출 한 번에 연속으로 처리할 목록 페이지 수 (예: `2`면 5·6페이지를 한 번에) |
| `CRAWL_MEMBER_FORM_PATH` | 기본 `/admin/member/member_form.html` |
| `CRAWL_MEMBER_FORM_EXTRA_QUERY` | 목록과 맞춘 쿼리 스트링 |
| `SUPABASE_SERVICE_ROLE_KEY` | RPC `insert_members_crawled_batch` 호출용 (또는 `ETK_SERVICE_ROLE_KEY`) |

호출 인증: **`SERVICE_ROLE`** 이면 `Authorization`·`apikey` 둘 다 같은 키. **`ANON`** 이면 Bearer만 ANON과 일치하면 통과(apikey 있으면 ANON과 동일해야 함).

**401 `Unauthorized` 일 때:** 응답 JSON의 `edge_has_service_role` 을 보면 됨. `false` 이면 **Supabase(프로젝트) Edge Secrets** 에 `SUPABASE_SERVICE_ROLE_KEY` 가 없는 것 — **GitHub Secret 에 넣은 값과 완전히 같은 문자열**을 Edge에도 `supabase secrets set SUPABASE_SERVICE_ROLE_KEY='…'` 로 넣고 함수 재배포. 대시보드에 **JWT(`eyJ…`)** 와 **`sb_…`** 이 둘 다 있으면 **한 종류만 골라** GitHub·Edge·RPC 모두 동일하게 맞출 것.

배포 예: `supabase functions deploy member-crawl --no-verify-jwt`

**주의:** Edge 함수는 **실행 시간 제한**이 있습니다. 페이지 단위로 나눈 뒤에도 메모 요청이 많으면 `CRAWL_PAGES_PER_RUN=1` 유지·`CRAWL_FETCH_MEMO=false`·`CRAWL_MEMO_DELAY_MS` 조정을 검토하세요. 전체 일괄은 로컬 `python -m crawler.run`(`.env`)와 병행할 수 있습니다.

**요청 JSON (선택):** `{"page_count":2}` — 시크릿 기본보다 우선. `{"reset":true}` — 다음 시작을 1페이지로 맞춤. `{"start_page":10}` — 체크포인트 무시하고 10페이지부터 (성공 시에도 `next_page`는 갱신됨).

### 4.2 Edge `edu-crawl` (교육 목록 + 신청자)

워크플로: `.github/workflows/crawl-edu.yml` — 3분마다 **같은 job 안에서** `edu_list` 1회 POST 후 `applicants` 1회 POST (각각 `page_count: 1` 기본).

| 단계 | `mode` | 저장 | 진행 테이블 |
|------|--------|------|-------------|
| 교육 목록 | `edu_list` | `upsert_edu_batch` (`seq` 기준 upsert) | `edu_list_crawl_progress` (`id=edu_list`, `next_page`) |
| 신청자 | `applicants` | `upsert_edu_applicant_batch` (`edu_id`,`user_id` upsert) | `edu_applicant_crawl_progress` (`target_edu_seq`, `next_page`) |

**Edge 시크릿 (로그인은 `member-crawl`과 동일 `CRAWL_*` 재사용 가능):**

| 이름 | 설명 |
|------|------|
| `EDU_LIST_PATH` | (선택) 미설정 시 ex-tech 기본: `/admin/edu/edu_list.html?select_key=&input_key=&search=&page=1` — `page`만 크롤러가 치환 |
| `EDU_APPLY_LIST_TEMPLATE` | (선택) 기본: `/admin/edu/edu_apply_list.html?el_seq={el_seq}` — **`el_seq` = `edu.seq`**(목록 체크박스 value와 동일). `{seq}`는 `{el_seq}` 별칭. 템플릿에 **`{page}`가 없으면** 한 번만 요청 후 다음 교육으로 진행(단일 페이지 목록) |
| `EDU_TABLE_SELECTOR` | 교육 표 선택자 (기본 `table.list_table`) |
| `EDU_APPLICANT_TABLE_SELECTOR` | 신청자 표 선택자 (기본 `table.list_table`) |
| `EDU_PAGES_PER_RUN` | 기본 `1` |
| `EDU_MAX_LIST_PAGES` | 기본 `2000` |

**ex-tech 예시 URL:** 교육 목록 `http://www.ex-techkorea.com/admin/edu/edu_list.html?select_key=&input_key=&search=&page=134` — 신청자 `http://www.ex-techkorea.com/admin/edu/edu_apply_list.html?el_seq=2195` (`el_seq`는 해당 행의 교육 `seq`).

**요청 JSON:** `{"mode":"edu_list"|"applicants","page_count":1,"reset":true}` — `edu_list`만 `start_page` 지원.

마지막 페이지 판별은 회원 크롤과 동일하게 표에 **`번호` 열 값 `1`** 이 있으면 해당 목록의 마지막 페이지로 간주하고, 교육 목록은 `next_page=1`로 돌아갑니다. 신청자는 `{page}`가 있는 다중 페이지면 동일 규칙으로 다음 페이지, **없으면** 한 번 저장 후 해당 `edu_seq` 다음 교육(없으면 처음 `seq`)으로 넘깁니다.

HTML 컬럼명이 다르면 `supabase/functions/edu-crawl/index.ts`의 `rowToEduPayload` / `rowToApplicantPayload` 에서 키 후보를 맞추면 됩니다.

배포 예: `supabase functions deploy edu-crawl --no-verify-jwt`

---

## 5. 사이트 쪽에서 미리 확인할 것 (체크리스트)

설정을 맞출 때 브라우저 개발자 도구(Network)로 다음을 확인하면 시간이 절약됩니다.

1. 로그인 폼의 **`action` URL** → `LOGIN_PATH` (또는 `BASE_URL`과 합쳐진 전체 경로)
2. 로그인 POST **Form Data**의 필드 이름 → `LOGIN_USER_FIELD`, `LOGIN_PASS_FIELD`, `LOGIN_EXTRA_FIELDS_JSON`
3. 목록이 로드될 때 호출되는 요청 URL → `LIST_OK_PATH`
4. 그 요청이 **GET**인지 **POST**인지, POST면 본문 → `LIST_HTTP_METHOD`, `LIST_POST_BODY_JSON`
5. 응답 HTML 안에서 실제 데이터가 들어 있는 **`<table>`** 을 고르는 선택자 → `TABLE_SELECTOR`
6. 행을 구분할 **고유 번호 컬럼** → `ROW_ID_HEADER` (가능하면 지정)

응답이 테이블이 아니라 순수 JSON이면, 현재 코드는 HTML 테이블 파싱 전제이므로 파서 추가가 필요합니다.

---

## 6. 문제 해결

| 증상 | 점검 |
|------|------|
| `Missing required environment variable` | 필수 env/Secret 누락 |
| 로그인은 되는데 목록이 비정상 | `LIST_OK_PATH`, `LIST_HTTP_METHOD`, `LIST_POST_BODY_JSON` |
| `Could not find a table` | `TABLE_SELECTOR`를 더 구체적으로 (예: `table.board_list`) |
| DB에 안 쌓임 / upsert 안 됨 | `ROW_ID_HEADER`와 실제 헤더 불일치, `external_id` 추정 실패 → 로그의 skipped 경고 확인 |
| SSL 오류 | 일시적으로만 `VERIFY_TLS=false` 검토 (근본은 인증서/도메인) |
| `PHPSESSID` 경고만 뜸 | 쿠키 이름이 다를 수 있음. 목록 요청이 동작하면 무시 가능한 경우가 많음 |

---

## 7. 보안

- `ADMIN_PASSWORD`, `SUPABASE_SERVICE_ROLE_KEY`는 **공개 저장소·채팅·스크린샷에 노출하지 마세요.**
- GitHub에는 **Secrets**만 사용하고, `.env`는 커밋하지 마세요.

---

## 프로젝트 구조 (요약)

```
crawler/
  config.py          # 환경 변수
  session.py         # 로그인·목록 HTTP
  list_pager.py      # 목록 URL page 파라미터
  parse_table.py     # HTML 테이블 → dict
  members_map.py     # 회원 행 → DB 페이로드
  member_memo.py     # member_form 메모
  store.py           # crawl_rows upsert
  store_members.py   # members_crawled RPC
  run.py             # 진입점
schema.sql
schema_members_crawled.sql
schema_edu_crawl.sql
requirements.txt
.github/workflows/crawl.yml
.github/workflows/crawl-edu.yml
supabase/functions/member-crawl/
supabase/functions/edu-crawl/
```
