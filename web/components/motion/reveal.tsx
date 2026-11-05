"use client";

import { useRef, type ReactNode } from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";

gsap.registerPlugin(useGSAP);

/**
 * Page entrance: every descendant marked `data-animate` rises in with a short stagger.
 * Elements start hidden in CSS (globals.css) so nothing flashes before GSAP runs;
 * reduced-motion users get the final state immediately.
 */
export function Reveal({ children, className, stagger = 0.05 }: { children: ReactNode; className?: string; stagger?: number }) {
  const scope = useRef<HTMLDivElement>(null);
  useGSAP(
    () => {
      const items = gsap.utils.toArray<HTMLElement>("[data-animate]");
      const mm = gsap.matchMedia();
      mm.add("(prefers-reduced-motion: no-preference)", () => {
        gsap.fromTo(
          items,
          { autoAlpha: 0, y: 12, filter: "blur(3px)" },
          { autoAlpha: 1, y: 0, filter: "blur(0px)", duration: 0.5, ease: "power3.out", stagger, clearProps: "filter,transform" },
        );
      });
      mm.add("(prefers-reduced-motion: reduce)", () => gsap.set(items, { autoAlpha: 1 }));
      return () => mm.revert();
    },
    { scope },
  );
  return (
    <div ref={scope} className={className}>
      {children}
    </div>
  );
}

/** Horizontal bar that grows to `pct` once mounted (GSAP), used by the funnel and metric bars. */
export function GrowBar({ pct, className, delay = 0 }: { pct: number; className?: string; delay?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useGSAP(() => {
    gsap.fromTo(ref.current, { width: "0%" }, { width: `${Math.max(0, Math.min(100, pct))}%`, duration: 1.1, delay, ease: "power3.out" });
  }, { dependencies: [pct] });
  return <div ref={ref} className={className} style={{ width: 0 }} />;
}
