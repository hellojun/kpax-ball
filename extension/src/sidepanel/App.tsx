import { useState, useEffect, useRef } from "react";
import { streamDeepAnalysis, streamFollowUp, getPreview } from "@shared/api";
import { signIn, signOut, getCachedUser, type UserInfo } from "@shared/auth";
import { t } from "@shared/i18n";
import type {
  AnalysisMessage,
  FootballMarket,
  FullReport,
  Lang,
  QuickPreview,
  Theme,
} from "@shared/types";
import { getUsdcBalance, formatUsdc, type UsdcBalance } from "@shared/wallet";
import type { LendingPosition, LoanItem } from "@shared/lending-api";
import MatchCard from "./components/MatchCard";
import BorrowEntry from "./components/BorrowEntry";
import BorrowFlowDialog from "./components/BorrowFlowDialog";
import RepayModal from "./components/RepayModal";
import Loading from "./components/Loading";
import QuickPreviewCard from "./components/QuickPreview";
import ExpertDebate from "./components/ExpertDebate";
import FullReportView from "./components/FullReport";
import FollowUp from "./components/FollowUp";
import Profile from "./components/Profile";
import KnowledgeGraph from "./components/charts/KnowledgeGraph";

/**
 * idle           — 等待检测足球盘口
 * match          — 已检测到球赛，显示球赛信息 + 按钮
 * loading_quick  — 正在调用简要分析 API
 * preview        — 显示简要分析结果
 * loading_deep   — 正在调用详细分析 API
 * deep           — 显示专家辩论流
 * report         — 显示最终报告
 */
type ViewState =
  | "idle"
  | "match"
  | "loading_quick"
  | "preview"
  | "loading_deep"
  | "deep"
  | "report"
  | "profile";

function shortAddress(addr: string): string {
  if (!addr || addr.length < 10) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

export default function App() {
  const [lang, setLang] = useState<Lang>("en");
  const [theme, setTheme] = useState<Theme>("dark");
  const [market, setMarket] = useState<FootballMarket | null>(null);
  const [preview, setPreview] = useState<QuickPreview | null>(null);
  const [view, setView] = useState<ViewState>("idle");
  const [debateMessages, setDebateMessages] = useState<AnalysisMessage[]>([]);
  const [report, setReport] = useState<FullReport | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [followUpQA, setFollowUpQA] = useState<{question: string; answer: string}[]>([]);
  const [user, setUser] = useState<UserInfo | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [pendingMarket, setPendingMarket] = useState<FootballMarket | null>(null);
  const [balance, setBalance] = useState<UsdcBalance | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(false);
  const [profileReturnView, setProfileReturnView] = useState<ViewState>("idle");
  // True iff the user's currently-active tab is a Polymarket page. Gates
  // whether the panel renders normal content or a "switch back to Polymarket"
  // placeholder. We follow the active tab ourselves because Chrome's
  // sidePanel.setOptions({enabled: false}) doesn't reliably close an
  // already-open panel.
  const [activeTabIsPolymarket, setActiveTabIsPolymarket] = useState(true);
  // ---- Lending dialog state (lifted from BorrowEntry) ----
  // BorrowEntry can unmount whenever view leaves "match" (e.g. content
  // script's MARKET_LEFT, even debounced, eventually fires) — so any
  // dialog/modal state stored INSIDE BorrowEntry would be lost mid-flow,
  // killing the user's UI while their MetaMask popup is still open. These
  // live at the App level so the dialogs survive any view churn.
  const [borrowSession, setBorrowSession] = useState<{
    proxy: string;
    position: LendingPosition;
  } | null>(null);
  const [repayingLoan, setRepayingLoan] = useState<LoanItem | null>(null);
  // CTFs the user *just* opened a loan against. BorrowEntry reads this to
  // optimistically hide the "borrow on this position" CTA before fetchLoans
  // returns the real `active` row. App owns the set; BorrowEntry feeds back
  // the active loans list it sees so we can drop entries that the real data
  // has caught up on.
  const [optimisticBorrowedCtfs, setOptimisticBorrowedCtfs] = useState<
    Set<string>
  >(new Set());
  // Bumped after every borrow/repay event to ask BorrowEntry to refetch.
  // BorrowEntry watches this in a useEffect dep array.
  const [lendingReloadVersion, setLendingReloadVersion] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    function evalActiveTab(tabId?: number) {
      const q = tabId !== undefined
        ? new Promise<chrome.tabs.Tab | undefined>((res) =>
            chrome.tabs.get(tabId, (t) => res(t)),
          )
        : new Promise<chrome.tabs.Tab | undefined>((res) =>
            chrome.tabs.query({ active: true, currentWindow: true }, (tabs) =>
              res(tabs[0]),
            ),
          );
      void q.then((t) => {
        const url = t?.url ?? "";
        setActiveTabIsPolymarket(url.startsWith("https://polymarket.com/"));
      });
    }
    evalActiveTab();
    const onActivated = (info: chrome.tabs.TabActiveInfo) => evalActiveTab(info.tabId);
    const onUpdated = (
      _id: number,
      changeInfo: chrome.tabs.TabChangeInfo,
      tab: chrome.tabs.Tab,
    ) => {
      if (!tab.active) return;
      if (changeInfo.url || changeInfo.status === "complete") evalActiveTab();
    };
    chrome.tabs.onActivated.addListener(onActivated);
    chrome.tabs.onUpdated.addListener(onUpdated);
    return () => {
      chrome.tabs.onActivated.removeListener(onActivated);
      chrome.tabs.onUpdated.removeListener(onUpdated);
    };
  }, []);

  // 检查登录状态
  useEffect(() => {
    getCachedUser().then((u) => {
      setUser(u);
      setAuthLoading(false);
    });
  }, []);

  // 打开 side panel 时 + 登录后 拉一次余额
  useEffect(() => {
    if (!user?.wallet_address) {
      setBalance(null);
      return;
    }
    void refreshBalance(user.wallet_address);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.wallet_address]);

  async function refreshBalance(address: string) {
    setBalanceLoading(true);
    try {
      const b = await getUsdcBalance(address);
      setBalance(b);
    } catch (e) {
      console.warn("Failed to load USDC balance:", e);
      // 保留上一次成功的余额以免闪烁，但记下失败不再加 loading
    } finally {
      setBalanceLoading(false);
    }
  }

  /** Bump the version counter BorrowEntry watches, and refresh the wallet
   *  balance. Called whenever a lending tx broadcasts / confirms / fails so
   *  BorrowEntry refetches loans + positions and the header USDC updates. */
  function bumpLendingReload() {
    setLendingReloadVersion((v) => v + 1);
    if (user?.wallet_address) void refreshBalance(user.wallet_address);
  }

  /** Add a CTF to the optimistic-borrowed set (called when the borrow tx
   *  broadcasts, so BorrowEntry's CTA flips immediately without waiting for
   *  the backend's confirmBorrow → DB commit → next fetchLoans round-trip). */
  function addOptimisticBorrowedCtf(ctf: string) {
    setOptimisticBorrowedCtfs((prev) => {
      if (prev.has(ctf)) return prev;
      const next = new Set(prev);
      next.add(ctf);
      return next;
    });
  }

  /** BorrowEntry calls this with the active-loans list each time it
   *  successfully fetches loans. Any CTF that's now confirmed active in the
   *  real data can be dropped from the optimistic set — the live data takes
   *  over. */
  function reconcileOptimisticBorrowedCtfs(activeLoans: LoanItem[]) {
    if (activeLoans.length === 0) return;
    const realCtfs = new Set(activeLoans.map((l) => l.ctf_token_id));
    setOptimisticBorrowedCtfs((prev) => {
      if (prev.size === 0) return prev;
      const next = new Set(prev);
      for (const c of realCtfs) next.delete(c);
      return next.size === prev.size ? prev : next;
    });
  }

  function goToProfile() {
    setProfileReturnView(view === "profile" ? "idle" : view);
    setView("profile");
    if (user?.wallet_address) {
      void refreshBalance(user.wallet_address);
    }
  }

  function leaveProfile() {
    setView(profileReturnView);
  }

  async function handleSignIn() {
    setAuthLoading(true);
    setError(null);
    try {
      const u = await signIn();
      setUser(u);
      setError(null);
    } catch (e) {
      // If the user simply closed the auth window, don't surface it as an error
      // the next successful attempt will still show the wallet; silent abort.
      const msg = e instanceof Error ? e.message : "登录失败";
      if (msg !== "Login window closed" && msg !== "Login timed out") {
        setError(msg);
      }
    }
    setAuthLoading(false);
  }

  async function handleSignOut() {
    await signOut();
    setUser(null);
    setError(null);
  }

  // 应用主题到 DOM
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  // 打开时从 storage 读取初始状态，并验证当前页面是否仍是比赛页
  useEffect(() => {
    chrome.storage.local.get(["kpax_market", "kpax_theme"]).then((data) => {
      if (data.kpax_theme) setTheme(data.kpax_theme);

      // 检查当前 tab URL，决定是否使用缓存的 market
      chrome.tabs?.query({ active: true, currentWindow: true }, (tabs) => {
        const url = tabs[0]?.url || "";
        const isMatchPage = /polymarket\.com\/.*sports\/.*\d{4}-\d{2}-\d{2}/.test(url);

        if (isMatchPage && data.kpax_market) {
          setMarket(data.kpax_market);
          setView("match");
        } else if (!isMatchPage) {
          // 不在比赛页，清除旧缓存
          chrome.storage.local.remove("kpax_market");
        }

        // 请求 content script 报告主题和重新扫描
        if (tabs[0]?.id) {
          chrome.tabs.sendMessage(tabs[0].id, { type: "REQUEST_THEME" }).catch(() => {});
          chrome.tabs.sendMessage(tabs[0].id, { type: "REQUEST_RESCAN" }).catch(() => {});
        }
      });
    });
  }, []);

  // 监听 Content Script 消息
  useEffect(() => {
    const listener = (message: any) => {
      if (message.type === "MARKET_DETECTED" && message.market) {
        setView((prev) => {
          const inAnalysis = prev !== "idle" && prev !== "match";
          if (inAnalysis) {
            // 分析中：不打断，暂存新比赛
            setMarket((curMarket) => {
              const isNew = !curMarket || curMarket.slug !== message.market.slug ||
                curMarket.homeTeam !== message.market.homeTeam;
              if (isNew) setPendingMarket(message.market);
              return curMarket; // 不更新当前 market
            });
          } else {
            // idle/match：直接切换
            setMarket(message.market);
            setPendingMarket(null);
            setError(null);
            return "match";
          }
          return prev;
        });
      }
      if (message.type === "MARKET_LEFT") {
        setView((prev) => {
          if (prev === "match" || prev === "idle") return "idle";
          return prev;
        });
      }
      if (message.type === "THEME_CHANGED" && message.theme) {
        setTheme(message.theme);
      }
    };

    chrome.runtime.onMessage.addListener(listener);

    // 同时监听 storage 变化（更可靠，不依赖 sendMessage 到达）
    const storageListener = (changes: { [key: string]: chrome.storage.StorageChange }) => {
      if (changes.kpax_theme?.newValue) {
        setTheme(changes.kpax_theme.newValue);
      }
    };
    chrome.storage.onChanged.addListener(storageListener);

    return () => {
      chrome.runtime.onMessage.removeListener(listener);
      chrome.storage.onChanged.removeListener(storageListener);
    };
  }, []);

  // 清理 stream
  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  // ---- 操作 ----

  async function handleQuickAnalysis() {
    if (!market) return;
    setView("loading_quick");
    setError(null);
    setPreview(null);

    try {
      const p = await getPreview(market.slug, market.polymarketOdds, lang, market.homeTeam, market.awayTeam, market.competition);
      setPreview(p);
      setView("preview");
    } catch {
      setError(t(lang, "backendError"));
      setView("match");
    }
  }

  function handleDeepAnalysis(userContext?: string) {
    if (!market) return;
    setView("loading_deep");
    setDebateMessages([]);
    setReport(null);
    setFollowUpQA([]);
    setIsStreaming(true);
    setStatusMessage(t(lang, "loadingDeep"));
    setError(null);

    abortRef.current = streamDeepAnalysis(
      market.slug,
      market.polymarketOdds,
      userContext,
      // callbacks below, language at the end
      (msg) => {
        if (msg.type === "status") {
          setStatusMessage(typeof msg.content === "string" ? msg.content : "");
          // 第一条 expert 消息时切换到 deep 视图
        } else if (msg.type === "expert" || msg.type === "moderator") {
          setView("deep");
          setDebateMessages((prev) => [...prev, msg]);
        } else if (msg.type === "report") {
          setReport(msg.content as FullReport);
          setView("report");
          setIsStreaming(false);
        } else if (msg.type === "error") {
          setError(typeof msg.content === "string" ? msg.content : t(lang, "error"));
          setIsStreaming(false);
          setView("match");
        }
      },
      () => setIsStreaming(false),
      (err) => {
        setError(err.message);
        setIsStreaming(false);
        setView("match");
      },
      lang,
      market.homeTeam,
      market.awayTeam,
      market.competition,
    );
  }

  function handleFollowUp(question: string) {
    console.log("[FollowUp] triggered:", question, "market:", !!market, "report:", !!report);
    if (!market || !report) return;
    console.log("[FollowUp] sending request...");
    setIsStreaming(true);
    setError(null);

    const msgs = debateMessages.map((m) => ({
      expert: (m as any).expert || "unknown",
      role: (m as any).role || "",
      content: typeof m.content === "string" ? m.content : "",
      round: (m as any).round || 1,
    }));

    abortRef.current = streamFollowUp(
      market.slug,
      question,
      market.polymarketOdds as Record<string, number>,
      msgs,
      report as unknown as Record<string, unknown>,
      (msg) => {
        if (msg.type === "followup_answer") {
          const answer = typeof msg.content === "string" ? msg.content : "";
          setFollowUpQA((prev) => [...prev, { question, answer }]);
          setIsStreaming(false);
        } else if (msg.type === "error") {
          setError(typeof msg.content === "string" ? msg.content : t(lang, "error"));
          setIsStreaming(false);
        }
      },
      () => setIsStreaming(false),
      (err) => {
        setError(err.message);
        setIsStreaming(false);
      },
      lang,
    );
  }

  function switchToPendingMarket() {
    if (!pendingMarket) return;
    abortRef.current?.abort();
    setMarket(pendingMarket);
    setPendingMarket(null);
    setPreview(null);
    setDebateMessages([]);
    setReport(null);
    setFollowUpQA([]);
    setIsStreaming(false);
    setError(null);
    setView("match");
  }

  /** 返回球赛：有 pending 新比赛则切换，否则回到当前比赛 */
  /** 重新读取当前页面的比赛信息 */
  function handleRefreshMarket() {
    chrome.tabs?.query({ active: true, currentWindow: true }, (tabs) => {
      const url = tabs[0]?.url || "";
      // 检查 URL 是否是具体比赛页（含日期）
      const isMatchPage = /polymarket\.com\/.*sports\/.*\d{4}-\d{2}-\d{2}/.test(url);
      if (!isMatchPage) {
        // 不是比赛页，清除旧数据回到 idle
        setMarket(null);
        setPreview(null);
        setReport(null);
        setDebateMessages([]);
        setFollowUpQA([]);
        setPendingMarket(null);
        setView("idle");
        chrome.storage.local.remove("kpax_market");
        return;
      }
      // 是比赛页，通知 content script 重新扫描
      if (tabs[0]?.id) {
        chrome.tabs.sendMessage(tabs[0].id, { type: "REQUEST_RESCAN" }).catch(() => {});
      }
    });
  }

  function goBackToMatch() {
    if (pendingMarket) {
      switchToPendingMarket();
    } else {
      setView("match");
    }
  }

  function toggleLang() {
    setLang((prev) => (prev === "zh" ? "en" : "zh"));
  }

  // ---- 渲染 ----

  // 顶部栏
  const header = (
    <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800">
      {/* Brand wordmark — emerald→cyan gradient is the KPAX signature.
          Same gradient is reused on tab indicator + primary CTAs so users
          consistently associate the color with the product. */}
      <span className="text-sm font-bold bg-clip-text text-transparent bg-gradient-to-r from-emerald-400 to-cyan-400">
        KPAX Ball
      </span>
      <div className="flex items-center gap-2">
        {user && (
          <button
            onClick={goToProfile}
            className="flex items-center gap-1.5 rounded-md border border-slate-700/60 px-1.5 py-0.5 hover:border-cyan-400/60 hover:bg-cyan-500/5 transition-colors"
            title={user.email || user.wallet_address}
          >
            <span className="text-[11px] font-semibold text-slate-100">
              {balance === null && balanceLoading ? (
                <span className="inline-block h-2.5 w-8 rounded bg-slate-700/70 animate-pulse" />
              ) : (
                <>$<span>{formatUsdc(balance?.amount ?? 0)}</span></>
              )}
            </span>
            <span className="text-[9px] text-slate-400 uppercase tracking-wider">
              USDC.e
            </span>
            <span className="text-[10px] font-mono text-slate-500">
              {shortAddress(user.wallet_address)}
            </span>
          </button>
        )}
        <button
          onClick={toggleLang}
          className="text-[10px] text-slate-500 hover:text-slate-300 border border-slate-700 rounded px-1.5 py-0.5"
        >
          {t(lang, "langSwitch")}
        </button>
      </div>
    </div>
  );

  const pendingBanner = pendingMarket && (
    <button
      onClick={switchToPendingMarket}
      className="flex w-full items-center gap-2 bg-blue-600/20 px-4 py-2 text-left text-xs text-blue-400 hover:bg-blue-600/30 transition-colors"
    >
      <span className="shrink-0">&#9918;</span>
      <span className="flex-1 truncate">
        {lang === "zh" ? "新比赛：" : "New match: "}
        {pendingMarket.homeTeam} vs {pendingMarket.awayTeam}
      </span>
      <span className="shrink-0 text-[10px] text-blue-500">
        {lang === "zh" ? "点击切换 →" : "Switch →"}
      </span>
    </button>
  );

  // Lending dialogs render at App level and are added to every view branch
  // that the user might transition to mid-flow (match / preview / loading_*
  // / deep / report / off-polymarket placeholder / idle). Keeping them out
  // of BorrowEntry means they survive view churn — including the case where
  // content script briefly emits MARKET_LEFT during MM popup wait.
  const lendingDialogs = user ? (
    <>
      {borrowSession && (
        <BorrowFlowDialog
          proxy={borrowSession.proxy}
          position={borrowSession.position}
          lang={lang}
          expectedEoa={user.wallet_address}
          onSettled={(borrowed) => {
            addOptimisticBorrowedCtf(borrowed.ctfTokenId);
            bumpLendingReload();
          }}
          onClose={() => {
            setBorrowSession(null);
            bumpLendingReload();
          }}
        />
      )}
      {repayingLoan && (
        <RepayModal
          loan={repayingLoan}
          lang={lang}
          expectedEoa={user.wallet_address}
          onClose={() => {
            setRepayingLoan(null);
            bumpLendingReload();
          }}
          onRepaid={() => {
            bumpLendingReload();
          }}
        />
      )}
    </>
  ) : null;

  // ---- 视图主体 ----
  // Wrap the whole view dispatch in an IIFE so all `return (...)`s become
  // values assigned to `body`. The single bottom return then renders
  // `{body}{lendingDialogs}` together — keeping the dialogs at one
  // INVARIANT position in the React tree across every view change. Without
  // this, view → view transitions remount the modals (state lost mid-flow:
  // user signs both txes in MM but the dialog reverts to the initial
  // "ready" / "approved" state because a fresh prepareRepay/approval-check
  // ran on remount).
  const body = (() => {
  // ---- 登录门控 ----
  if (authLoading) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        <div className="flex flex-1 items-center justify-center">
          <div className="h-6 w-6 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        <div className="flex flex-1 items-center justify-center p-6">
          <div className="text-center">
            <div className="mb-4 text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-br from-emerald-400 to-cyan-400">
              K
            </div>
            <h2 className="mb-2 text-lg font-semibold text-white">KPAX Ball</h2>
            <p className="mb-6 text-xs text-slate-400">
              {lang === "zh"
                ? "AI 足球分析工具，连接钱包后即可使用"
                : "AI football analysis tool. Connect a wallet to get started."}
            </p>
            <button
              onClick={handleSignIn}
              className="flex items-center gap-2 mx-auto rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 hover:shadow-lg hover:shadow-cyan-500/30 px-5 py-2.5 text-sm font-semibold text-white transition-all"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 12V7H5a2 2 0 0 1 0-4h14v4" />
                <path d="M3 5v14a2 2 0 0 0 2 2h16v-5" />
                <path d="M18 12a2 2 0 0 0 0 4h4v-4Z" />
              </svg>
              {lang === "zh" ? "连接钱包登录" : "Connect wallet"}
            </button>
            {error && (
              <p className="mt-3 text-xs text-red-400">{error}</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Off-Polymarket gate: when the active tab isn't on polymarket.com, render
  // a minimal placeholder so the panel doesn't flash stale match data while
  // the user reads other sites. (Profile is exempted — user can manage their
  // wallet from any page.)
  if (!activeTabIsPolymarket && view !== "profile") {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        <div className="flex flex-1 items-center justify-center p-6">
          <div className="text-center text-slate-500 max-w-[260px]">
            <div className="mb-3 text-3xl">🌐</div>
            <p className="text-xs leading-relaxed">
              {lang === "zh"
                ? "切到 polymarket.com 比赛页可继续使用 KPAX。"
                : "Switch to a polymarket.com match page to continue using KPAX."}
            </p>
          </div>
        </div>
      </div>
    );
  }

  // profile page — 接管全部视图，不显示原 header
  if (view === "profile") {
    return (
      <>
        <Profile
          user={user}
          balance={balance}
          balanceLoading={balanceLoading}
          lang={lang}
          onBack={leaveProfile}
          onRefresh={() =>
            user?.wallet_address && refreshBalance(user.wallet_address)
          }
          onSignOut={async () => {
            await handleSignOut();
            setView("idle");
          }}
          optimisticBorrowedCtfs={optimisticBorrowedCtfs}
          lendingReloadVersion={lendingReloadVersion}
          onLoansLoaded={reconcileOptimisticBorrowedCtfs}
          onStartBorrow={(session) => setBorrowSession(session)}
          onStartRepay={(loan) => setRepayingLoan(loan)}
        />
      </>
    );
  }

  // idle
  if (view === "idle") {
    const guideSrc = chrome.runtime.getURL(
      lang === "zh" ? "cn-guide.jpg" : "en-guide.jpg",
    );
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex flex-1 overflow-y-auto p-6">
          <div className="m-auto w-full text-center space-y-5">
            <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/40 bg-amber-500/10 px-4 py-1.5 text-sm font-semibold text-amber-300">
              <span className="text-base">⚽</span>
              <span>
                {lang === "zh"
                  ? "当前仅支持英超 (EPL)"
                  : "Premier League (EPL) only"}
              </span>
            </div>

            <p className="text-lg font-semibold text-slate-100">
              {lang === "zh"
                ? "请点击「比赛视图」加载分析"
                : "Tap \"Game View\" to load analysis"}
            </p>

            <div className="overflow-hidden rounded-xl bg-white p-1.5 shadow-lg ring-1 ring-slate-800">
              <img
                src={guideSrc}
                alt={lang === "zh" ? "比赛视图引导" : "Game View guide"}
                className="block h-auto w-full rounded-lg"
                loading="eager"
              />
            </div>

            <p className="text-xs text-slate-500">
              {lang === "zh"
                ? "进入具体比赛页面后，KPAX 将自动识别并显示分析"
                : "Once on a match page, KPAX auto-detects and shows analysis"}
            </p>
          </div>
        </div>
      </div>
    );
  }

  // match — 显示球赛信息 + 按钮
  if (view === "match" && market) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex-1 overflow-y-auto p-4">
          <MatchCard
            market={market}
            lang={lang}
            onQuickAnalysis={handleQuickAnalysis}
            onDeepAnalysis={() => handleDeepAnalysis()}
            isLoading={false}
          />
          <BorrowEntry
            market={market}
            lang={lang}
            expectedEoa={user.wallet_address}
            optimisticBorrowedCtfs={optimisticBorrowedCtfs}
            lendingReloadVersion={lendingReloadVersion}
            onLoansLoaded={reconcileOptimisticBorrowedCtfs}
            onStartBorrow={(session) => setBorrowSession(session)}
            onStartRepay={(loan) => setRepayingLoan(loan)}
          />
          {error && (
            <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">
              {error}
            </div>
          )}
        </div>
      </div>
    );
  }

  // loading_quick — 简要分析加载中
  if (view === "loading_quick") {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex-1 overflow-y-auto p-4">
          {market && (
            <MatchCard
              market={market}
              lang={lang}
              onQuickAnalysis={handleQuickAnalysis}
              onDeepAnalysis={() => handleDeepAnalysis()}
              isLoading={true}
            />
          )}
          <Loading lang={lang} message={t(lang, "loadingPreview")} />
        </div>
      </div>
    );
  }

  // preview — 简要分析结果
  if (view === "preview" && preview && market) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex-1 overflow-y-auto p-4">
          <QuickPreviewCard
            market={market}
            preview={preview}
            lang={lang}
            onDeepAnalysis={() => handleDeepAnalysis()}
          />
          {/* 知识图谱 */}
          <div className="mt-4">
            <KnowledgeGraph lang={lang} />
          </div>

          <button
            onClick={goBackToMatch}
            className="mt-3 text-xs text-slate-500 hover:text-slate-300"
          >
            &larr; {t(lang, "backToMatch")}
          </button>
        </div>
      </div>
    );
  }

  // loading_deep — 详细分析加载中
  if (view === "loading_deep" && market) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mb-4 rounded-lg border border-slate-700 bg-slate-900 p-3">
            <div className="text-xs text-slate-500">{market.competition}</div>
            <h2 className="text-sm font-semibold text-white">
              {market.homeTeam} vs {market.awayTeam}
            </h2>
          </div>
          <Loading lang={lang} message={statusMessage || t(lang, "loadingDeep")} />
        </div>
      </div>
    );
  }

  // deep — 专家辩论流
  if (view === "deep" && market) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mb-4 rounded-lg border border-slate-700 bg-slate-900 p-3">
            <div className="text-xs text-slate-500">{market.competition}</div>
            <h2 className="text-sm font-semibold text-white">
              {market.homeTeam} vs {market.awayTeam}
            </h2>
            {statusMessage && (
              <div className="mt-2 flex items-center gap-2 text-xs text-blue-400">
                <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
                {statusMessage}
              </div>
            )}
          </div>
          <ExpertDebate messages={debateMessages} isStreaming={isStreaming} lang={lang} />
          {!isStreaming && (
            <button
              onClick={goBackToMatch}
              className="mt-4 text-xs text-slate-500 hover:text-slate-300"
            >
              &larr; {t(lang, "backToMatch")}
            </button>
          )}
        </div>
      </div>
    );
  }

  // report — 最终报告
  if (view === "report" && report && market) {
    return (
      <div className="flex h-screen flex-col bg-slate-950">
        {header}
        {pendingBanner}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mb-4">
            <div className="text-xs text-slate-500">{market.competition}</div>
            <h2 className="text-lg font-semibold text-white">
              {market.homeTeam} vs {market.awayTeam}
            </h2>
          </div>
          <FullReportView
            report={report}
            polymarketOdds={market.polymarketOdds as Record<string, number>}
            lang={lang}
          />
          {/* 知识图谱 */}
          <div className="mt-4">
            <KnowledgeGraph lang={lang} />
          </div>

          {followUpQA.length > 0 && (
            <div className="mt-4 space-y-3">
              {followUpQA.map((qa, i) => (
                <div key={i} className="rounded-lg border border-slate-700 bg-slate-900/50 p-3">
                  <div className="mb-2 text-xs font-medium text-blue-400">Q: {qa.question}</div>
                  <div className="whitespace-pre-wrap text-xs leading-relaxed text-slate-300">{qa.answer}</div>
                </div>
              ))}
            </div>
          )}
          <FollowUp
            onSubmit={(msg) => handleFollowUp(msg)}
            isLoading={isStreaming}
            lang={lang}
            homeTeam={market.homeTeam}
            awayTeam={market.awayTeam}
            report={report}
          />
          {error && (
            <div className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-400">
              {error}
            </div>
          )}
          <div className="mt-4 flex gap-3">
            <button
              onClick={goBackToMatch}
              className="text-xs text-slate-500 hover:text-slate-300"
            >
              &larr; {t(lang, "backToMatch")}
            </button>
            {debateMessages.length > 0 && (
              <button
                onClick={() => setView("deep")}
                className="text-xs text-slate-500 hover:text-slate-300"
              >
                {t(lang, "viewDebate")}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // fallback
  return (
    <div className="flex h-screen flex-col bg-slate-950">
      {header}
      <div className="flex flex-1 items-center justify-center p-6 text-slate-400">
        <div className="text-center">
          <div className="mb-3 text-4xl font-bold bg-clip-text text-transparent bg-gradient-to-br from-emerald-400 to-cyan-400">
            K
          </div>
          <p className="text-sm">{t(lang, "idle")}</p>
          {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
        </div>
      </div>
    </div>
  );
  })();

  return (
    <>
      {body}
      {lendingDialogs}
    </>
  );
}
