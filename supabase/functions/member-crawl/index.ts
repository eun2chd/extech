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
 *     CRAWL_MEMBER_FORM_PATH, CRAWL_MEMBER_FORM_EXTRA_QUERY
 *   DB (RPC)
 *     SUPABASE_SERVICE_ROLE_KEY (또는 ETK_SERVICE_ROLE_KEY)
 *   호출 인증 (생일 워크플로와 동일)
 *     요청 Authorization: Bearer <SUPABASE_ANON_KEY> — 플랫폼에서 주입되는 ANON 과 일치해야 함
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

    const anon =
      Deno.env.get("SUPABASE_ANON_KEY") || Deno.env.get("ETK_ANON_KEY");
    const auth = req.headers.get("Authorization") ?? "";
    const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
    if (!anon || token !== anon) {
      return new Response(
        JSON.stringify({ success: false, error: "Unauthorized" }),
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
    const formPath = (Deno.env.get("CRAWL_MEMBER_FORM_PATH") ??
      "/admin/member/member_form.html").trim();
    const formExtra = (Deno.env.get("CRAWL_MEMBER_FORM_EXTRA_QUERY") ??
      "select_key=&input_key=&search=&sort=member&type=P&page=1").trim();

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

    const jar = new Map<string, string>();
    const ua =
      "Mozilla/5.0 (compatible; MemberCrawl-Edge/1.0; +supabase-edge)";

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

    let totalInserted = 0;
    let pages = 0;

    for (let page = 1; page <= maxPages; page++) {
      const path = listPathForPage(MEMBER_LIST_PATH, page);
      const listUrl = resolveUrl(baseUrl, path);
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
          JSON.stringify({ success: false, error: String(e) }),
          {
            status: 500,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }

      if (!rows.length) {
        console.log("empty page", page, "stop");
        break;
      }

      const payloads: Record<string, unknown>[] = [];

      for (let i = 0; i < rows.length; i++) {
        const row = rows[i]!;
        let memo = "";
        if (fetchMemo && row._seq) {
          if (i > 0 && memoDelay > 0) {
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
        if (p) payloads.push(p);
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
        if (typeof n === "number" && !Number.isNaN(n)) totalInserted += n;
      }

      pages++;
      const last = pageHasNumOne(rows);
      if (last) {
        console.log("last page (번호=1) at page", page);
        break;
      }
    }

    return new Response(
      JSON.stringify({
        success: true,
        pages,
        inserted_approx: totalInserted,
        message:
          "inserted_approx 는 RPC 가 반환한 신규 행 수 합(문자열 파싱)",
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
