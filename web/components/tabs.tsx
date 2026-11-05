"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import gsap from "gsap";
import { cn } from "@/lib/utils";

/** Lightweight tabs: GSAP slides the underline and fades the panel in. */
export function Tabs({ tabs }: { tabs: { id: string; label: ReactNode; content: ReactNode }[] }) {
  const [active, setActive] = useState(tabs[0]?.id);
  const list = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = list.current?.querySelector<HTMLElement>(`[data-tab="${active}"]`);
    if (el && bar.current) gsap.to(bar.current, { x: el.offsetLeft, width: el.offsetWidth, duration: 0.35, ease: "power3.out" });
    if (panel.current) gsap.fromTo(panel.current, { autoAlpha: 0, y: 6 }, { autoAlpha: 1, y: 0, duration: 0.3, ease: "power2.out" });
  }, [active]);

  return (
    <div data-animate>
      <div ref={list} role="tablist" className="relative mb-5 flex gap-1 overflow-x-auto border-b">
        {tabs.map((t) => (
          <button key={t.id} role="tab" data-tab={t.id} aria-selected={t.id === active} onClick={() => setActive(t.id)}
            className={cn("whitespace-nowrap px-3 pb-2.5 pt-1 text-sm transition-colors",
              t.id === active ? "font-medium text-foreground" : "text-muted-foreground hover:text-foreground")}>
            {t.label}
          </button>
        ))}
        <div ref={bar} className="absolute bottom-[-1px] left-0 h-0.5 rounded-full bg-primary" style={{ width: 0 }} />
      </div>
      <div ref={panel} role="tabpanel">{tabs.find((t) => t.id === active)?.content}</div>
    </div>
  );
}
