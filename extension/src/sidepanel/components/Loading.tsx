import type { Lang } from "@shared/types";
import { t } from "@shared/i18n";

interface Props {
  lang: Lang;
  message?: string;
}

export default function Loading({ lang, message }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-12">
      {/* 旋转动画 */}
      <div className="mb-4 h-8 w-8 animate-spin rounded-full border-3 border-blue-500 border-t-transparent" />
      <p className="text-sm text-slate-400">
        {message || t(lang, "loading")}
      </p>
    </div>
  );
}
