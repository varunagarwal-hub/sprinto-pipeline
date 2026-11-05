"use client";

import { useRef, type RefObject } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { Archive, BrainCircuit, Database, Radio, Search, Send } from "lucide-react";
import { AnimatedBeam } from "@/components/ui/animated-beam";
import { cn } from "@/lib/utils";

gsap.registerPlugin(useGSAP);

export type LoopStage = { key: string; label: string; value: number; detail: string; live: boolean };

const ICONS: Record<string, typeof Send> = {
  act: Send, signal: Radio, capture: Archive, store: Database, retrieve: Search, apply: BrainCircuit,
};

function Node({ stage, nodeRef, index }: { stage: LoopStage; nodeRef: RefObject<HTMLDivElement | null>; index: number }) {
  const Icon = ICONS[stage.key];
  return (
    <div ref={nodeRef} data-node
      className={cn(
        "relative z-10 w-full max-w-[220px] rounded-xl border bg-card p-4 shadow-sm transition-colors",
        stage.live ? "border-primary/25" : "border-dashed border-warning/60",
      )}>
      <div className="mb-3 flex items-center justify-between">
        <div className={cn("grid size-9 place-items-center rounded-lg",
          stage.live ? "bg-primary/10 text-primary" : "bg-warning/15 text-warning")}>
          <Icon className="size-4" />
        </div>
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          {String(index + 1).padStart(2, "0")}
        </span>
      </div>
      <div className="text-sm font-semibold">{stage.label}</div>
      <div className="tabular mt-0.5 text-2xl font-semibold tracking-tight">{stage.value}</div>
      <div className="mt-1 text-xs leading-snug text-muted-foreground">{stage.detail}</div>
      {!stage.live && <div className="mt-2 text-[11px] font-medium text-warning">Wired, waiting for data</div>}
    </div>
  );
}

export function LoopDiagram({ stages }: { stages: LoopStage[] }) {
  const container = useRef<HTMLDivElement>(null);
  const refs = [useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null),
                useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null), useRef<HTMLDivElement>(null)];
  // grid order: top row Act Signal Capture, bottom row Apply Retrieve Store (so the loop reads clockwise)
  const order = [0, 1, 2, 5, 4, 3];

  useGSAP(() => {
    gsap.from("[data-node]", { autoAlpha: 0, scale: 0.94, y: 10, duration: 0.6, ease: "back.out(1.6)", stagger: 0.09 });
  }, { scope: container });

  const beam = "color-mix(in oklch, var(--primary) 80%, transparent)";
  return (
    <div ref={container} className="relative grid grid-cols-3 place-items-center gap-x-6 gap-y-14 px-2 py-6 sm:gap-x-16">
      {order.map((i) => (
        <Node key={stages[i].key} stage={stages[i]} nodeRef={refs[i]} index={i} />
      ))}
      {stages.map((s, i) => {
        const next = (i + 1) % stages.length;
        const vertical = i === 2 || i === 5;          // Capture -> Store and Apply -> Act
        const leftward = i === 3 || i === 4;          // bottom row runs right-to-left
        return (
          <AnimatedBeam key={s.key} containerRef={container} fromRef={refs[i]} toRef={refs[next]}
            curvature={vertical ? (i === 2 ? -60 : 60) : 0} reverse={leftward} duration={4} delay={i * 0.35}
            pathColor="var(--border)" pathWidth={2} pathOpacity={0.9}
            gradientStartColor={s.live && stages[next].live ? beam : "var(--warning)"}
            gradientStopColor={s.live && stages[next].live ? "var(--brand-2)" : "var(--warning)"} />
        );
      })}
    </div>
  );
}
