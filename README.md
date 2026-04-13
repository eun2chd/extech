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

