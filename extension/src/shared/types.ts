/** 支持的语言 */
export type Lang = "zh" | "en";

/** Structured football market data extracted from Polymarket */
export interface FootballMarket {
  slug: string;
  homeTeam: string;
  awayTeam: string;
  competition: string;
  marketType: "match_winner" | "over_under" | "both_to_score";
  polymarketOdds: {
    home?: number;
    away?: number;
    draw?: number;
    yes?: number;
    no?: number;
  };
  conditionId: string;
  clobTokenIds: string[];
  endDate: string;
  volume: number;
}

/** Quick preview response from KPAX API */
export interface QuickPreview {
  summary: string;
  kpaxOdds: Record<string, number>;
  marketDeviation: Record<string, number>;
  confidence: "high" | "medium" | "low";
  confidenceReason: string;
}

/** SSE message from deep analysis stream */
export interface AnalysisMessage {
  type: "status" | "expert" | "moderator" | "report" | "error" | "followup_answer";
  expert?: string;
  role?: string;
  round?: number;
  content: string | FullReport;
}

/** Full analysis report */
export interface FullReport {
  coreJudgment: {
    homeWinPct: number;
    awayWinPct: number;
    drawPct: number;
    confidence: "high" | "medium" | "low";
    confidenceReason: string;
    marketDeviation: Record<string, number>;
  };
  sections: {
    dimension: string;
    title: string;
    content: string;
  }[];
  keyVariables: {
    description: string;
    impact: string;
  }[];
  disagreements: {
    expertA: string;
    expertB: string;
    topic: string;
    summary: string;
  }[];
  historicalAccuracy: string | null;
}

/** 主题模式 */
export type Theme = "dark" | "light";

/** Messages between content script, service worker, and side panel */
export type ExtensionMessage =
  | { type: "MARKET_DETECTED"; market: FootballMarket }
  | { type: "MARKET_LEFT" }
  | { type: "OPEN_SIDEPANEL" }
  | { type: "THEME_CHANGED"; theme: Theme }
  | { type: "REQUEST_PREVIEW"; slug: string }
  | { type: "REQUEST_DEEP_ANALYSIS"; slug: string; userContext?: string }
  | { type: "PREVIEW_RESULT"; preview: QuickPreview }
  | { type: "ANALYSIS_MESSAGE"; message: AnalysisMessage };
