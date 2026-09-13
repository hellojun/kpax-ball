import { API_BASE_URL } from "./constants";
import { getToken } from "./auth";
import type { AnalysisMessage, QuickPreview } from "./types";

/** 构建带 auth token 的请求头 */
async function authHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = await getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

/** Call KPAX Ball backend API */
async function apiRequest<T>(
  method: string,
  path: string,
  body?: unknown
): Promise<T> {
  const headers = await authHeaders();
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!resp.ok) {
    throw new Error(`API error: ${resp.status} ${resp.statusText}`);
  }

  return resp.json() as Promise<T>;
}

/** Get quick preview for a market */
export async function getPreview(
  slug: string,
  polymarketOdds?: Record<string, number>,
  language: string = "zh",
  homeTeam?: string,
  awayTeam?: string,
  competition?: string,
): Promise<QuickPreview> {
  return apiRequest("POST", "/api/analysis/preview", {
    slug,
    polymarket_odds: polymarketOdds,
    home_team: homeTeam,
    away_team: awayTeam,
    competition,
    language,
  });
}

/** Stream deep analysis via SSE (POST + ReadableStream).
 *
 * Returns an AbortController so the caller can cancel the stream.
 */
export function streamDeepAnalysis(
  slug: string,
  polymarketOdds?: Record<string, number>,
  userContext?: string,
  onMessage?: (msg: AnalysisMessage) => void,
  onDone?: () => void,
  onError?: (err: Error) => void,
  language: string = "zh",
  homeTeam?: string,
  awayTeam?: string,
  competition?: string,
): AbortController {
  const controller = new AbortController();

  (async () => {
    const headers = await authHeaders();
    try {
      const resp = await fetch(`${API_BASE_URL}/api/analysis/deep`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          slug,
          polymarket_odds: polymarketOdds,
          user_context: userContext,
          home_team: homeTeam,
          away_team: awayTeam,
          competition,
          language,
        }),
        signal: controller.signal,
      });

      if (!resp.ok) throw new Error(`API error: ${resp.status}`);
      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error("No response body");

      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6)) as AnalysisMessage;
              onMessage?.(data);
            } catch { /* skip */ }
          }
        }
      }
      onDone?.();
    } catch (err: any) {
      if (err.name !== "AbortError") onError?.(err);
    }
  })();

  return controller;
}

/** Follow-up question — re-synthesize report without re-running debate */
export function streamFollowUp(
  slug: string,
  question: string,
  polymarketOdds: Record<string, number> | undefined,
  debateMessages: Array<Record<string, unknown>>,
  previousReport: Record<string, unknown>,
  onMessage?: (msg: AnalysisMessage) => void,
  onDone?: () => void,
  onError?: (err: Error) => void,
  language: string = "zh"
): AbortController {
  const controller = new AbortController();

  (async () => {
    const headers = await authHeaders();
    try {
      const resp = await fetch(`${API_BASE_URL}/api/analysis/followup`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          slug,
          question,
          polymarket_odds: polymarketOdds,
          debate_messages: debateMessages,
          previous_report: previousReport,
          language,
        }),
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(`API error: ${resp.status}`);
      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error("No response body");

      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6)) as AnalysisMessage;
              onMessage?.(data);
            } catch { /* skip */ }
          }
        }
      }
      onDone?.();
    } catch (err: any) {
      if (err.name !== "AbortError") onError?.(err);
    }
  })();

  return controller;
}

/** Get verification stats */
export async function getVerificationStats(competition?: string) {
  const params = competition ? `?competition=${competition}` : "";
  return apiRequest("GET", `/api/verification/stats${params}`);
}
