# 레거시 PHP 관리자 크롤러 → Supabase

관리자 계정으로 로그인한 뒤 `*_list_ok.php` 같은 엔드포인트에서 내려주는 **HTML 목록(주로 `<table>`)** 을 읽어, 행 단위로 JSON에 넣어 Supabase에 **upsert** 저장합니다.  
주기 실행은 GitHub Actions의 `cron`(기본 3분 간격) 또는 수동 실행(`workflow_dispatch`)을 사용합니다.

---

## 사전 준비

- **Python** 3.10 이상 권장 (GitHub Actions 워크플로는 3.12 사용)
- **Supabase** 프로젝트
- 대상 사이트의 **관리자 로그인 URL**, **목록 데이터 URL**(`list_ok` 등), 로그인 폼 **필드 이름**

---

## 1. Supabase 테이블 만들기

1. Supabase 대시보드 → **SQL Editor**
2. 저장소 루트의 `schema.sql` 내용을 붙여 넣고 실행

생성되는 테이블(기본 이름):

| 컬럼 | 설명 |
|------|------|
| `external_id` | upsert 기준 키 (행 고유값) |
| `row_data` | 파싱된 한 행 전체(JSON) |
| `scraped_at` | 수집 시각 |

3. **Project Settings → API**에서 `Project URL`, **`service_role` API 키**를 복사해 둡니다.  
   이 크롤러는 서버/GitHub Actions에서 돌아가므로 **`service_role`** 을 사용하는 것을 전제로 합니다. 키는 외부에 노출하지 마세요.

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

정상일 때 대략 다음 순서로 동작합니다.

1. `BASE_URL` + `LOGIN_PATH`로 로그인 POST  
2. 세션 쿠키 유지  
3. `BASE_URL` + `LIST_OK_PATH`로 목록 요청(GET 또는 POST)  
4. HTML에서 `<table>` 파싱  
5. `external_id` 기준으로 Supabase `upsert`

---

## 3. 환경 변수 설명

### 필수

| 변수 | 설명 |
|------|------|
| `BASE_URL` | 사이트 루트 URL (끝 `/` 없이도 됨, 코드에서 정리) |
| `LOGIN_PATH` | 로그인 처리 경로 (예: `/admin/login/login_ok.php`) |
| `LIST_OK_PATH` | 목록 데이터 경로 (예: `/admin/apply/edu_apply_list_ok.php`) |
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

`ROW_ID_HEADER`를 비우면, 컬럼명에 `번호`, `신청번호`, `id` 등이 포함된 칼럼이나 첫 번째 칼럼 값으로 `external_id`를 추정합니다. **고유 번호 컬럼이 명확하면 `ROW_ID_HEADER`를 지정하는 것이 가장 안전합니다.**

---

## 4. GitHub Actions로 주기 실행

워크플로 파일: `.github/workflows/crawl.yml`

- **스케줄:** `*/3 * * * *` (약 3분마다)  
  GitHub 쪽 부하에 따라 실행이 밀릴 수 있습니다. 엄밀한 3분이 필요하면 외부 스케줄러를 검토하세요.
- **수동 실행:** Actions 탭 → `crawl-admin-list` → **Run workflow**

### 4.1 Repository secrets 등록

저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래 이름으로 등록합니다. (워크플로의 `env:`와 이름이 일치해야 합니다.)

| Secret 이름 | 필수 여부 |
|-------------|-----------|
| `BASE_URL` | 필수 |
| `LOGIN_PATH` | 필수 |
| `LIST_OK_PATH` | 필수 |
| `ADMIN_USER` | 필수 |
| `ADMIN_PASSWORD` | 필수 |
| `SUPABASE_URL` | 필수 |
| `SUPABASE_SERVICE_ROLE_KEY` | 필수 |
| `LOGIN_USER_FIELD` | 선택 (비우면 코드 기본값) |
| `LOGIN_PASS_FIELD` | 선택 |
| `LOGIN_EXTRA_FIELDS_JSON` | 선택 |
| `LIST_HTTP_METHOD` | 선택 |
| `LIST_POST_BODY_JSON` | 선택 |
| `TABLE_SELECTOR` | 선택 |
| `ROW_ID_HEADER` | 선택 |
| `VERIFY_TLS` | 선택 |
| `SUPABASE_TABLE` | 선택 |

선택 항목을 만들지 않았다면 GitHub에서는 빈 값으로 넘어가며, 로컬과 동일하게 기본값이 적용됩니다.

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
  config.py       # 환경 변수
  session.py      # 로그인·목록 HTTP
  parse_table.py  # HTML 테이블 → dict 목록
  store.py        # Supabase upsert
  run.py          # 진입점
schema.sql
requirements.txt
.github/workflows/crawl.yml
```
# extech
