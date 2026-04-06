// Setup type definitions for built-in Supabase Runtime APIs
// @deno-types="https://esm.sh/@supabase/functions-js/src/edge-runtime.d.ts"
/**
 * GitHub Actions 워크플로 `crawl.yml` → workflow_dispatch
 *
 * Secrets (Edge Function, admin-login 과 동일하게 ETK_* 폴백 지원):
 *   GITHUB_TOKEN / ETK_GITHUB_TOKEN
 *   GITHUB_REPO / ETK_GITHUB_REPO   (owner/repo)
 *   GITHUB_WORKFLOW / ETK_GITHUB_WORKFLOW  (기본 crawl.yml)
 *   GITHUB_REF / ETK_GITHUB_REF      (기본 main)
 *   TRIGGER_SECRET / ETK_TRIGGER_SECRET  — 설정 시 Authorization: Bearer 와 일치해야 함
 *
 * 배포 예: supabase functions deploy trigger-github-crawl --no-verify-jwt
 */

Deno.serve(async (req: Request) => {
  const corsHeaders: Record<string, string> = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers":
      "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  };

  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    if (req.method !== "GET" && req.method !== "POST") {
      return new Response(
        JSON.stringify({ success: false, error: "Method not allowed" }),
        {
          status: 405,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const secret =
      Deno.env.get("TRIGGER_SECRET") || Deno.env.get("ETK_TRIGGER_SECRET");
    if (secret) {
      const auth = req.headers.get("Authorization") ?? "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      if (token !== secret) {
        return new Response(
          JSON.stringify({ success: false, error: "Unauthorized" }),
          {
            status: 401,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
          },
        );
      }
    }

    const ghToken =
      Deno.env.get("GITHUB_TOKEN") || Deno.env.get("ETK_GITHUB_TOKEN");
    const repo =
      Deno.env.get("GITHUB_REPO") || Deno.env.get("ETK_GITHUB_REPO");
    const workflow =
      Deno.env.get("GITHUB_WORKFLOW") ||
      Deno.env.get("ETK_GITHUB_WORKFLOW") ||
      "crawl.yml";
    const ref =
      Deno.env.get("GITHUB_REF") || Deno.env.get("ETK_GITHUB_REF") || "main";

    if (!ghToken || !repo) {
      console.error("Missing GITHUB_TOKEN/ETK_GITHUB_TOKEN or GITHUB_REPO");
      return new Response(
        JSON.stringify({
          success: false,
          error:
            "서버 설정 오류: GITHUB_TOKEN(또는 ETK_GITHUB_TOKEN), GITHUB_REPO(또는 ETK_GITHUB_REPO)가 필요합니다.",
        }),
        {
          status: 500,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    const url =
      `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;

    const res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${ghToken}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref }),
    });

    const text = await res.text();
    if (!res.ok) {
      console.error("GitHub API error", res.status, text.slice(0, 500));
      return new Response(
        JSON.stringify({
          success: false,
          error: "GitHub API 호출 실패",
          status: res.status,
          body: text.slice(0, 2000),
        }),
        {
          status: 502,
          headers: { ...corsHeaders, "Content-Type": "application/json" },
        },
      );
    }

    return new Response(
      JSON.stringify({
        success: true,
        workflow,
        ref,
      }),
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      },
    );
  } catch (error) {
    console.error("trigger-github-crawl error:", error);
    return new Response(
      JSON.stringify({
        success: false,
        error: "서버 오류가 발생했습니다.",
      }),
      {
        status: 500,
        headers: {
          ...corsHeaders,
          "Content-Type": "application/json",
        },
      },
    );
  }
});
