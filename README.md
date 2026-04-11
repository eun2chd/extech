📌 레거시 PHP 관리자 크롤러 → Supabase 정리
1. 개요

관리자 로그인 후 *_list_ok.php 또는 HTML 목록 페이지를 크롤링하여 Supabase에 저장한다.

기본 흐름
관리자 로그인
목록 페이지 요청 (page=1,2,...)
HTML <table> 파싱
데이터 가공
Supabase upsert

2. 동작 모드
2.1 기본 모드
테이블: crawl_rows
방식: JSON upsert
설정:
SAVE_TO_MEMBERS_CRAWLED=false
2.2 회원 풀패스 모드
테이블: members_crawled
방식: RPC batch upsert (seq 기준)
특징:
page=1부터 순회
번호 = 1 나오면 종료
다음 실행 시 다시 1부터 시작
SAVE_TO_MEMBERS_CRAWLED=true
2.3 교육 크롤 모드
대상 테이블
legacy_edu (교육)
legacy_edu_applicant (신청자)
관계
교육 (1) : 신청자 (N)
처리 흐름
교육 목록 수집
각 교육(seq)별 신청자 수집
3. Supabase 스키마
3.1 회원
members_crawled
insert_members_crawled_batch
3.2 교육
legacy_edu
3.2b 교육신청관리(관리자 전체 신청)
edu_apply, edu_apply_user — schema_edu_apply.sql, 실행: python -m crawler.edu_apply_management_crawl
3.3 신청자
legacy_edu_applicant
3.4 진행 상태 테이블
테이블	역할
member_crawl_progress	회원 다음 page
edu_list_crawl_progress	교육 목록 page
edu_applicant_crawl_progress	신청자 진행

## 파이썬 실행 명령 모음

프로젝트 루트에서 `.env` 를 채운 뒤 아래 명령을 실행한다.

### 명령 한눈에

| 명령 | 하는 일 |
|------|---------|
| `python -m crawler.run` | **기본 크롤**: `LIST_OK_PATH` 를 **한 번** 받아 표를 파싱한 뒤 `crawl_rows` 에 upsert. `SKIP_SUPABASE=true` 이면 DB 없이 JSON 출력·`_debug/last_crawl.json` 저장. **`SAVE_TO_MEMBERS_CRAWLED=true`** 이면 회원 목록을 `page=1`부터 끝(번호=1)까지 순회하며 `members_crawled` RPC 배치 저장·메모 보강·무한 라운드 옵션 지원. |
| `python -m crawler.edu_crawl_local` | **교육(강좌) + 신청자**: 교육 목록 페이지를 순회해 `legacy_edu` (`upsert_edu_batch` RPC), 이어서 **교육 seq별** 신청자 목록(`/admin/edu/edu_apply_list.html?el_seq=…`)을 긁어 `legacy_edu_applicant` (`upsert_edu_applicant_batch` RPC) 저장. |
| `python -m crawler.edu_apply_management_crawl` | **교육신청관리(전체)**: `/admin/edu/edu_apply_list.html` 목록과 각 행의 `edu_apply_form.html?mode=modify&seq=…` 상세(개인·회사 필드만)를 읽어 **`edu_apply` / `edu_apply_user`** (1:1) upsert. 스키마는 `schema_edu_apply.sql`. |
| `python -m crawler.probe` | **HTML 디버그**: 로그인 후 `PROBE_TARGET_URL`(또는 기본 회원 목록) 한 번 GET 해서 `probe_last.html` 등으로 저장. Supabase 미사용. |
| `python -m crawler.edu_list_debug` | **교육 목록 파싱 디버그**: `EDU_LIST_PATH` 한 페이지를 파싱한 헤더·행·seq 추출 결과를 **표준출력 JSON** 으로 출력. DB 미저장. |

### 옵션·환경 요약

| 모듈 | 자주 쓰는 플래그 / 변수 |
|------|-------------------------|
| `crawler.run` | `SAVE_TO_MEMBERS_CRAWLED`, `SKIP_SUPABASE`, `LIST_OK_PATH`, `TABLE_SELECTOR`, `MAX_LIST_PAGES`, `CRAWL_LOOP_FOREVER` |
| `crawler.edu_crawl_local` | `--skip-applicants` (교육만), `--delay`, `--loop`, `--applicants-progress-mode`, `EDU_LIST_PATH`, `EDU_APPLY_LIST_TEMPLATE` 등 |
| `crawler.edu_apply_management_crawl` | `--max-pages`, `--start-page`, `--detail-delay`, `--skip-detail`, `EDU_APPLY_MANAGE_LIST_PATH`, `EDU_APPLY_DETAIL_PATH_TEMPLATE` |
| `crawler.probe` | `PROBE_TARGET_URL` |
| `crawler.edu_list_debug` | `--page`, `--limit` |

4. 실행 방법
4.1 기본 실행
python -m crawler.run
4.2 교육 크롤
python -m crawler.edu_crawl_local
옵션:
--skip-applicants   # 신청자 제외
--delay 5           # 요청 간 딜레이

4.3 교육신청관리(전체 목록+상세)
python -m crawler.edu_apply_management_crawl
4.4 디버그
HTML 확인
python -m crawler.probe
교육 목록 확인
python -m crawler.edu_list_debug --page 1
5. 환경 변수
필수
변수	설명
BASE_URL	사이트 URL
LOGIN_PATH	로그인 경로
LIST_OK_PATH	목록 URL
ADMIN_USER	관리자 ID
ADMIN_PASSWORD	비밀번호
SUPABASE_URL	Supabase URL
SUPABASE_SERVICE_ROLE_KEY	service_role 키
주요 옵션
변수	기본값	설명
SAVE_TO_MEMBERS_CRAWLED	false	회원 모드
SKIP_SUPABASE	false	DB 저장 여부
TABLE_SELECTOR	table	파싱 대상
LIST_HTTP_METHOD	GET	요청 방식
MAX_LIST_PAGES	2000	최대 페이지
LOOP_SLEEP_SECONDS	10	반복 대기
6. 크롤링 전략
6.1 페이지 종료 조건
번호 == 1 → 마지막 페이지
6.2 신청자 크롤
URL
/admin/edu/edu_apply_list.html?el_seq={seq}
특징
{seq} = 교육 PK
페이지 없으면 1회 요청
6.3 데이터 저장
교육
upsert_edu_batch
신청자
upsert_edu_applicant_batch
7. 운영 (GitHub Actions + Edge)
구조
GitHub Actions → Edge Function → Supabase
Secrets
이름	설명
SUPABASE_SERVICE_ROLE_KEY	인증
SUPABASE_PROJECT_REF	프로젝트 ID
실행 방식
3분마다 호출
1페이지씩 진행
진행 상태 DB 저장
8. 성능 / 안정성
권장 설정
MEMBER_LIST_PAGE_DELAY_SECONDS=30
EDU_APPLICANT_PAGE_DELAY_SECONDS=30
과부하 방지
요청 간 딜레이 필수
동시에 여러 프로세스 실행 금지
9. 주요 문제 & 해결
문제	원인
데이터 0건	테이블 selector 오류
신청자 저장 안됨	edu 먼저 없음
로그인 실패	필드명 불일치
401	service_role 키 불일치
10. 핵심 설계 포인트
✔ 크롤링
페이지 단위 처리
상태 저장 기반 이어서 실행
✔ DB
upsert 기반 중복 방지
seq 기준 식별
✔ 안정성
딜레이 필수
진행 테이블 필수
🚀 한 줄 요약

회원·목록 범용은 `crawler.run`, 강좌+강좌별 신청자는 `edu_crawl_local`, 관리자 **교육신청관리** 화면 동기화는 `edu_apply_management_crawl`, HTML·파싱 확인은 `probe` / `edu_list_debug`.

<<<<<<< HEAD
### 2.6 실행 명령 정리 — 무엇을 돌리면 되나요?

| 하고 싶은 일 | 실행 명령 | `.env`에서 특히 켜야 할 것 |
|--------------|-----------|----------------------------|
| **회원** 목록을 DB `members_crawled`에 쌓기 (1→끝페이지 한 바퀴) | `python -m crawler.run` | `SAVE_TO_MEMBERS_CRAWLED=true`, `SKIP_SUPABASE=false`, `LIST_OK_PATH` = 회원 목록 URL, `SUPABASE_*`, (선택) `MEMBER_LIST_PAGE_DELAY_SECONDS=30` |
| **교육 목록** + **교육 신청자** 둘 다 DB에 (한 프로세스 안에서 순서대로) | `python -m crawler.edu_crawl_local` | 위와 동일. **신청자 기본:** `legacy_edu`의 **display_no(숫자) 오름차순(1→끝)** 으로 각 **seq**마다 신청 목록 전부 저장. Edge式 진행 테이블만 쓰려면 `--applicants-progress-mode` |
| 교육 **목록만** (신청자 생략) | `python -m crawler.edu_crawl_local --skip-applicants` | 위와 동일 |
| 목록 HTML만 확인·디버그 (DB 없음) | `python -m crawler.edu_list_debug --page 1` 또는 `SKIP_SUPABASE=true` 로 `python -m crawler.run` | 디버그용 |
| 로그인 후 임의 URL HTML 덤프 | `python -m crawler.probe` | `PROBE_TARGET_URL` 등 |
| (구버전) 행을 `crawl_rows` JSON upsert | `python -m crawler.run` | `SAVE_TO_MEMBERS_CRAWLED=false` (기본), `SUPABASE_TABLE`, `ROW_ID_HEADER` 등 |

교육은 **별도 터미널 두 개로 “목록”과 “신청”을 나눌 필요 없습니다.** `edu_crawl_local` 한 번이 라운드마다 **교육 목록 → 신청자** 순으로 처리합니다.

### 2.7 서버에 상시 띄울 때 — 부하 완화·대기

같은 관리자 사이트에 **짧은 간격으로 요청이 몰리면** 세션 끊김·차단·PHP 부하가 날 수 있으니, **대기 시간을 넉넉히** 두는 것을 권장합니다.

| 설정 | 용도 |
|------|------|
| `python -m crawler.edu_crawl_local --delay 5` | 교육 **목록 페이지마다** (초). 서버 부담 줄이기 좋음. |
| `LOOP_SLEEP_SECONDS=30` (또는 `--between-rounds 30`) | `CRAWL_LOOP_FOREVER` / `EDU_CRAWL_LOOP` 로 **라운드 사이** 휴지. |
| `MEMO_REQUEST_DELAY_MS=200` | 회원 크롤에서 **회원별 메모** 추가 요청 사이 간격. |
| `MEMBER_LIST_PAGE_DELAY_SECONDS=30` | 회원 **목록 페이지마다** (저장 후 다음 `page=` 요청 전) 대기(초). `0`이면 연속 요청. |
| `EDU_PAGES_PER_RUN=1` | 신청자 단계에서 **한 번에 처리할 신청 목록 페이지 수** (기본 1이면 가장 부담 적음). |
| `EDU_APPLICANT_PAGE_DELAY_SECONDS=30` | 신청자 **목록 페이지마다** 다음 페이지 요청 전 대기(초). `0`이면 연속. |
| `EDU_APPLICANT_EDU_DELAY_SECONDS` | 신청자 **교육(seq) 한 건 끝난 뒤** 다음 교육 요청 전 대기(초). |
| `--applicants-progress-mode` / `EDU_APPLICANTS_PROGRESS_MODE` | 신청자만 `edu_applicant_crawl_progress` 사용(Edge 동일). **기본은 DB `display_no` 순 전체 순회.** |
| `EDU_APPLICANTS_ROUNDS` / `--applicants-rounds N` | **진행 모드일 때만** `run_applicants_phase` 호출 횟수. |

**신청자(`legacy_edu_applicant`)가 0건만 쌓일 때:** 로그에 `[신청자] 표는 N행인데 user_id 추출 0건`이 나오면 표 헤더가 예상과 다른 경우가 많습니다(코드에서 `ID__이력서보기` 등 변형·열 이름 휴리스틱 처리). `[신청자] legacy_edu 에 seq=… 없음`이면 `upsert_edu_applicant_batch`의 `INNER JOIN` 때문에 삽입되지 않습니다 — 먼저 교육 목록이 같은 `seq`로 `legacy_edu`에 들어갔는지 확인하세요.

**무한 반복 예시 (서버에서 계속 돌리기):**

- 회원만: `.env`에 `CRAWL_LOOP_FOREVER=true`, `LOOP_SLEEP_SECONDS=60` 등 → `python -m crawler.run`
- 교육+신청만: `EDU_CRAWL_LOOP=true`, `LOOP_SLEEP_SECONDS=60` → `python -m crawler.edu_crawl_local --delay 5`

간격은 사이트 속도·허용치에 맞게 조절하면 됩니다 (5~60초 등).

### 2.8 VS Code를 두 개 켜서, 교육용 / 회원용을 동시에 돌려도 되나요?

**가능합니다.** 다만 둘 다 **같은 관리자 계정·같은 사이트**에 로그인해 요청하므로:

- **동시에 두 프로세스**가 돌면 PHP/세션/쿠키가 꼬이거나 부하가 **2배**로 갈 수 있습니다.
- **권장:** (1) 한쪽은 `LOOP_SLEEP_SECONDS`·`--delay`를 **크게** 잡거나, (2) **시간을 어긋나게** 시작해 요청이 겹치지 않게 하거나, (3) 부담이 크면 **한 번에 하나만** 실행.

**역할 분리:**  
- 창 A: ` --delay 5` → 교육 목록 + 교육 신청자까지 **한 명령**으로 처리.  
- 창 B: `python -m crawler.run` (`SAVE_TO_MEMBERS_CRAWLED=true`) → **회원(사용자 정보)** 만.

Edge(GitHub Actions)와 **로컬을 동시에** 같은 DB 진행 테이블로 돌리면 `next_page`·`target_edu_seq`가 섞일 수 있으니, 운영은 **로컬만** 또는 **Edge만** 중 하나로 통일하는 편이 안전합니다.

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
| `SKIP_SUPABASE` | `false` | `true`면 DB 없이 JSON만 출력·`_debug/last_crawl.json` 저장. `SAVE_TO_MEMBERS_CRAWLED=false`일 때는 **목록 1요청(미리보기)** 만 함 |
| `FETCH_MEMBER_MEMO` | `false` | `true`면 목록의 `python -m crawler.edu_crawl_local_seq`로 `member_form.html`을 추가 GET, `textarea#m_memo` → 각 행 `sub.메모` |
| `MEMBER_FORM_PATH` | `/admin/member/member_form.html` | 메모가 있는 회원 수정 폼 경로 |
| `MEMBER_FORM_EXTRA_QUERY` | (ex-tech 목록과 동일 쿼리 기본값) | `mode=modify&seq=` 뒤에 붙는 쿼리스트링 (`select_key=…` 등) |
| `MEMO_REQUEST_DELAY_MS` | `0` | 메모 요청 사이 간격(ms). 서버 부담 줄일 때 사용 |
| `SAVE_TO_MEMBERS_CRAWLED` | `false` | `true`면 위 회원 풀패스 + `members_crawled` RPC 저장 (`SKIP_SUPABASE`는 `false`여야 함) |
| `MAX_LIST_PAGES` | `2000` | 안전 상한. `번호=1`을 못 만나면 이 페이지 수에서 중단 |
| `MEMBER_LIST_PAGE_DELAY_SECONDS` | `0` | 회원 풀패스에서 **페이지 저장 후** 다음 목록 페이지까지 대기(초). 예: `30` |
| `CRAWL_LOOP_FOREVER` | `false` | `true`면 회원 풀패스가 끝날 때마다 다시 1페이지부터 반복 (서버 상시용) |
| `LOOP_SLEEP_SECONDS` | `10` | 위 반복·`EDU_CRAWL_LOOP` 라운드 **사이** 대기(초) |
| `EDU_CRAWL_LOOP` | `false` | `true`면 `edu_crawl_local`이 교육 라운드마다 반복 |
| `EDU_LIST_PATH` | (ex-tech 기본) | `edu_crawl_local` / Edge 교육 목록 URL (`page=` 포함) |
| `EDU_APPLY_LIST_TEMPLATE` | `/admin/edu/edu_apply_list.html?el_seq={el_seq}` | 신청자 목록 URL |
| `EDU_APPLICANT_TABLE_SELECTOR` | `table.list_table` | 신청자 표 선택자 |
| `EDU_PAGES_PER_RUN` | `1` | 신청자 단계에서 한 호출(라운드)당 처리할 목록 페이지 수 |
| `EDU_APPLICANT_PAGE_DELAY_SECONDS` | `0` | 신청자 목록 **페이지 사이** 대기(초) |
| `EDU_APPLICANT_EDU_DELAY_SECONDS` | `0` | 신청자: **교육(seq) 사이** 대기(초) |
| `EDU_APPLICANTS_PROGRESS_MODE` | `false` | `true`면 신청자를 진행 테이블 모드로(Edge). 기본은 `legacy_edu` display_no 순 |
| `EDU_APPLICANTS_ROUNDS` | `1` | 진행 모드에서 `run_applicants_phase` 호출 횟수 |

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
| 교육 목록 | `edu_list` | `upsert_edu_batch` → **`legacy_edu`** (`seq` 기준 upsert) | `edu_list_crawl_progress` (`id=edu_list`, `next_page`) |
| 신청자 | `applicants` | `upsert_edu_applicant_batch` → **`legacy_edu_applicant`** (`edu_id`,`user_id` upsert) | `edu_applicant_crawl_progress` (`target_edu_seq`, `next_page`) |

**Edge 시크릿 (로그인은 `member-crawl`과 동일 `CRAWL_*` 재사용 가능):**

| 이름 | 설명 |
|------|------|
| `EDU_LIST_PATH` | (선택) 미설정 시 ex-tech 기본: `...edu_list.html?select_key=&input_key=&search=&cate=&el_state=-1&el_area=&el_code=&el_startdate=&el_enddate=&page=1` — `page`만 치환 |
| `EDU_APPLY_LIST_TEMPLATE` | (선택) 기본: `/admin/edu/edu_apply_list.html?el_seq={el_seq}` — **`el_seq` = `edu.seq`**(목록 체크박스 value와 동일). `{seq}`는 `{el_seq}` 별칭. 템플릿에 **`{page}`가 없으면** 한 번만 요청 후 다음 교육으로 진행(단일 페이지 목록) |
| `EDU_TABLE_SELECTOR` | 교육 표 선택자 (기본 `table.list_table`) |
| `EDU_APPLICANT_TABLE_SELECTOR` | 신청자 표 선택자 (기본 `table.list_table`) |
| `EDU_PAGES_PER_RUN` | 기본 `1` |
| `EDU_MAX_LIST_PAGES` | 기본 `2000` |

**ex-tech 예시 URL:** 교육 목록 `.../edu_list.html?select_key=&input_key=&search=&cate=&el_state=-1&el_area=&el_code=&el_startdate=&el_enddate=&page=134` — 신청자 `.../edu_apply_list.html?el_seq=2195` (`el_seq` = **`legacy_edu.seq`**).

**요청 JSON:** `{"mode":"edu_list"|"applicants","page_count":1,"reset":true}` — `edu_list`만 `start_page` 지원.

마지막 페이지 판별은 회원 크롤과 동일하게 표에 **`번호` 열 값 `1`** 이 있으면 해당 목록의 마지막 페이지로 간주하고, 교육 목록은 `next_page=1`로 돌아갑니다. 신청자는 `{page}`가 있는 다중 페이지면 동일 규칙으로 다음 페이지, **없으면** 한 번 저장 후 해당 `edu_seq` 다음 교육(없으면 처음 `seq`)으로 넘깁니다.

HTML 컬럼명이 다르면 `supabase/functions/edu-crawl/index.ts`의 `rowToEduPayload` / `rowToApplicantPayload` 에서 키 후보를 맞추면 됩니다.

배포 예: `supabase functions deploy edu-crawl --no-verify-jwt`

**로컬 (Edge 없이):** `python -m crawler.edu_crawl_local` — 교육 목록(`upsert_edu_batch`) 후 신청자는 **기본**으로 DB `legacy_edu`의 **display_no 순**으로 각 `seq` 신청 목록 전부 `upsert_edu_applicant_batch`. 진행 테이블만 쓰려면 `--applicants-progress-mode`. `--skip-applicants` 로 목록만.

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
  store_edu.py       # legacy_edu / legacy_edu_applicant RPC
  edu_applicants.py  # 신청자 단계 (Edge applicants 와 동일 로직)
  edu_crawl_local.py # 교육 목록+신청자 로컬 크롤
  run.py             # 진입점
schema.sql
schema_members_crawled.sql
schema_edu.sql
schema_edu_crawl.sql
schema_edu_views.sql
requirements.txt
.github/workflows/crawl.yml
.github/workflows/crawl-edu.yml
supabase/functions/member-crawl/
supabase/functions/edu-crawl/
```
=======
>>>>>>> 626ab94841cf57b61701fef2fbeff65b7fbca73c
