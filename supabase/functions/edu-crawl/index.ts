// Setup type definitions for built-in Supabase Runtime APIs
// @deno-types="https://esm.sh/@supabase/functions-js/src/edge-runtime.d.ts"
/**
 * 교육 목록(edu_list) → legacy_edu upsert(셀 그대로 + 교육명에서 unit 만 괄호 추출) / 신청자 → legacy_edu_applicant
 * 호출마다 N페이지만 처리, 진행은 edu_list_crawl_progress / edu_applicant_crawl_progress 에 저장
 *
 * Secrets: member-crawl 과 동일하게 로그인용 CRAWL_* + SUPABASE_* + 아래 EDU_* 권장
 *
 *   EDU_LIST_PATH — 미설정 시 ex-tech 기본: /admin/edu/edu_list.html?select_key=&input_key=&search=&page=1
 *   EDU_APPLY_LIST_TEMPLATE — {el_seq} = edu.seq(목록 체크박스와 동일). {seq} 는 {el_seq} 별칭.
 *     {page} 가 있으면 치환; 없으면 요청 시 `page` 쿼리를 자동 병합(ex-tech: …&el_seq=32&page=7).
 *     기본: /admin/edu/edu_apply_list.html?el_seq={el_seq}
 *   EDU_TABLE_SELECTOR — 기본 table.list_table
 *   EDU_APPLICANT_TABLE_SELECTOR — 기본 table.list_table
 *   EDU_PAGES_PER_RUN — 기본 1 (또는 요청 JSON page_count)
 *   EDU_MAX_LIST_PAGES — 목록 상한 (기본 2000)
 *
 * 요청 JSON:
 *   mode: "edu_list" | "applicants" (필수 권장)
 *   page_count, reset, start_page (edu_list 전용 start_page)
 *   applicants 에서 reset 이면 target_edu_seq=null, next_page=1
 *
 * 배포: supabase functions deploy edu-crawl --no-verify-jwt
 */
import { load } from "npm:cheerio@1.0.0-rc.12";

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const EDU_LIST_PROGRESS_ID = "edu_list";
const APPLICANT_PROGRESS_ID = "default";

const MSG_EDU_LIST_WRAP =
  "[edu-crawl] 교육 목록: 끝(빈 페이지·번호=1·상한) 도달 — checkpoint 1페이지로 복귀. " +
  "다음 호출부터 처음부터 다시 순회합니다.";

/** ex-tech — 시크릿 EDU_LIST_PATH 없을 때 (필터 파라미터 브라우저와 동일) */
const DEFAULT_EDU_LIST_PATH =
  "/admin/edu/edu_list.html?select_key=&input_key=&search=&cate=&el_state=-1&el_area=&el_code=&el_startdate=&el_enddate=&page=1";

/** el_seq = 교육 seq. {page} 없어도 신청자 모드에서 `page` 는 항상 쿼리에 병합 */
const DEFAULT_EDU_APPLY_TEMPLATE =
  "/admin/edu/edu_apply_list.html?el_seq={el_seq}";

type CrawlMode = "edu_list" | "applicants";

function resolveUrl(base: string, path: string): string {
  const p = path.trim();
  if (p.startsWith("http://") || p.startsWith("https://")) return p;
  const b = base.endsWith("/") ? base : `${base}/`;
  return new URL(p, b).href;
}

function listPathForPage(listOkPath: string, page: number): string {
  const n = Math.max(1, page);
  const [path, q] = listOkPath.includes("?")
    ? listOkPath.split("?", 2)
    : [listOkPath, ""];
  const params = new URLSearchParams(q);
  params.set("page", String(n));
  return `${path}?${params.toString()}`;
}

function applyListUrl(
  base: string,
  template: string,
  eduSeq: number,
  page: number,
): string {
  const n = Math.max(1, page);
  let filled = template
    .replaceAll("{el_seq}", String(eduSeq))
    .replaceAll("{seq}", String(eduSeq));
  if (filled.includes("{page}")) {
    filled = filled.replaceAll("{page}", String(n));
    return resolveUrl(base, filled);
  }
  const href = resolveUrl(base, filled);
  const u = new URL(href);
  u.searchParams.set("page", String(n));
  return u.href;
}

function normalizeKey(s: string): string {
  let key = s.trim().replace(/\s+/g, "_");
  key = key.replace(/[^\w가-힣]+/gu, "");
  return key || "col";
}

function cellText($: ReturnType<typeof load>, el: unknown): string {
  return $(el as never).text().replace(/\s+/g, " ").trim();
}

/**
 * ex-tech edu_list 행: edu_apply_list.html?el_seq= 와 동일한 값이어야 하므로
 * 행 HTML 의 el_seq= 를 체크박스 value 보다 우선. 없으면 체크박스 → 링크 seq= 다수일 때 최빈값.
 */
function extractEduListRowSeq($: ReturnType<typeof load>, tr: unknown): string | null {
  const $tr = $(tr as never);
  const html = $tr.html() ?? "";
  const el = html.match(/(?:[?&])el_seq=(\d+)/i);
  if (el) return el[1]!;

  const cb = $tr
    .find(
      'input[type="checkbox"][name="seq_list[]"], input[type="checkbox"][name^="seq_list"]',
    )
    .first()
    .attr("value")
    ?.trim();
  if (cb && /^\d+$/.test(cb)) return cb;

  const seqMatches = [...html.matchAll(/[?&]seq=(\d+)/gi)].map((m) => m[1]!);
  if (seqMatches.length) {
    const tally = new Map<string, number>();
    for (const s of seqMatches) tally.set(s, (tally.get(s) ?? 0) + 1);
    let best = seqMatches[0]!;
    let n = 0;
    for (const [s, c] of tally) {
      if (c > n) {
        n = c;
        best = s;
      }
    }
    return best;
  }
  return null;
}

/**
 * 헤더 행에 체크박스 열이 없고, 데이터 행만 <td><checkbox></td> 로 시작하면 1칸 밀림.
 * 이 경우 첫 셀은 헤더 매핑에서 제외한다.
 */
function eduListDataCellOffset(
  $: ReturnType<typeof load>,
  tr: unknown,
  headerLen: number,
): number {
  const $tr = $(tr as never);
  const cells = $tr.find("td, th");
  const n = cells.length;
  if (n <= headerLen) return 0;
  const $first = cells.first();
  const hasCb = $first.find('input[type="checkbox"]').length > 0;
  if (hasCb && n === headerLen + 1) return 1;
  return 0;
}

function parseTable(html: string, selector: string): Record<string, string>[] {
  const $ = load(html);
  let table = $(selector).first();
  if (!table.length) table = $("table").first();
  if (!table.length) throw new Error("Could not find a <table>");

  const rows = table.find("tr");
  if (!rows.length) return [];

  const firstRow = rows[0];
  const headerCells = $(firstRow).find("th, td");
  const seen = new Map<string, number>();
  const headers: string[] = [];
  headerCells.each((_, c) => {
    const h = normalizeKey(cellText($, c));
    const n = seen.get(h) ?? 0;
    seen.set(h, n + 1);
    headers.push(n === 0 ? h : `${h}_${n + 1}`);
  });
  console.log("[edu-crawl] parseTable headers (index:key):");
  headers.forEach((h, idx) => console.log(`[edu-crawl]   [${idx}] ${h}`));

  const dataRows = rows.toArray().slice(1);
  const firstDataEl = dataRows[0];
  const cellOffset = firstDataEl != null
    ? eduListDataCellOffset($, firstDataEl, headers.length)
    : 0;
  console.log("[edu-crawl] parseTable data cell offset (checkbox column pad):", cellOffset);

  const out: Record<string, string>[] = [];
  for (const tr of dataRows) {
    const $tr = $(tr);
    const cells = $tr.find("td, th");
    if (!cells.length) continue;
    const row: Record<string, string> = {};
    const seqVal = extractEduListRowSeq($, tr);
    if (seqVal) row._seq = seqVal;
    cells.each((i, c) => {
      if (i < cellOffset) return;
      const hi = i - cellOffset;
      const key = headers[hi] ?? `col_${i}`;
      row[key] = cellText($, c);
    });
    if (Object.values(row).some((x) => x)) out.push(row);
  }
  return out;
}

function pageHasNumOne(rows: Record<string, string>[]): boolean {
  for (const r of rows) {
    const v = r["번호"];
    if (v !== undefined && String(v).trim() === "1") return true;
  }
  return false;
}

function pick(row: Record<string, string>, keys: string[]): string {
  for (const k of keys) {
    const v = row[k];
    if (v != null && String(v).trim() !== "") return String(v).trim();
  }
  return "";
}

function stripNbsp(s: string): string {
  return s
    .replace(/\u00a0/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** 교육명 선두 `(IECEx 010)` → `IECEx 010`(괄호 제외). title 은 셀 원문과 별도 */
function extractLeadingParenUnit(raw: string): string | null {
  const s = stripNbsp(raw);
  if (!s) return null;
  const m = s.match(/^\s*\(([^)]*)\)\s*/);
  if (!m) return null;
  const inner = stripNbsp(m[1] ?? "");
  return inner || null;
}

/** 목록 테이블 행 → legacy_edu upsert JSON (셀 문자열 그대로, seq·unit 만 특수) */
function rowToEduPayload(
  row: Record<string, string>,
): Record<string, unknown> | null {
  const seqRaw = row._seq?.trim();
  if (!seqRaw || !/^\d+$/.test(seqRaw)) return null;
  const seq = parseInt(seqRaw, 10);
  if (Number.isNaN(seq)) return null;

  const t = (keys: string[]): string | null => {
    const v = stripNbsp(pick(row, keys));
    return v || null;
  };

  const titleRaw = pick(row, ["교육명", "제목", "강좌명", "title"]);
  const title = stripNbsp(titleRaw);
  const unit = extractLeadingParenUnit(titleRaw);

  return {
    seq,
    display_no: t(["번호", "No", "no"]),
    region: t(["지역", "region"]),
    title: title || "[제목없음]",
    unit,
    edu_period: t(["교육기간일시", "교육기간", "교육_기간"]),
    apply_period: t(["접수기간", "접수_기간"]),
    capacity: t(["정원", "모집인원", "capacity"]),
    category: t(["분류", "카테고리", "category"]),
    registered_at: t(["등록일자", "등록일", "created_at"]),
  };
}

function rowToApplicantPayload(
  row: Record<string, string>,
): Record<string, unknown> | null {
  // <th>ID(이력서보기)</th> → normalizeKey → ID이력서보기
  const user_id = stripNbsp(
    pick(row, [
      "ID이력서보기",
      "ID_이력서보기",
      "ID__이력서보기",
      "ID이력서",
      "아이디",
      "회원아이디",
      "user_id",
      "USER_ID",
    ]),
  );
  if (!user_id) return null;

  const noRaw = pick(row, ["번호", "No", "no"]);
  let applicant_no: number | null = null;
  if (noRaw) {
    const n = parseInt(noRaw, 10);
    applicant_no = Number.isNaN(n) ? null : n;
  }

  return {
    user_id,
    name: pick(row, ["성명", "이름", "name"]) || null,
    phone: pick(row, ["연락처", "휴대폰", "phone"]) || null,
    branch: pick(row, ["신청지사", "지점", "branch"]) || null,
    type: pick(row, ["구분", "유형", "type"]) || null,
    apply_status: pick(row, ["접수상태", "신청상태", "apply_status"]) || null,
    exam_status: pick(row, ["시험상태", "exam_status"]) || null,
    payment_status: pick(row, ["결제", "결제상태", "입금상태", "payment_status"]) ||
      null,
    applicant_no,
    created_at: pick(row, ["등록일자", "신청일", "등록일", "created_at"]) || null,
    updated_at: pick(row, ["수정일", "updated_at"]) || null,
  };
}

/** 한 RPC 배치에 동일 user_id 가 두 번 있으면 Postgres 21000 */
function dedupeApplicantPayloads(
  payloads: Record<string, unknown>[],
): Record<string, unknown>[] {
  const m = new Map<string, Record<string, unknown>>();
  for (const p of payloads) {
    const uid = stripNbsp(String(p.user_id ?? ""));
    if (!uid) continue;
    m.set(uid, p);
  }
  return [...m.values()];
}

async function applySetCookie(res: Response, jar: Map<string, string>) {
  const h = res.headers as unknown as { getSetCookie?: () => string[] };
  const list = typeof h.getSetCookie === "function"
    ? h.getSetCookie()
    : [];
  for (const line of list) {
    const part = line.split(";")[0]?.trim();
    if (!part?.includes("=")) continue;
    const i = part.indexOf("=");
    jar.set(part.slice(0, i).trim(), part.slice(i + 1).trim());
  }
}

function cookieHeader(jar: Map<string, string>): string {
  return [...jar.entries()].map(([k, v]) => `${k}=${v}`).join("; ");
}

function serviceRestHeaders(serviceKey: string): Record<string, string> {
  return {
    Authorization: `Bearer ${serviceKey}`,
    apikey: serviceKey,
  };
}

function parseRpcInt(text: string): number {
  const t = text.trim();
  try {
    const j = JSON.parse(t) as unknown;
    if (typeof j === "number" && !Number.isNaN(j)) return j;
  } catch {
    /* scalar 가 따옴표 없이 올 수 있음 */
  }
  const n = parseInt(t, 10);
  return Number.isNaN(n) ? 0 : n;
}

async function restJson(
  url: string,
  serviceKey: string,
  init?: RequestInit,
): Promise<unknown> {
  const r = await fetch(url, {
    ...init,
    headers: {
      ...serviceRestHeaders(serviceKey),
      ...(init?.headers as Record<string, string>),
    },
  });
  const text = await r.text();
  if (!r.ok) {
    throw new Error(`HTTP ${r.status}: ${text.slice(0, 500)}`);
  }
  if (!text.trim()) return null;
  return JSON.parse(text);
}

async function ensureEduListProgress(
  supabaseUrl: string,
  serviceKey: string,
): Promise<void> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/edu_list_crawl_progress?select=id&id=eq.${EDU_LIST_PROGRESS_ID}`;
  try {
    const arr = await restJson(url, serviceKey) as unknown[];
    if (Array.isArray(arr) && arr.length > 0) return;
  } catch {
    throw new Error("edu_list_crawl_progress 조회 실패");
  }
  const ins = await fetch(`${base}/rest/v1/edu_list_crawl_progress`, {
    method: "POST",
    headers: {
      ...serviceRestHeaders(serviceKey),
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify({ id: EDU_LIST_PROGRESS_ID, next_page: 1 }),
  });
  if (!ins.ok && ins.status !== 409) {
    const t = await ins.text();
    throw new Error(`edu_list_crawl_progress seed failed: ${ins.status} ${t}`);
  }
}

async function ensureApplicantProgress(
  supabaseUrl: string,
  serviceKey: string,
): Promise<void> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/edu_applicant_crawl_progress?select=id&id=eq.${APPLICANT_PROGRESS_ID}`;
  try {
    const arr = await restJson(url, serviceKey) as unknown[];
    if (Array.isArray(arr) && arr.length > 0) return;
  } catch {
    throw new Error("edu_applicant_crawl_progress 조회 실패");
  }
  const ins = await fetch(`${base}/rest/v1/edu_applicant_crawl_progress`, {
    method: "POST",
    headers: {
      ...serviceRestHeaders(serviceKey),
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify({
      id: APPLICANT_PROGRESS_ID,
      target_edu_seq: null,
      next_page: 1,
    }),
  });
  if (!ins.ok && ins.status !== 409) {
    const t = await ins.text();
    throw new Error(`edu_applicant_crawl_progress seed failed: ${ins.status} ${t}`);
  }
}

async function getEduListNextPage(
  supabaseUrl: string,
  serviceKey: string,
): Promise<number> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/edu_list_crawl_progress?select=next_page&id=eq.${EDU_LIST_PROGRESS_ID}`;
  const arr = await restJson(url, serviceKey) as { next_page?: unknown }[];
  if (!Array.isArray(arr) || arr.length === 0) return 1;
  const raw = arr[0]!.next_page;
  const p = typeof raw === "number" ? raw : parseInt(String(raw), 10);
  return Number.isNaN(p) || p < 1 ? 1 : p;
}

async function patchEduListNextPage(
  supabaseUrl: string,
  serviceKey: string,
  nextPage: number,
): Promise<void> {
  const base = supabaseUrl.replace(/\/$/, "");
  const r = await fetch(
    `${base}/rest/v1/edu_list_crawl_progress?id=eq.${EDU_LIST_PROGRESS_ID}`,
    {
      method: "PATCH",
      headers: {
        ...serviceRestHeaders(serviceKey),
        "Content-Type": "application/json",
        Prefer: "return=minimal",
      },
      body: JSON.stringify({ next_page: Math.max(1, nextPage) }),
    },
  );
  if (!r.ok) {
    throw new Error(`patch edu_list progress: ${r.status} ${await r.text()}`);
  }
}

type ApplicantProgress = {
  target_edu_seq: number | null;
  next_page: number;
};

async function getApplicantProgress(
  supabaseUrl: string,
  serviceKey: string,
): Promise<ApplicantProgress> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/edu_applicant_crawl_progress?select=target_edu_seq,next_page&id=eq.${APPLICANT_PROGRESS_ID}`;
  const arr = await restJson(url, serviceKey) as {
    target_edu_seq?: number | null;
    next_page?: unknown;
  }[];
  if (!Array.isArray(arr) || arr.length === 0) {
    return { target_edu_seq: null, next_page: 1 };
  }
  const row = arr[0]!;
  const np = row.next_page;
  const page = typeof np === "number" ? np : parseInt(String(np), 10);
  return {
    target_edu_seq: row.target_edu_seq ?? null,
    next_page: Number.isNaN(page) || page < 1 ? 1 : page,
  };
}

async function patchApplicantProgress(
  supabaseUrl: string,
  serviceKey: string,
  p: ApplicantProgress,
): Promise<void> {
  const base = supabaseUrl.replace(/\/$/, "");
  const r = await fetch(
    `${base}/rest/v1/edu_applicant_crawl_progress?id=eq.${APPLICANT_PROGRESS_ID}`,
    {
      method: "PATCH",
      headers: {
        ...serviceRestHeaders(serviceKey),
        "Content-Type": "application/json",
        Prefer: "return=minimal",
      },
      body: JSON.stringify({
        target_edu_seq: p.target_edu_seq,
        next_page: Math.max(1, p.next_page),
      }),
    },
  );
  if (!r.ok) {
    throw new Error(`patch applicant progress: ${r.status} ${await r.text()}`);
  }
}

async function fetchMinEduSeq(
  supabaseUrl: string,
  serviceKey: string,
): Promise<number | null> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url = `${base}/rest/v1/legacy_edu?select=seq&order=seq.asc&limit=1`;
  const arr = await restJson(url, serviceKey) as { seq?: number }[];
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const s = arr[0]!.seq;
  return typeof s === "number" ? s : null;
}

async function fetchNextEduSeqAfter(
  supabaseUrl: string,
  serviceKey: string,
  afterSeq: number,
): Promise<number | null> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/legacy_edu?select=seq&seq=gt.${afterSeq}&order=seq.asc&limit=1`;
  const arr = await restJson(url, serviceKey) as { seq?: number }[];
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const s = arr[0]!.seq;
  return typeof s === "number" ? s : null;
}

async function loginAdmin(
  baseUrl: string,
  loginPath: string,
  adminUser: string,
  adminPass: string,
  userField: string,
  passField: string,
  ua: string,
): Promise<Map<string, string>> {
  const jar = new Map<string, string>();
  const loginUrl = resolveUrl(baseUrl, loginPath);
  const body = new URLSearchParams();
  body.set(userField, adminUser);
  body.set(passField, adminPass);
  let loginRes = await fetch(loginUrl, {
    method: "POST",
    headers: {
      "User-Agent": ua,
      "Content-Type": "application/x-www-form-urlencoded",
      Accept:
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    },
    body: body.toString(),
    redirect: "manual",
  });
  await applySetCookie(loginRes, jar);
  for (let hop = 0; hop < 8; hop++) {
    if (loginRes.status < 300 || loginRes.status >= 400) break;
    const loc = loginRes.headers.get("location");
    if (!loc) break;
    loginRes = await fetch(resolveUrl(baseUrl, loc), {
      headers: {
        Cookie: cookieHeader(jar),
        "User-Agent": ua,
        Accept:
          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      },
      redirect: "manual",
    });
    await applySetCookie(loginRes, jar);
  }
  return jar;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    if (req.method !== "POST") {
      return new Response(
        JSON.stringify({ success: false, error: "Method not allowed" }),
        {
          status: 405,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    let bodyJson: Record<string, unknown> = {};
    try {
      const raw = await req.text();
      if (raw.trim()) {
        const j = JSON.parse(raw) as unknown;
        if (j && typeof j === "object" && !Array.isArray(j)) {
          bodyJson = j as Record<string, unknown>;
        }
      }
    } catch {
      bodyJson = {};
    }

    const modeRaw = (bodyJson.mode as string | undefined)?.trim() ?? "";
    const mode: CrawlMode = modeRaw === "applicants"
      ? "applicants"
      : "edu_list";

    const anon = (Deno.env.get("ETK_ANON_KEY") ?? "").trim();
    const serviceRole = (Deno.env.get("ETK_SERVICE_ROLE_KEY") ?? "").trim();
    const auth = req.headers.get("Authorization") ?? "";
    const token = (auth.startsWith("Bearer ") ? auth.slice(7) : "").trim();
    const apikeyHeader = (req.headers.get("apikey") ?? "").trim();

    const okAnon = !!anon && token === anon &&
      (!apikeyHeader || apikeyHeader === anon);
    const okService = !!serviceRole && token === serviceRole &&
      apikeyHeader === serviceRole;

    if (!okAnon && !okService) {
      return new Response(
        JSON.stringify({ success: false, error: "Unauthorized" }),
        {
          status: 401,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const baseUrl = (Deno.env.get("CRAWL_BASE_URL") ?? "").trim().replace(
      /\/$/,
      "",
    );
    const loginPath = Deno.env.get("CRAWL_LOGIN_PATH") ?? "";
    const adminUser = Deno.env.get("CRAWL_ADMIN_USER") ?? "";
    const adminPass = Deno.env.get("CRAWL_ADMIN_PASSWORD") ?? "";
    const userField = Deno.env.get("CRAWL_LOGIN_USER_FIELD") ?? "m_id";
    const passField = Deno.env.get("CRAWL_LOGIN_PASS_FIELD") ?? "m_pass";

    const eduListPath = (Deno.env.get("EDU_LIST_PATH") ?? "").trim() ||
      DEFAULT_EDU_LIST_PATH;
    const applyTemplate =
      (Deno.env.get("EDU_APPLY_LIST_TEMPLATE") ?? "").trim() ||
      DEFAULT_EDU_APPLY_TEMPLATE;
    const tableSel = Deno.env.get("EDU_TABLE_SELECTOR") ?? "table.list_table";
    const applicantTableSel =
      Deno.env.get("EDU_APPLICANT_TABLE_SELECTOR") ?? "table.list_table";

    const maxPages = Math.max(
      1,
      parseInt(Deno.env.get("EDU_MAX_LIST_PAGES") ?? "2000", 10) || 2000,
    );
    const pagesPerRunDefault = Math.max(
      1,
      parseInt(Deno.env.get("EDU_PAGES_PER_RUN") ?? "1", 10) || 1,
    );
    const pagesPerRun = Math.min(
      50,
      Math.max(
        1,
        typeof bodyJson.page_count === "number" &&
            Number.isFinite(bodyJson.page_count)
          ? Math.floor(bodyJson.page_count as number)
          : pagesPerRunDefault,
      ),
    );

    const supabaseUrl = (Deno.env.get("SUPABASE_URL") ?? "").trim();
    const serviceKey =
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ||
      Deno.env.get("ETK_SERVICE_ROLE_KEY") ||
      "";

    if (!baseUrl || !loginPath || !adminUser || !adminPass) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "CRAWL_BASE_URL, CRAWL_LOGIN_PATH, CRAWL_ADMIN_USER, CRAWL_ADMIN_PASSWORD 필요",
        }),
        {
          status: 500,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }
    if (!supabaseUrl || !serviceKey) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY 없음",
        }),
        {
          status: 500,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    try {
      if (mode === "edu_list") await ensureEduListProgress(supabaseUrl, serviceKey);
      else await ensureApplicantProgress(supabaseUrl, serviceKey);
    } catch (e) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "진행 테이블 준비 실패 — schema_edu_crawl.sql 실행 여부 확인",
          detail: String(e),
        }),
        {
          status: 502,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const ua =
      "Mozilla/5.0 (compatible; EduCrawl-Edge/1.0; +supabase-edge)";
    const jar = await loginAdmin(
      baseUrl,
      loginPath,
      adminUser,
      adminPass,
      userField,
      passField,
      ua,
    );

    const rpcBase = `${supabaseUrl.replace(/\/$/, "")}/rest/v1/rpc`;

    // ── edu_list 모드
    if (mode === "edu_list") {
      if (bodyJson.reset === true) {
        await patchEduListNextPage(supabaseUrl, serviceKey, 1);
      }

      let startPage =
        typeof bodyJson.start_page === "number" &&
          Number.isFinite(bodyJson.start_page as number)
          ? Math.max(1, Math.floor(bodyJson.start_page as number))
          : await getEduListNextPage(supabaseUrl, serviceKey);
      startPage = Math.max(1, Math.min(startPage, maxPages));

      let totalUpserted = 0;
      const processedPages: number[] = [];
      let crawlDone = false;
      let lastPage: number | null = null;

      for (let i = 0; i < pagesPerRun; i++) {
        const page = startPage + i;
        if (page > maxPages) break;

        const listUrl = resolveUrl(baseUrl, listPathForPage(eduListPath, page));
        const listRes = await fetch(listUrl, {
          headers: {
            Cookie: cookieHeader(jar),
            "User-Agent": ua,
            Accept:
              "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          },
        });
        await applySetCookie(listRes, jar);
        const html = await listRes.text();
        if (!listRes.ok) {
          return new Response(
            JSON.stringify({
              success: false,
              error: `교육 목록 HTTP ${listRes.status}`,
              page,
            }),
            {
              status: 502,
              headers: { ...corsHeaders, "Content-Type": "application/json" },
            },
          );
        }

        let rows: Record<string, string>[];
        try {
          rows = parseTable(html, tableSel);
        } catch (e) {
          return new Response(
            JSON.stringify({ success: false, error: String(e), page }),
            {
              status: 500,
              headers: { ...corsHeaders, "Content-Type": "application/json" },
            },
          );
        }

        if (!rows.length) {
          console.log(MSG_EDU_LIST_WRAP);
          await patchEduListNextPage(supabaseUrl, serviceKey, 1);
          crawlDone = true;
          lastPage = page;
          break;
        }

        const payloads: Record<string, unknown>[] = [];
        for (const row of rows) {
          const p = rowToEduPayload(row);
          if (p) payloads.push(p);
        }

        if (payloads.length) {
          const rpcRes = await fetch(`${rpcBase}/upsert_edu_batch`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${serviceKey}`,
              apikey: serviceKey,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ p_rows: payloads }),
          });
          const rpcText = await rpcRes.text();
          if (!rpcRes.ok) {
            return new Response(
              JSON.stringify({
                success: false,
                error: "upsert_edu_batch 실패",
                status: rpcRes.status,
                body: rpcText.slice(0, 1200),
                page,
              }),
              {
                status: 502,
                headers: { ...corsHeaders, "Content-Type": "application/json" },
              },
            );
          }
          const touched = parseRpcInt(rpcText);
          totalUpserted += touched;
          console.log(
            `[edu-crawl] 교육 목록 페이지 ${page}: RPC 영향 행 수=${touched} ` +
              "(upsert: 동일 seq 는 갱신 · 회원처럼 insert-only 건너뜀 아님)",
          );
        }

        const last = pageHasNumOne(rows);
        const hitCap = page >= maxPages;
        const nextP = last || hitCap ? 1 : page + 1;
        await patchEduListNextPage(supabaseUrl, serviceKey, nextP);
        processedPages.push(page);
        lastPage = page;
        if (last || hitCap) {
          console.log(MSG_EDU_LIST_WRAP);
          crawlDone = true;
          break;
        }
      }

      return new Response(
        JSON.stringify({
          success: true,
          mode: "edu_list",
          pages_processed: processedPages.length,
          pages: processedPages,
          end_page: lastPage,
          crawl_done: crawlDone,
          rows_upsert_touched: totalUpserted,
          cycled_to_first_page: crawlDone,
          message_ko: crawlDone
            ? "교육 목록 끝 도달 시 next_page=1 로 돌아가 다음 호출은 1페이지부터 다시 순회합니다."
            : "진행 중: 다음 호출은 edu_list_crawl_progress.next_page 이어서 처리합니다.",
        }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } },
      );
    }

    // ── applicants 모드
    if (bodyJson.reset === true) {
      await patchApplicantProgress(supabaseUrl, serviceKey, {
        target_edu_seq: null,
        next_page: 1,
      });
    }

    let { target_edu_seq: eduSeq, next_page: apStartPage } = await getApplicantProgress(
      supabaseUrl,
      serviceKey,
    );

    if (eduSeq == null) {
      eduSeq = await fetchMinEduSeq(supabaseUrl, serviceKey);
      if (eduSeq == null) {
        return new Response(
          JSON.stringify({
            success: true,
            mode: "applicants",
            message: "legacy_edu 테이블에 행이 없어 신청자 크롤을 건너뜀",
            pages_processed: 0,
          }),
          { headers: { ...corsHeaders, "Content-Type": "application/json" } },
        );
      }
      await patchApplicantProgress(supabaseUrl, serviceKey, {
        target_edu_seq: eduSeq,
        next_page: 1,
      });
      apStartPage = 1;
    }

    apStartPage = Math.max(1, Math.min(apStartPage, maxPages));

    /** applyListUrl 이 항상 `page` 쿼리를 붙이므로 신청 목록은 항상 다페이지 가능 */
    const applyListPaginated = true;

    let totalUpserted = 0;
    const processedPages: number[] = [];
    let crawlDone = false;
    let lastPage: number | null = null;

    for (let i = 0; i < pagesPerRun; i++) {
      const page = apStartPage + i;
      if (page > maxPages) break;

      const listUrl = applyListUrl(baseUrl, applyTemplate, eduSeq!, page);
      const listRes = await fetch(listUrl, {
        headers: {
          Cookie: cookieHeader(jar),
          "User-Agent": ua,
          Accept:
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
      });
      await applySetCookie(listRes, jar);
      const html = await listRes.text();
      if (!listRes.ok) {
        return new Response(
          JSON.stringify({
            success: false,
            error: `신청 목록 HTTP ${listRes.status}`,
            edu_seq: eduSeq,
            page,
          }),
          {
            status: 502,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }

      let rows: Record<string, string>[];
      try {
        rows = parseTable(html, applicantTableSel);
      } catch (e) {
        return new Response(
          JSON.stringify({
            success: false,
            error: String(e),
            edu_seq: eduSeq,
            page,
          }),
          {
            status: 500,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }

      if (!rows.length) {
        const nextSeq = await fetchNextEduSeqAfter(
          supabaseUrl,
          serviceKey,
          eduSeq!,
        );
        const wrap = nextSeq == null
          ? await fetchMinEduSeq(supabaseUrl, serviceKey)
          : nextSeq;
        console.log(
          `[edu-crawl] 신청자: 빈 목록 (교육 seq=${eduSeq}). ` +
            `다음 target_edu_seq=${wrap ?? "null"}, 1페이지부터.` +
            (nextSeq == null
              ? " (끝 seq였으면 최소 seq로 순환)"
              : ""),
        );
        await patchApplicantProgress(supabaseUrl, serviceKey, {
          target_edu_seq: wrap,
          next_page: 1,
        });
        crawlDone = true;
        lastPage = page;
        break;
      }

      const payloads: Record<string, unknown>[] = [];
      for (const row of rows) {
        const p = rowToApplicantPayload(row);
        if (p) payloads.push(p);
      }

      const payloadsDeduped = dedupeApplicantPayloads(payloads);
      if (payloadsDeduped.length < payloads.length) {
        console.log(
          `[edu-crawl] 신청자 user_id 중복 제거: ${payloads.length} → ${payloadsDeduped.length} (edu_seq=${eduSeq})`,
        );
      }

      if (rows.length && !payloadsDeduped.length) {
        const nextSeq = await fetchNextEduSeqAfter(
          supabaseUrl,
          serviceKey,
          eduSeq!,
        );
        const wrap = nextSeq == null
          ? await fetchMinEduSeq(supabaseUrl, serviceKey)
          : nextSeq;
        console.log(
          `[edu-crawl] 신청자: 표는 ${rows.length}행이나 user_id 0건(파싱 실패·잘못된 테이블). ` +
            `edu_seq=${eduSeq} page=${page} — 다음 seq=${wrap ?? "null"}로 이동(무한 페이징 방지).`,
        );
        await patchApplicantProgress(supabaseUrl, serviceKey, {
          target_edu_seq: wrap,
          next_page: 1,
        });
        crawlDone = true;
        lastPage = page;
        processedPages.push(page);
        break;
      }

      if (payloadsDeduped.length) {
        const rpcRes = await fetch(`${rpcBase}/upsert_edu_applicant_batch`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${serviceKey}`,
            apikey: serviceKey,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            p_edu_seq: eduSeq,
            p_rows: payloadsDeduped,
          }),
        });
        const rpcText = await rpcRes.text();
        if (!rpcRes.ok) {
          return new Response(
            JSON.stringify({
              success: false,
              error: "upsert_edu_applicant_batch 실패",
              status: rpcRes.status,
              body: rpcText.slice(0, 1200),
              edu_seq: eduSeq,
              page,
            }),
            {
              status: 502,
              headers: { ...corsHeaders, "Content-Type": "application/json" },
            },
          );
        }
        const aptTouched = parseRpcInt(rpcText);
        totalUpserted += aptTouched;
        console.log(
          `[edu-crawl] 신청자 교육 seq=${eduSeq} 페이지 ${page}: RPC 영향 행 수=${aptTouched} ` +
            "(upsert: 동일 신청은 갱신)",
        );
      }

      const last = pageHasNumOne(rows);
      const hitCap = page >= maxPages;
      const doneWithThisEdu = !applyListPaginated || last || hitCap;

      if (doneWithThisEdu) {
        const nextSeq = await fetchNextEduSeqAfter(
          supabaseUrl,
          serviceKey,
          eduSeq!,
        );
        const wrap = nextSeq == null
          ? await fetchMinEduSeq(supabaseUrl, serviceKey)
          : nextSeq;
        console.log(
          `[edu-crawl] 신청자: 교육 seq=${eduSeq} 구간 완료 → 다음 seq=${wrap ?? "null"}, 1페이지부터.` +
            (nextSeq == null ? " (순환: 최소 seq)" : ""),
        );
        await patchApplicantProgress(supabaseUrl, serviceKey, {
          target_edu_seq: wrap,
          next_page: 1,
        });
        crawlDone = true;
      } else {
        await patchApplicantProgress(supabaseUrl, serviceKey, {
          target_edu_seq: eduSeq,
          next_page: page + 1,
        });
      }

      processedPages.push(page);
      lastPage = page;
      if (doneWithThisEdu) break;
    }

    return new Response(
      JSON.stringify({
        success: true,
        mode: "applicants",
        edu_seq: eduSeq,
        pages_processed: processedPages.length,
        pages: processedPages,
        end_page: lastPage,
        crawl_done: crawlDone,
        rows_upsert_touched: totalUpserted,
        cycled_to_next_edu: crawlDone,
        message_ko: crawlDone
          ? "신청자: 교육 seq 한 덩어리 끝나면 다음 seq·1페이지. 마지막 seq 다음은 최소 seq로 순환합니다."
          : "진행 중: 다음 호출은 edu_applicant_crawl_progress 기준 이어서.",
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  } catch (error) {
    console.error("edu-crawl", error);
    return new Response(
      JSON.stringify({
        success: false,
        error: "서버 오류",
        detail: String(error),
      }),
      {
        status: 500,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      },
    );
  }
});
