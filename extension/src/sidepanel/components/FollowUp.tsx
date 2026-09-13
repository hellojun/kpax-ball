import { useState } from "react";
import type { FullReport, Lang } from "@shared/types";
import { t } from "@shared/i18n";

interface Props {
  onSubmit: (message: string) => void;
  isLoading: boolean;
  lang: Lang;
  homeTeam?: string;
  awayTeam?: string;
  report?: FullReport | null;
}

export default function FollowUp({ onSubmit, isLoading, lang, homeTeam, awayTeam, report }: Props) {
  const [message, setMessage] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!message.trim() || isLoading) return;
    onSubmit(message.trim());
    setMessage("");
  };

  const placeholder = lang === "zh" ? "输入你感兴趣的问题..." : "Ask anything about this match...";
  const suggestions = generateSuggestions(report, homeTeam, awayTeam, lang);

  return (
    <form onSubmit={handleSubmit} className="mt-4">
      <div className="text-xs font-medium text-slate-400 mb-2">{t(lang, "askFollowUp")}</div>
      <div className="flex gap-2">
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={placeholder}
          disabled={isLoading}
          className="flex-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-white placeholder-slate-600 focus:border-blue-500 focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={!message.trim() || isLoading}
          className="rounded-lg bg-gradient-to-r from-emerald-500 to-cyan-500 px-3 py-2 text-xs font-semibold text-white transition-all hover:shadow-md hover:shadow-cyan-500/30 disabled:bg-none disabled:bg-slate-700 disabled:text-slate-500 disabled:shadow-none disabled:cursor-not-allowed"
        >
          {isLoading ? "..." : t(lang, "followUp")}
        </button>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {suggestions.map((s, i) => (
          <button key={i} type="button" onClick={() => onSubmit(s)} disabled={isLoading}
            className="rounded-full border border-slate-700 px-2 py-0.5 text-[10px] text-slate-500 transition-colors hover:border-blue-500/50 hover:text-blue-400 disabled:opacity-50">
            {s}
          </button>
        ))}
      </div>
    </form>
  );
}

/** 从报告内容中生成球迷可能感兴趣的追问 */
function generateSuggestions(
  report: FullReport | null | undefined,
  homeTeam: string | undefined,
  awayTeam: string | undefined,
  lang: Lang,
): string[] {
  const suggestions: string[] = [];
  const zh = lang === "zh";

  if (!report) {
    return zh
      ? ["关键伤病影响？", "主场优势有多大？", "历史交锋战绩？"]
      : ["Key injury impact?", "Home advantage?", "H2H record?"];
  }

  // 1. 从关键变量中提取（这些是"可能翻转结果"的因素）
  const kv = report.keyVariables || [];
  for (const v of kv.slice(0, 2)) {
    const desc = v.description || "";
    if (desc.length > 3 && desc.length < 40) {
      suggestions.push(zh ? `如果${desc}会怎样？` : `What if ${desc}?`);
    }
  }

  // 2. 从专家分歧中提取
  const dis = report.disagreements || [];
  if (dis.length > 0 && dis[0].topic) {
    const topic = dis[0].topic;
    if (topic.length < 30) {
      suggestions.push(zh ? `${topic}的影响有多大？` : `How significant is ${topic}?`);
    }
  }

  // 3. 根据置信度补充
  const cj = report.coreJudgment;
  if (cj) {
    if (cj.confidence === "low") {
      suggestions.push(zh ? "为什么置信度这么低？" : "Why is confidence so low?");
    }
    // 找最大偏差方向
    const dev = cj.marketDeviation || {};
    const maxKey = Object.entries(dev).sort(([, a], [, b]) => Math.abs(b as number) - Math.abs(a as number))[0];
    if (maxKey && Math.abs(maxKey[1] as number) > 0.05) {
      const label = maxKey[0] === "home" ? homeTeam : maxKey[0] === "away" ? awayTeam : (zh ? "平局" : "draw");
      suggestions.push(zh ? `市场为什么高估/低估${label}？` : `Why does market misprice ${label}?`);
    }
  }

  // 保证至少3个，不够就补通用的
  if (suggestions.length < 3) {
    const fallbacks = zh
      ? [`${homeTeam || "主队"}近期状态如何？`, `${awayTeam || "客队"}的弱点在哪？`, "比赛节奏会怎样？"]
      : [`${homeTeam || "Home"} recent form?`, `${awayTeam || "Away"} weaknesses?`, "Match tempo?"];
    for (const f of fallbacks) {
      if (suggestions.length >= 3) break;
      if (!suggestions.includes(f)) suggestions.push(f);
    }
  }

  return suggestions.slice(0, 3);
}
