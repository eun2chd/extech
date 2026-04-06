// Setup type definitions for built-in Supabase Runtime APIs
// @deno-types="https://esm.sh/@supabase/functions-js/src/edge-runtime.d.ts"
/**
 * 회원 목록 풀 크롤 → members_crawled RPC (seq 중복 skip)
 *
 * GitHub Actions 는 Anon 키로 POST 만 호출하면 됨.
 * 나머지 비밀·URL 은 Supabase Edge Function Secrets 에만 둠.
 *
 * Secrets (Edge):
 *   크롤 대상
 *     CRAWL_BASE_URL, CRAWL_LOGIN_PATH
 *     (회원 목록 URL 은 ex-tech 전용 상수로 고정 — 아래 MEMBER_LIST_PATH)
 *     CRAWL_ADMIN_USER, CRAWL_ADMIN_PASSWORD
 *     CRAWL_LOGIN_USER_FIELD (기본 m_id), CRAWL_LOGIN_PASS_FIELD (기본 m_pass)
 *     CRAWL_TABLE_SELECTOR (기본 table.list_table)
 *     CRAWL_FETCH_MEMO (true/false), CRAWL_MEMO_DELAY_MS, CRAWL_MAX_LIST_PAGES
 *     CRAWL_PAGES_PER_RUN (기본 1) — 한 번에 목록 몇 페이지까지 처리할지
 *     CRAWL_MEMBER_FORM_PATH, CRAWL_MEMBER_FORM_EXTRA_QUERY
 *
 *   요청 JSON (선택)
 *     page_count — 이번 호출에서 처리할 목록 페이지 수 (시크릿 기본값보다 우선)
 *     reset: true — 진행을 1페이지부터 다시 (DB next_page 를 1로 맞춤)
 *     start_page — 다음 페이지 대신 이 번호부터 처리 (체크포인트 읽기 무시)
 *
 *   진행 저장: public.member_crawl_progress (id=member_list, next_page)
 *   DB (RPC)
 *     SUPABASE_SERVICE_ROLE_KEY (또는 ETK_SERVICE_ROLE_KEY)
 *   호출 인증 (둘 중 하나)
 *     A) Authorization: Bearer <ANON> (apikey 생략 가능, 넣으면 ANON 과 동일 권장)
 *     B) 서버 간: Authorization: Bearer <SERVICE_ROLE> + apikey: <SERVICE_ROLE> (둘 다 필수, 게이트웨이 401 방지)
 *
 * 배포: supabase functions deploy member-crawl --no-verify-jwt
 */
import { load } from "npm:cheerio@1.0.0-rc.12";

/** ex-tech 회원 목록 (page 는 실행 시 치환) */
const MEMBER_LIST_PATH =
  "/admin/member/member_list.html?select_key=&input_key=&search=&sort=member&type=P&page=1";

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

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

function normalizeKey(s: string): string {
  let key = s.trim().replace(/\s+/g, "_");
  key = key.replace(/[^\w가-힣]+/gu, "");
  return key || "col";
}

function cellText($: ReturnType<typeof load>, el: unknown): string {
  return $(el as never).text().replace(/\s+/g, " ").trim();
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
  console.log("[member-crawl] parseTable headers (index:key):");
  headers.forEach((h, idx) => console.log(`[member-crawl]   [${idx}] ${h}`));

  const out: Record<string, string>[] = [];
  const dataRows = rows.toArray().slice(1);
  for (const tr of dataRows) {
    const $tr = $(tr);
    const cells = $tr.find("td, th");
    if (!cells.length) continue;
    const row: Record<string, string> = {};
    const cb = $tr.find('input[type="checkbox"][name*="seq_list"]').first();
    const v = cb.attr("value");
    if (v) row._seq = v.trim();
    cells.each((i, c) => {
      const key = headers[i] ?? `col_${i}`;
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

function parseLoginSocial(raw: string): { login_id: string; social: string | null } {
  const s = (raw || "").trim();
  if (!s) return { login_id: "", social: null };
  let social: string | null = null;
  if (s.includes("네이버")) social = "naver";
  else if (s.includes("카카오")) social = "kakao";
  let login_id: string;
  if (s.includes(" (")) login_id = s.split(" (", 1)[0]!.trim();
  else if (s.includes(" [")) login_id = s.split(" [", 1)[0]!.trim();
  else login_id = s.split(/\s+/)[0] ?? "";
  return { login_id, social };
}

function rowToPayload(
  row: Record<string, string>,
  memo: string,
): Record<string, unknown> | null {
  const seqRaw = row._seq;
  if (!seqRaw?.trim()) return null;
  const seq = parseInt(seqRaw.trim(), 10);
  if (Number.isNaN(seq)) return null;

  let num: number | null = null;
  const nr = row["번호"];
  if (nr != null && String(nr).trim() !== "") {
    const n = parseInt(String(nr).trim(), 10);
    num = Number.isNaN(n) ? null : n;
  }

  const { login_id, social } = parseLoginSocial(row["아이디"] ?? "");

  const joinRaw = row["가입일"];
  const join_date = joinRaw != null && String(joinRaw).trim()
    ? String(joinRaw).trim()
    : null;

  return {
    seq,
    num,
    login_id: login_id || null,
    social_type: social,
    name: (row["이름"] ?? "").trim() || null,
    phone: (row["연락처"] ?? "").trim() || null,
    email: (row["이메일"] ?? "").trim() || null,
    join_date,
    status: (row["상태"] ?? "").trim() || null,
    memo: memo.trim() || null,
  };
}

function parseMemoFromFormHtml(html: string): string {
  const $ = load(html);
  const ta = $("#m_memo").first().length
    ? $("#m_memo")
    : $('textarea[name="m_memo"]').first();
  return ta.length ? ta.text().trim() : "";
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

/** 디버그: 객체의 모든 키·값 로그 (민감값은 마스크) */
function logKeyValues(
  label: string,
  obj: Record<string, unknown>,
  secretKeys?: ReadonlySet<string>,
) {
  const secrets = secretKeys ?? new Set<string>();
  for (const [k, v] of Object.entries(obj)) {
    if (secrets.has(k)) {
      const s = typeof v === "string" ? v : String(v);
      console.log(`[member-crawl] ${label} ${k}=`, s ? `<redacted len=${s.length}>` : "");
      continue;
    }
    console.log(`[member-crawl] ${label} ${k}=`, v);
  }
}

function serviceRestHeaders(serviceKey: string): Record<string, string> {
  return {
    Authorization: `Bearer ${serviceKey}`,
    apikey: serviceKey,
  };
}

async function fetchProgressNextPage(
  supabaseUrl: string,
  serviceKey: string,
): Promise<number> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/member_crawl_progress?select=next_page&id=eq.member_list`;
  const r = await fetch(url, { headers: serviceRestHeaders(serviceKey) });
  const text = await r.text();
  if (!r.ok) {
    console.error("[member-crawl] progress GET failed", r.status, text.slice(0, 400));
    throw new Error(`member_crawl_progress GET HTTP ${r.status}`);
  }
  let arr: unknown;
  try {
    arr = JSON.parse(text);
  } catch {
    throw new Error("member_crawl_progress GET: invalid JSON");
  }
  if (!Array.isArray(arr) || arr.length === 0) return 1;
  const raw = (arr[0] as { next_page?: unknown }).next_page;
  const page = typeof raw === "number" && Number.isFinite(raw)
    ? Math.floor(raw)
    : parseInt(String(raw ?? ""), 10);
  if (Number.isNaN(page) || page < 1) return 1;
  return page;
}

async function patchProgressNextPage(
  supabaseUrl: string,
  serviceKey: string,
  nextPage: number,
): Promise<void> {
  const n = Math.max(1, Math.floor(nextPage));
  const base = supabaseUrl.replace(/\/$/, "");
  const url = `${base}/rest/v1/member_crawl_progress?id=eq.member_list`;
  const r = await fetch(url, {
    method: "PATCH",
    headers: {
      ...serviceRestHeaders(serviceKey),
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify({ next_page: n }),
  });
  if (!r.ok) {
    const t = await r.text();
    console.error("[member-crawl] progress PATCH failed", r.status, t.slice(0, 400));
    throw new Error(`member_crawl_progress PATCH HTTP ${r.status}`);
  }
}

/** 테이블만 있고 seed 행이 없을 때 PATCH 가 무반응이 되는 것을 막음 */
async function ensureProgressRow(
  supabaseUrl: string,
  serviceKey: string,
): Promise<void> {
  const base = supabaseUrl.replace(/\/$/, "");
  const url =
    `${base}/rest/v1/member_crawl_progress?select=id&id=eq.member_list`;
  const r = await fetch(url, { headers: serviceRestHeaders(serviceKey) });
  const text = await r.text();
  if (!r.ok) {
    console.error("[member-crawl] progress ensure GET", r.status, text.slice(0, 300));
    throw new Error(`member_crawl_progress ensure GET HTTP ${r.status}`);
  }
  let arr: unknown;
  try {
    arr = JSON.parse(text) as unknown;
  } catch {
    throw new Error("member_crawl_progress ensure: invalid JSON");
  }
  if (Array.isArray(arr) && arr.length > 0) return;
  const ins = await fetch(`${base}/rest/v1/member_crawl_progress`, {
    method: "POST",
    headers: {
      ...serviceRestHeaders(serviceKey),
      "Content-Type": "application/json",
      Prefer: "return=minimal",
    },
    body: JSON.stringify({ id: "member_list", next_page: 1 }),
  });
  if (!ins.ok && ins.status !== 409) {
    const t = await ins.text();
    console.error("[member-crawl] progress ensure POST", ins.status, t.slice(0, 300));
    throw new Error(`member_crawl_progress ensure POST HTTP ${ins.status}`);
  }
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

    const anon = (
      Deno.env.get("ETK_ANON_KEY") ||
      ""
    ).trim();
    const serviceRole = (
      Deno.env.get("ETK_SERVICE_ROLE_KEY") ||
      ""
    ).trim();
    const auth = req.headers.get("Authorization") ?? "";
    const token = (auth.startsWith("Bearer ") ? auth.slice(7) : "").trim();
    const apikeyHeader = (req.headers.get("apikey") ?? "").trim();

    const okAnon = !!anon && token === anon &&
      (!apikeyHeader || apikeyHeader === anon);
    const okService = !!serviceRole && token === serviceRole &&
      apikeyHeader === serviceRole;

    console.log("[member-crawl] request auth:", {
      method: req.method,
      hasAuthorization: !!auth,
      tokenLen: token.length,
      apikeyHeaderLen: apikeyHeader.length,
      okAnon,
      okService,
    });

    if (!okAnon && !okService) {
      return new Response(
        JSON.stringify({
          success: false,
          error: "Unauthorized",
          edge_has_service_role: serviceRole.length > 0,
          edge_has_anon: anon.length > 0,
          hint:
            "SERVICE_ROLE 로 호출 시: (1) Supabase Edge 시크릿에 SUPABASE_SERVICE_ROLE_KEY 가 있어야 하고 (2) 그 값과 GitHub Secret 이 한 글자까지 동일해야 합니다. eyJ…(JWT) 와 sb_… 를 섞어 쓰면 안 됩니다. ANON 만 쓸 땐 Bearer=ANON, apikey 는 비우거나 ANON 과 동일.",
        }),
        {
          status: 401,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const baseUrl = (Deno.env.get("CRAWL_BASE_URL") ?? "").trim().replace(/\/$/, "");
    const loginPath = Deno.env.get("CRAWL_LOGIN_PATH") ?? "";
    const adminUser = Deno.env.get("CRAWL_ADMIN_USER") ?? "";
    const adminPass = Deno.env.get("CRAWL_ADMIN_PASSWORD") ?? "";
    const userField = Deno.env.get("CRAWL_LOGIN_USER_FIELD") ?? "m_id";
    const passField = Deno.env.get("CRAWL_LOGIN_PASS_FIELD") ?? "m_pass";
    const tableSel = Deno.env.get("CRAWL_TABLE_SELECTOR") ?? "table.list_table";
    const fetchMemo = (Deno.env.get("CRAWL_FETCH_MEMO") ?? "true")
      .toLowerCase() === "true";
    const memoDelay = Math.max(
      0,
      parseInt(Deno.env.get("CRAWL_MEMO_DELAY_MS") ?? "0", 10) || 0,
    );
    const maxPages = Math.max(
      1,
      parseInt(Deno.env.get("CRAWL_MAX_LIST_PAGES") ?? "2000", 10) || 2000,
    );
    const pagesPerRunDefault = Math.max(
      1,
      parseInt(Deno.env.get("CRAWL_PAGES_PER_RUN") ?? "1", 10) || 1,
    );
    const formPath = (Deno.env.get("CRAWL_MEMBER_FORM_PATH") ??
      "/admin/member/member_form.html").trim();
    const formExtra = (Deno.env.get("CRAWL_MEMBER_FORM_EXTRA_QUERY") ??
      "select_key=&input_key=&search=&sort=member&type=P&page=1").trim();

    const supabaseUrl = (Deno.env.get("SUPABASE_URL") ?? "").trim();
    const serviceKey =
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ||
      Deno.env.get("ETK_SERVICE_ROLE_KEY") ||
      "";

    logKeyValues(
      "config",
      {
        CRAWL_BASE_URL: baseUrl,
        CRAWL_LOGIN_PATH: loginPath,
        CRAWL_ADMIN_USER: adminUser,
        CRAWL_ADMIN_PASSWORD: adminPass,
        CRAWL_LOGIN_USER_FIELD: userField,
        CRAWL_LOGIN_PASS_FIELD: passField,
        CRAWL_TABLE_SELECTOR: tableSel,
        CRAWL_FETCH_MEMO: fetchMemo,
        CRAWL_MEMO_DELAY_MS: memoDelay,
        CRAWL_MAX_LIST_PAGES: maxPages,
        CRAWL_PAGES_PER_RUN: pagesPerRunDefault,
        CRAWL_MEMBER_FORM_PATH: formPath,
        CRAWL_MEMBER_FORM_EXTRA_QUERY: formExtra,
        SUPABASE_URL: supabaseUrl,
        SUPABASE_SERVICE_ROLE_KEY: serviceKey,
      },
      new Set(["CRAWL_ADMIN_PASSWORD", "SUPABASE_SERVICE_ROLE_KEY"]),
    );

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
      await ensureProgressRow(supabaseUrl, serviceKey);
    } catch (e) {
      return new Response(
        JSON.stringify({
          success: false,
          error:
            "member_crawl_progress 준비 실패 (테이블·권한·schema_members_crawled.sql 확인)",
          detail: String(e),
        }),
        {
          status: 502,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const jar = new Map<string, string>();
    const ua =
      "Mozilla/5.0 (compatible; MemberCrawl-Edge/1.0; +supabase-edge)";

    const loginUrl = resolveUrl(baseUrl, loginPath);
    const body = new URLSearchParams();
    body.set(userField, adminUser);
    body.set(passField, adminPass);
    console.log("[member-crawl] login POST", { loginUrl });
    for (const [k, v] of body.entries()) {
      const secret = k === passField;
      console.log(
        `[member-crawl] login body ${k}=`,
        secret ? (v ? `<redacted len=${v.length}>` : "") : v,
      );
    }

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
    console.log("[member-crawl] cookie jar after login:");
    for (const [k, v] of jar.entries()) {
      console.log(
        `[member-crawl]   cookie ${k}=`,
        v ? `<value len=${v.length}>` : "",
      );
    }

    const pagesPerRunCap = 50;
    const pagesPerRunFromBody =
      typeof bodyJson.page_count === "number" &&
        Number.isFinite(bodyJson.page_count)
        ? Math.floor(bodyJson.page_count)
        : null;
    const pagesPerRun = Math.min(
      pagesPerRunCap,
      Math.max(
        1,
        pagesPerRunFromBody ?? pagesPerRunDefault,
      ),
    );
    const resetCheckpoint = bodyJson.reset === true;
    const explicitStart =
      typeof bodyJson.start_page === "number" &&
        Number.isFinite(bodyJson.start_page)
        ? Math.max(1, Math.floor(bodyJson.start_page))
        : null;

    if (resetCheckpoint) {
      await patchProgressNextPage(supabaseUrl, serviceKey, 1);
    }

    let startPage: number;
    if (explicitStart != null) {
      startPage = explicitStart;
    } else {
      try {
        startPage = await fetchProgressNextPage(supabaseUrl, serviceKey);
      } catch (e) {
        return new Response(
          JSON.stringify({
            success: false,
            error: "member_crawl_progress 조회 실패 (schema_members_crawled.sql 반영 여부 확인)",
            detail: String(e),
          }),
          {
            status: 502,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }
    }

    startPage = Math.max(1, Math.min(startPage, maxPages));

    console.log("[member-crawl] chunk run", {
      pagesPerRun,
      startPage,
      resetCheckpoint,
      explicitStart,
    });

    let totalInserted = 0;
    let pagesProcessed = 0;
    const processedPageNumbers: number[] = [];
    let crawlDone = false;
    let lastPageHandled: number | null = null;

    for (let i = 0; i < pagesPerRun; i++) {
      const page = startPage + i;
      if (page > maxPages) {
        console.log(
          "[member-crawl] page exceeds CRAWL_MAX_LIST_PAGES",
          page,
          maxPages,
        );
        break;
      }

      const path = listPathForPage(MEMBER_LIST_PATH, page);
      const listUrl = resolveUrl(baseUrl, path);
      console.log("[member-crawl] list fetch", { page, path, listUrl });
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
        console.error("list fetch failed", listRes.status, listUrl);
        return new Response(
          JSON.stringify({
            success: false,
            error: `목록 요청 실패 HTTP ${listRes.status}`,
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
        console.error("parseTable", e);
        return new Response(
          JSON.stringify({ success: false, error: String(e), page }),
          {
            status: 500,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }

      if (!rows.length) {
        console.log("empty page", page, "checkpoint → next_page=1");
        try {
          await patchProgressNextPage(supabaseUrl, serviceKey, 1);
        } catch (e) {
          return new Response(
            JSON.stringify({
              success: false,
              error: "member_crawl_progress 갱신 실패 (빈 목록 후)",
              detail: String(e),
            }),
            {
              status: 502,
              headers: { ...corsHeaders, "Content-Type": "application/json" },
            },
          );
        }
        crawlDone = true;
        lastPageHandled = page;
        break;
      }

      const payloads: Record<string, unknown>[] = [];

      for (let ri = 0; ri < rows.length; ri++) {
        const row = rows[ri]!;
        console.log(`[member-crawl] page=${page} row=${ri} keys:`);
        for (const [k, v] of Object.entries(row)) {
          console.log(`[member-crawl]   ${k}=`, JSON.stringify(v));
        }
        let memo = "";
        if (fetchMemo && row._seq) {
          if (ri > 0 && memoDelay > 0) {
            await new Promise((r) => setTimeout(r, memoDelay));
          }
          const q = `mode=modify&seq=${encodeURIComponent(row._seq)}${
            formExtra ? `&${formExtra}` : ""
          }`;
          const fu = resolveUrl(baseUrl, `${formPath.split("?")[0]}?${q}`);
          try {
            const mr = await fetch(fu, {
              headers: {
                Cookie: cookieHeader(jar),
                "User-Agent": ua,
                Accept:
                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
              },
            });
            await applySetCookie(mr, jar);
            const mh = await mr.text();
            memo = parseMemoFromFormHtml(mh);
          } catch (err) {
            console.warn("memo fetch", row._seq, err);
          }
        }
        const p = rowToPayload(row, memo);
        if (p) {
          console.log(`[member-crawl] page=${page} row=${ri} payload keys:`);
          for (const [k, v] of Object.entries(p)) {
            console.log(`[member-crawl]   ${k}=`, v);
          }
          payloads.push(p);
        }
      }

      if (payloads.length) {
        const rpcRes = await fetch(
          `${supabaseUrl.replace(/\/$/, "")}/rest/v1/rpc/insert_members_crawled_batch`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${serviceKey}`,
              apikey: serviceKey,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ p_rows: payloads }),
          },
        );
        const rpcText = await rpcRes.text();
        if (!rpcRes.ok) {
          console.error("RPC error", rpcRes.status, rpcText.slice(0, 800));
          return new Response(
            JSON.stringify({
              success: false,
              error: "RPC insert_members_crawled_batch 실패",
              status: rpcRes.status,
              body: rpcText.slice(0, 1500),
              page,
            }),
            {
              status: 502,
              headers: { ...corsHeaders, "Content-Type": "application/json" },
            },
          );
        }
        let n = 0;
        try {
          n = JSON.parse(rpcText) as number;
        } catch {
          n = parseInt(rpcText.trim(), 10);
        }
        console.log("[member-crawl] RPC batch result", {
          ok: rpcRes.ok,
          status: rpcRes.status,
          parsedCount: n,
          bodyPreview: rpcText.slice(0, 200),
        });
        if (typeof n === "number" && !Number.isNaN(n)) totalInserted += n;
      } else if (rows.length) {
        console.warn(
          "[member-crawl] no valid payloads but table had rows; advancing page",
          page,
        );
      }

      const last = pageHasNumOne(rows);
      const hitMaxPagesCap = page >= maxPages;
      const nextCheckpoint = last || hitMaxPagesCap ? 1 : page + 1;
      try {
        await patchProgressNextPage(supabaseUrl, serviceKey, nextCheckpoint);
      } catch (e) {
        return new Response(
          JSON.stringify({
            success: false,
            error: "member_crawl_progress 갱신 실패",
            detail: String(e),
            page,
          }),
          {
            status: 502,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }

      pagesProcessed++;
      processedPageNumbers.push(page);
      lastPageHandled = page;

      if (last || hitMaxPagesCap) {
        if (last) console.log("last page (번호=1) at page", page);
        else {
          console.log("[member-crawl] CRAWL_MAX_LIST_PAGES 도달, checkpoint → 1", page);
        }
        crawlDone = true;
        break;
      }
    }

    const nextPageAfterRun = crawlDone
      ? 1
      : (lastPageHandled != null ? lastPageHandled + 1 : startPage);

    return new Response(
      JSON.stringify({
        success: true,
        pages_processed: pagesProcessed,
        pages: processedPageNumbers,
        start_page: processedPageNumbers[0] ?? startPage,
        end_page: lastPageHandled,
        next_page: nextPageAfterRun,
        crawl_done: crawlDone,
        inserted_approx: totalInserted,
        pages_per_run: pagesPerRun,
        message:
          "호출마다 pages_per_run 만큼만 목록을 처리하고 member_crawl_progress.next_page 에 이어서 할 페이지를 저장함. inserted_approx 는 RPC 신규 행 수 합.",
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } },
    );
  } catch (error) {
    console.error("member-crawl", error);
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
