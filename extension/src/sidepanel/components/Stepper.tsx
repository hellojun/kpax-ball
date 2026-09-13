import { Fragment } from "react";
import { Check } from "lucide-react";

/** Horizontal step indicator used by the borrow + repay flows.
 *
 *  Each step has a stable identity (label) and a state — `done` (✓ on emerald),
 *  `current` (number on blue + ring), `upcoming` (number on slate). All
 *  state-dependent classes go through transition-colors so a step flipping
 *  from current → done eases between palettes rather than snapping.
 *
 *  Skipped steps (e.g. USDC approval already granted) should be omitted from
 *  the steps array entirely — the parent owns that decision. */

export interface StepperStep {
  label: string;
  state: "done" | "current" | "upcoming";
}

interface Props {
  steps: StepperStep[];
}

export default function Stepper({ steps }: Props) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      {steps.map((step, i) => (
        <Fragment key={i}>
          <Pill step={step} index={i + 1} />
          {/* Connector tints emerald once the preceding step is done — gives
              the row a subtle "progress" gradient as the user advances. */}
          {i < steps.length - 1 && (
            <div
              className={
                "flex-1 h-px transition-colors duration-500 " +
                (step.state === "done" ? "bg-emerald-500/40" : "bg-slate-700")
              }
            />
          )}
        </Fragment>
      ))}
    </div>
  );
}

function Pill({ step, index }: { step: StepperStep; index: number }) {
  const dotCls =
    step.state === "done"
      ? "bg-emerald-500 text-white shadow-sm shadow-emerald-500/30"
      : step.state === "current"
        ? "bg-gradient-to-br from-emerald-500 to-cyan-500 text-white ring-2 ring-cyan-500/30 shadow-sm shadow-cyan-500/30"
        : "border border-slate-700 text-slate-500";
  const labelCls =
    step.state === "done"
      ? "text-emerald-300"
      : step.state === "current"
        ? "text-cyan-300 font-semibold"
        : "text-slate-500";

  return (
    <div className="flex items-center gap-1.5">
      <span
        className={
          "flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold transition-all duration-300 " +
          dotCls
        }
      >
        {step.state === "done" ? (
          <Check size={12} strokeWidth={3} />
        ) : (
          index
        )}
      </span>
      <span className={"transition-colors duration-300 " + labelCls}>
        {step.label}
      </span>
    </div>
  );
}
