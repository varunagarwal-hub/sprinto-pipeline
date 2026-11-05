import type { LoopStage } from "@/components/loop-diagram";

type Counts = {
  drafts: number; sends: number; signals: number; human_decisions: number; captured: number; unprocessed: number;
  stored: number; retrievals: number; applied_drafts: number;
};

/** The six stages from the "Act → Signal → Capture → Store → Retrieve → Apply" model, with live counts. */
export function loopStages(s: Counts): LoopStage[] {
  return [
    { key: "act", label: "Act", value: s.drafts, detail: `${s.drafts} drafts written, ${s.sends} sent`, live: s.drafts > 0 },
    { key: "signal", label: "Signal", value: s.signals + s.human_decisions,
      detail: "Reviewer edits, ratings, overrides; prospect replies", live: s.signals + s.human_decisions > 0 },
    { key: "capture", label: "Capture", value: s.captured, detail: `${s.unprocessed} not yet distilled`, live: s.captured > 0 },
    { key: "store", label: "Store", value: s.stored, detail: "Active learnings in memory", live: s.stored > 0 },
    { key: "retrieve", label: "Retrieve", value: s.retrievals, detail: "Times a learning was looked up for a draft", live: s.retrievals > 0 },
    { key: "apply", label: "Apply", value: s.applied_drafts, detail: "Drafts shaped by a learning", live: s.applied_drafts > 0 },
  ];
}
