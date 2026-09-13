/**
 * Data extractor — pull market data from Polymarket page.
 *
 * 提取策略：
 * 1. 注入脚本到页面上下文，读取 Polymarket 内部数据
 * 2. 读取 <script id="__NEXT_DATA__"> DOM 元素
 * 3. 文本兜底（简化版，只用于最后手段）
 */

import { POLYMARKET_GAMMA_URL } from "@shared/constants";
import type { FootballMarket } from "@shared/types";
import {
  extractSlugFromUrl,
  extractLeagueCodeFromUrl,
  isFootballRelated,
  isSportsSlugFootball,
} from "./detector";

const EPL_TEAM_MAP: Record<string, string> = {
  ars: "Arsenal",  avl: "Aston Villa",  bou: "Bournemouth",
  bre: "Brentford", bri: "Brighton",    che: "Chelsea",
  cry: "Crystal Palace", eve: "Everton", ful: "Fulham",
  ips: "Ipswich Town",  lei: "Leicester City", liv: "Liverpool",
  mac: "Man City",      mau: "Man United",     new: "Newcastle",
  nfo: "Nottingham Forest", sou: "Southampton", tot: "Tottenham",
  whu: "West Ham",      wol: "Wolves",
};

/** 判断 slug 是否是具体比赛（含日期），而非联赛列表页 */
function isMatchSlug(slug: string): boolean {
  return /\d{4}-\d{2}-\d{2}/.test(slug);
}

export async function extractMarketData(): Promise<FootballMarket | null> {
  const slug = extractSlugFromUrl(window.location.href);
  if (!slug) return null;

  // 列表页（如 /sports/epl、/sports/cal/epl/events）不检测
  if (!isMatchSlug(slug)) return null;

  // /sports/ 下带日期的 slug = 具体比赛，直接尝试解析
  const leagueCode = extractLeagueCodeFromUrl(window.location.href);
  const base = parseSportsSlug(slug, leagueCode);
  if (base) {
    base.polymarketOdds = await extractOdds(base.homeTeam, base.awayTeam);
    return base;
  }

  return tryGammaApi(slug);
}

// ==================== 赔率提取 ====================

async function extractOdds(
  homeTeam: string,
  awayTeam: string
): Promise<Record<string, number>> {
  // 策略 1: 读取 __NEXT_DATA__ DOM 元素（无需注入脚本，不触发 CSP）
  const nextData = extractFromNextDataElement(homeTeam, awayTeam);
  if (nextData && nextData.home != null && nextData.draw != null) {
    return nextData;
  }

  // 策略 2: DOM 元素遍历（找页面上的百分比文本）
  const domOdds = extractFromDomElements(homeTeam, awayTeam);
  if (domOdds.home != null && domOdds.away != null) {
    return domOdds;
  }

  return nextData || domOdds;
}

/**
 * 策略 1: 注入脚本到页面上下文，通过 postMessage 传回数据。
 * 页面上下文可以访问 window.__NEXT_DATA__、React fiber 等内部状态。
 */
function extractViaInjection(
  homeTeam: string,
  awayTeam: string
): Promise<Record<string, number>> {
  return new Promise((resolve) => {
    const timeoutId = setTimeout(() => {
      window.removeEventListener("message", handler);
      resolve({});
    }, 3000);

    function handler(event: MessageEvent) {
      if (event.data?.type === "KPAX_ODDS_RESULT") {
        clearTimeout(timeoutId);
        window.removeEventListener("message", handler);
        resolve(event.data.odds || {});
      }
    }
    window.addEventListener("message", handler);

    const homeLower = homeTeam.toLowerCase();
    const awayLower = awayTeam.toLowerCase();

    // 注入的脚本在页面上下文执行
    const script = document.createElement("script");
    script.textContent = `
      (function() {
        var home = ${JSON.stringify(homeLower)};
        var away = ${JSON.stringify(awayLower)};
        var odds = {};

        function matchTeam(name, team) {
          name = name.toLowerCase();
          if (name.includes(team)) return true;
          var words = team.split(' ');
          for (var i = 0; i < words.length; i++) {
            if (words[i].length > 2 && name.includes(words[i])) return true;
          }
          return false;
        }

        function searchObj(obj, depth) {
          if (depth > 10 || !obj || typeof obj !== 'object') return null;

          // 找 outcomes + outcomePrices 结构（3路市场）
          if (Array.isArray(obj.outcomes) && Array.isArray(obj.outcomePrices) && obj.outcomes.length >= 3) {
            var result = {};
            for (var i = 0; i < obj.outcomes.length; i++) {
              var name = (obj.outcomes[i] || '').toLowerCase();
              var price = parseFloat(obj.outcomePrices[i]);
              if (isNaN(price) || price <= 0 || price >= 1) continue;
              if (name === 'draw' || name === 'tie') result.draw = price;
              else if (matchTeam(name, home)) result.home = price;
              else if (matchTeam(name, away)) result.away = price;
            }
            if (result.home && result.draw && result.away) return result;
          }

          // 递归
          var keys = Array.isArray(obj) ? obj : Object.values(obj);
          for (var j = 0; j < keys.length; j++) {
            var val = Array.isArray(obj) ? obj[j] : keys[j];
            var found = searchObj(val, depth + 1);
            if (found && found.draw) return found;
          }
          return null;
        }

        try {
          // 1. window.__NEXT_DATA__
          if (window.__NEXT_DATA__) {
            var found = searchObj(window.__NEXT_DATA__, 0);
            if (found) { odds = found; }
          }

          // 2. 尝试所有 script[type="application/json"]
          if (!odds.draw) {
            var scripts = document.querySelectorAll('script[type="application/json"]');
            for (var s = 0; s < scripts.length; s++) {
              try {
                var data = JSON.parse(scripts[s].textContent);
                var found2 = searchObj(data, 0);
                if (found2 && found2.draw) { odds = found2; break; }
              } catch(e) {}
            }
          }

          // 3. 尝试 window.__polymarket__ 或其他全局变量
          if (!odds.draw) {
            var globals = ['__polymarket__', '__POLYMARKET_DATA__', '__APP_DATA__'];
            for (var g = 0; g < globals.length; g++) {
              if (window[globals[g]]) {
                var found3 = searchObj(window[globals[g]], 0);
                if (found3 && found3.draw) { odds = found3; break; }
              }
            }
          }
        } catch(e) {}

        window.postMessage({ type: 'KPAX_ODDS_RESULT', odds: odds }, '*');
      })();
    `;
    document.documentElement.appendChild(script);
    script.remove();
  });
}

/**
 * 策略 2: 直接读取 <script id="__NEXT_DATA__"> DOM 元素。
 * 内容脚本可以访问 DOM 元素（但不能访问 window.__NEXT_DATA__ JS 变量）。
 */
function extractFromNextDataElement(
  homeTeam: string,
  awayTeam: string
): Record<string, number> | null {
  try {
    const el = document.getElementById("__NEXT_DATA__");
    if (!el) return null;
    const data = JSON.parse(el.textContent || "{}");
    return findThreeWayOdds(data, homeTeam, awayTeam);
  } catch {
    return null;
  }
}

/** 递归搜索三路赔率 */
function findThreeWayOdds(
  obj: any,
  homeTeam: string,
  awayTeam: string,
  depth = 0
): Record<string, number> | null {
  if (depth > 10 || !obj || typeof obj !== "object") return null;

  if (Array.isArray(obj.outcomes) && Array.isArray(obj.outcomePrices) && obj.outcomes.length >= 3) {
    const result: Record<string, number> = {};
    for (let i = 0; i < obj.outcomes.length; i++) {
      const name = (obj.outcomes[i] || "").toLowerCase();
      const price = parseFloat(obj.outcomePrices[i]);
      if (isNaN(price) || price <= 0 || price >= 1) continue;
      if (name === "draw" || name === "tie") result.draw = price;
      else if (teamMatch(name, homeTeam)) result.home = price;
      else if (teamMatch(name, awayTeam)) result.away = price;
    }
    if (result.home != null && result.draw != null && result.away != null) return result;
  }

  const items = Array.isArray(obj) ? obj : Object.values(obj);
  for (const val of items) {
    const found = findThreeWayOdds(val, homeTeam, awayTeam, depth + 1);
    if (found) return found;
  }
  return null;
}

/**
 * 策略 3: 遍历 DOM 元素找赔率。
 * 找包含百分比的元素，检查相邻元素是否有球队名。
 */
function extractFromDomElements(
  homeTeam: string,
  awayTeam: string
): Record<string, number> {
  const odds: Record<string, number> = {};
  try {
    // 找所有包含百分比的文本节点
    const allElements = document.querySelectorAll("*");
    const pctElements: { el: Element; pct: number; text: string }[] = [];

    for (const el of allElements) {
      if (el.children.length > 0) continue;
      const text = (el.textContent || "").trim();
      const m = text.match(/^(\d{1,2}(?:\.\d{1,2})?)%$/);
      if (m) {
        const pct = parseFloat(m[1]);
        if (pct >= 1 && pct <= 99) {
          pctElements.push({ el, pct, text });
        }
      }
    }

    for (const { el, pct } of pctElements) {
      const context = getContextText(el).toLowerCase();
      if (context.includes("draw") || context.includes("平")) {
        odds.draw = pct / 100;
      } else {
        const homeScore = teamMatchScore(context, homeTeam);
        const awayScore = teamMatchScore(context, awayTeam);
        if (homeScore > awayScore && homeScore > 0) {
          odds.home = pct / 100;
        } else if (awayScore > homeScore && awayScore > 0) {
          odds.away = pct / 100;
        }
      }
    }
  } catch { /* best effort */ }
  return odds;
}

/** 获取元素附近的上下文文本（前一个兄弟、父元素等） */
function getContextText(el: Element): string {
  const parts: string[] = [];
  // 前一个兄弟元素
  if (el.previousElementSibling) {
    parts.push(el.previousElementSibling.textContent || "");
  }
  // 父元素的文本（去掉自己）
  if (el.parentElement) {
    const parentText = el.parentElement.textContent || "";
    const selfText = el.textContent || "";
    parts.push(parentText.replace(selfText, ""));
    // 父的前一个兄弟
    if (el.parentElement.previousElementSibling) {
      parts.push(el.parentElement.previousElementSibling.textContent || "");
    }
  }
  return parts.join(" ");
}

/** 计算上下文文本与球队名的匹配得分（匹配的词数越多分越高） */
function teamMatchScore(context: string, team: string): number {
  const lower = team.toLowerCase();
  // 完整匹配得最高分
  if (context.includes(lower)) return 100;
  // 按词匹配，统计命中数
  const words = lower.split(" ").filter((w) => w.length > 2 && w !== "fc" && w !== "afc" && w !== "sc");
  const matched = words.filter((w) => context.includes(w));
  return matched.length;
}

function teamMatch(name: string, team: string): boolean {
  const lower = team.toLowerCase();
  if (name.includes(lower)) return true;
  return lower.split(" ").some((w) => w.length > 2 && name.includes(w));
}

// ==================== Slug / API ====================

/** League code → competition name mapping */
const LEAGUE_COMPETITION_MAP: Record<string, string> = {
  epl: "Premier League",
  cal: "Chinese Super League",
  ger: "Bundesliga",
  esp: "La Liga",
  ita: "Serie A",
  fra: "Ligue 1",
  ucl: "Champions League",
  uel: "Europa League",
  uecl: "Conference League",
  wc: "World Cup 2026",
  mls: "MLS",
  jpn: "J-League",
  kor: "K-League",
  ned: "Eredivisie",
  sud: "Copa Sudamericana",
  lib: "Copa Libertadores",
  bra: "Brasileirão",
  arg: "Liga Profesional",
  tur: "Süper Lig",
  nor: "Eliteserien",
  sco: "Scottish Premiership",
  por: "Primeira Liga",
  mex: "Liga MX",
};

function parseSportsSlug(slug: string, leagueCode?: string | null): FootballMarket | null {
  const parts = slug.toLowerCase().split("-");

  // EPL 专用：有球队代码映射
  if (parts[0] === "epl" && parts.length >= 4) {
    const homeTeam = EPL_TEAM_MAP[parts[1]];
    const awayTeam = EPL_TEAM_MAP[parts[2]];
    if (homeTeam && awayTeam) {
      const dateParts = parts.slice(3);
      const endDate = dateParts.length >= 3 ? `${dateParts[0]}-${dateParts[1]}-${dateParts[2]}` : "";
      return {
        slug, homeTeam, awayTeam,
        competition: "Premier League", marketType: "match_winner",
        polymarketOdds: {}, conditionId: "", clobTokenIds: [], endDate, volume: 0,
      };
    }
  }

  // 通用格式：从页面中搜索 "X vs Y" 球队名
  const competition = LEAGUE_COMPETITION_MAP[leagueCode || parts[0]] || leagueCode || "Football";
  const vsMatch = findVsTextInPage();
  if (vsMatch) {
    return {
      slug,
      homeTeam: vsMatch[0],
      awayTeam: vsMatch[1],
      competition,
      marketType: "match_winner",
      polymarketOdds: {}, conditionId: "", clobTokenIds: [],
      endDate: "", volume: 0,
    };
  }

  return null;
}

/** 从页面可见文本中搜索 "X vs Y"，取字体最大的那个（即主标题） */
function findVsTextInPage(): [string, string] | null {
  const vsRegex = /(.{2,40}?)\s+(?:vs\.?|v\.?)\s+(.{2,40}?)(?:\s*[-–—|]|$)/i;
  let best: [string, string] | null = null;
  let bestSize = 0;

  for (const el of document.querySelectorAll("*")) {
    const text = (el.textContent || "").trim();
    if (!text.includes("vs") && !text.includes("VS") && !text.includes("Vs")) continue;
    if (text.length > 120) continue;

    const m = text.match(vsRegex);
    if (!m) continue;

    const fontSize = parseFloat(getComputedStyle(el).fontSize) || 0;
    if (fontSize > bestSize) {
      bestSize = fontSize;
      best = [m[1].trim(), m[2].trim()];
    }
  }

  return best;
}

async function tryGammaApi(slug: string): Promise<FootballMarket | null> {
  try {
    const resp = await fetch(`${POLYMARKET_GAMMA_URL}/events?slug=${encodeURIComponent(slug)}`);
    if (!resp.ok) return null;
    const data = await resp.json();
    const event = Array.isArray(data) ? data[0] : data;
    if (!event) return null;
    const title = event.title || event.question || "";
    if (!isFootballRelated(title)) return null;
    const m = title.match(/^(.+?)\s+(?:vs\.?|v\.?)\s+(.+?)(?:\s*[-–—|]|$)/i);
    if (!m) return null;
    return {
      slug, homeTeam: m[1].trim(), awayTeam: m[2].trim(),
      competition: detectCompetition(title), marketType: "match_winner",
      polymarketOdds: await extractOdds(m[1].trim(), m[2].trim()),
      conditionId: event.markets?.[0]?.conditionId || "",
      clobTokenIds: event.markets?.[0]?.clobTokenIds || [],
      endDate: event.markets?.[0]?.endDate || "", volume: parseFloat(event.markets?.[0]?.volume) || 0,
    };
  } catch { return null; }
}

function detectCompetition(title: string): string {
  const l = title.toLowerCase();
  if (l.includes("premier league") || l.includes("epl")) return "Premier League";
  if (l.includes("world cup")) return "World Cup 2026";
  if (l.includes("champions league")) return "Champions League";
  return "Unknown";
}
