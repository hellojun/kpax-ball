/**
 * Polymarket page detector — identify football markets from URL and page content.
 *
 * Polymarket uses two URL patterns:
 * - /event/{slug}       — legacy event pages
 * - /sports/{slug}      — sports-specific pages (e.g. epl-mac-cry-2026-03-21)
 */

const FOOTBALL_KEYWORDS = [
  // Major leagues
  "premier league", "epl",
  "la liga", "serie a", "bundesliga", "ligue 1",
  "champions league", "europa league", "conference league",
  "world cup", "fifa",
  // Asian leagues
  "chinese super league", "chinese a-league", "csl",
  "j-league", "j league", "k-league", "k league",
  // Other
  "eredivisie", "primeira liga", "liga mx", "mls",
  "copa libertadores", "copa america", "euro ",
  // Generic football terms（Polymarket 页面常见）
  "football", "soccer", " fc ", " fc,",
  " vs ", " v ",
];

/** EPL team codes commonly found in Polymarket sports slugs */
const EPL_TEAM_CODES = [
  "ars", "avl", "bou", "bre", "bri", "che", "cry", "eve", "ful",
  "ips", "lei", "liv", "mac", "mau", "new", "nfo", "sou", "tot",
  "whu", "wol",
];

/** Extract slug from Polymarket URL.
 *
 * URL formats:
 * - /event/{slug}
 * - /sports/{slug}
 * - /sports/{league}/{slug}  (e.g. /sports/epl/epl-mac-cry-2026-03-21)
 * - /zh/sports/{league}/{slug}  (带语言前缀)
 */
export function extractSlugFromUrl(url: string): string | null {
  // 跳过可选的语言前缀 (zh, en, fr, etc.)，匹配最后一段路径
  const match = url.match(
    /polymarket\.com\/(?:[a-z]{2}\/)?(?:event|sports)\/(?:[^?#/]+\/)*([^?#/]+)/
  );
  return match ? match[1] : null;
}

/** Check if page title or content suggests a football market */
export function isFootballRelated(text: string): boolean {
  const lower = text.toLowerCase();
  return FOOTBALL_KEYWORDS.some((kw) => lower.includes(kw));
}

/** Known football league codes in Polymarket sports URL: /sports/{code}/{slug} */
const FOOTBALL_LEAGUE_CODES = [
  "epl", "cal", "ger", "esp", "ita", "fra",  // 五大联赛 + 中超
  "ucl", "uel", "uecl",                        // 欧战
  "wc", "euro", "copa",                         // 国家队赛事
  "mls", "jpn", "kor", "bra", "arg", "ned",    // 其他联赛
];

/** Check if a sports slug looks like a football match */
export function isSportsSlugFootball(slug: string): boolean {
  const lower = slug.toLowerCase();
  // 直接匹配已知联赛前缀
  if (FOOTBALL_LEAGUE_CODES.some((code) => lower.startsWith(code + "-"))) return true;
  if (lower.includes("world-cup") || lower.includes("champions")) return true;
  return false;
}

/** Extract league code from URL path: /sports/{leagueCode}/{slug} → leagueCode */
export function extractLeagueCodeFromUrl(url: string): string | null {
  const match = url.match(/polymarket\.com\/(?:[a-z]{2}\/)?sports\/([^/?#]+)\//);
  return match ? match[1].toLowerCase() : null;
}

/** Check if current page is a Polymarket event or sports page */
export function isPolymarketEventPage(): boolean {
  return /^https:\/\/polymarket\.com\/([a-z]{2}\/)?(event|sports)\//.test(
    window.location.href
  );
}
