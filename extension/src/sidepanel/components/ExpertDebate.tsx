import type { AnalysisMessage, Lang } from "@shared/types";
import { t } from "@shared/i18n";
import React from "react";

interface Props {
  messages: AnalysisMessage[];
  isStreaming: boolean;
  lang: Lang;
}

const EXPERT_COLORS: Record<string, string> = {
  "Tactical Network Analyst": "border-blue-500 bg-blue-500/10",
  "Statistical Modeler": "border-purple-500 bg-purple-500/10",
  "Tactical Interpreter": "border-emerald-500 bg-emerald-500/10",
  "Psychological Context Analyst": "border-amber-500 bg-amber-500/10",
  Moderator: "border-slate-400 bg-slate-400/10",
};

const EXPERT_INITIALS: Record<string, string> = {
  "Tactical Network Analyst": "TN",
  "Statistical Modeler": "SM",
  "Tactical Interpreter": "TI",
  "Psychological Context Analyst": "PC",
  Moderator: "M",
};

export default function ExpertDebate({ messages, isStreaming, lang }: Props) {
  const expertMessages = messages.filter(
    (m) => m.type === "expert" || m.type === "moderator"
  );

  if (expertMessages.length === 0 && isStreaming) {
    return (
      <div className="flex items-center gap-2 py-4 text-sm text-slate-400">
        <div className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
        {t(lang, "expertsAnalyzing")}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="text-xs font-medium text-slate-400">{t(lang, "expertDebate")}</div>
      {expertMessages.map((msg, i) => {
        const expert = msg.expert || "Unknown";
        const colors = EXPERT_COLORS[expert] || "border-slate-600 bg-slate-600/10";
        const initials = EXPERT_INITIALS[expert] || "?";
        const content = typeof msg.content === "string" ? msg.content : "";

        return (
          <div key={i} className={`rounded-lg border-l-2 p-3 ${colors}`}>
            <div className="mb-1.5 flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-700 text-[10px] font-bold text-white">
                {initials}
              </span>
              <span className="text-xs font-medium text-slate-300">{expert}</span>
              {msg.round && (
                <span className="text-[10px] text-slate-600">
                  {t(lang, "round").replace("{n}", String(msg.round))}
                </span>
              )}
            </div>
            <div className="prose-mini text-xs leading-relaxed text-slate-400">
              <MarkdownContent text={content} />
            </div>
          </div>
        );
      })}
      {isStreaming && (
        <div className="flex items-center gap-2 py-2 text-xs text-slate-500">
          <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-500" />
          {t(lang, "waitingNextExpert")}
        </div>
      )}
    </div>
  );
}

/** 轻量 Markdown 渲染（纯 React 组件，安全无 innerHTML） */
function MarkdownContent({ text }: { text: string }) {
  const lines = text.split("\n");
  const elems: React.ReactNode[] = [];
  let listItems: string[] = [];
  let k = 0;

  function flushList() {
    if (listItems.length > 0) {
      elems.push(
        <ul key={k++} className="my-1 ml-4 list-disc space-y-0.5">
          {listItems.map((item, i) => <li key={i}>{inlineFmt(item)}</li>)}
        </ul>
      );
      listItems = [];
    }
  }

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushList(); continue; }
    const lm = line.match(/^[-*]\s+(.+)/);
    if (lm) { listItems.push(lm[1]); continue; }
    flushList();
    if (line.startsWith("### "))
      elems.push(<h4 key={k++} className="mt-2 mb-1 text-xs font-semibold text-slate-300">{inlineFmt(line.slice(4))}</h4>);
    else if (line.startsWith("## "))
      elems.push(<h3 key={k++} className="mt-2 mb-1 text-xs font-semibold text-slate-200">{inlineFmt(line.slice(3))}</h3>);
    else
      elems.push(<p key={k++} className="my-0.5">{inlineFmt(line)}</p>);
  }
  flushList();
  return <>{elems}</>;
}

/** 行内格式：**bold** *italic* */
function inlineFmt(text: string): React.ReactNode {
  const parts: React.ReactNode[] = [];
  const re = /(\*\*(.+?)\*\*|\*(.+?)\*)/g;
  let last = 0, m: ReturnType<typeof re.exec>, i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    if (m[2]) parts.push(<strong key={i++} className="font-semibold text-slate-200">{m[2]}</strong>);
    else if (m[3]) parts.push(<em key={i++} className="italic text-slate-300">{m[3]}</em>);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts.length <= 1 ? parts[0] || text : <>{parts}</>;
}
