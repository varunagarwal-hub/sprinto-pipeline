"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLayoutEffect, useRef } from "react";
import gsap from "gsap";
import { Building2, Inbox, LayoutDashboard, MailCheck, RefreshCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "@/components/shell/theme-toggle";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard, key: null },
  { href: "/review", label: "Review queue", icon: Inbox, key: "review" },
  { href: "/drafts", label: "Drafts & sends", icon: MailCheck, key: "drafts" },
  { href: "/companies", label: "Companies", icon: Building2, key: null },
  { href: "/loop", label: "Feedback loop", icon: RefreshCcw, key: "loop" },
] as const;

export function Sidebar({ counts }: { counts: { review: number; drafts: number; loop: number } }) {
  const pathname = usePathname();
  const navRef = useRef<HTMLElement>(null);
  const pill = useRef<HTMLDivElement>(null);
  const active = NAV.findIndex((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)));

  // The active highlight glides between items instead of jumping.
  useLayoutEffect(() => {
    const el = navRef.current?.querySelectorAll<HTMLElement>("[data-nav]")[active];
    if (!el || !pill.current) return;
    gsap.to(pill.current, { y: el.offsetTop, height: el.offsetHeight, opacity: 1, duration: 0.45, ease: "power3.out" });
  }, [active]);

  return (
    <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar px-3 py-5 md:flex">
      <Link href="/" className="mb-8 flex items-center gap-2.5 px-2">
        <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-brand to-brand-2 text-sm font-semibold text-white shadow-sm">
          S
        </div>
        <div className="leading-tight">
          <div className="text-sm font-semibold tracking-tight">Sprinto Outreach</div>
          <div className="text-xs text-muted-foreground">Pipeline console</div>
        </div>
      </Link>

      <nav ref={navRef} className="relative flex flex-col gap-0.5">
        <div ref={pill} className="absolute inset-x-0 top-0 rounded-lg bg-sidebar-accent opacity-0" aria-hidden />
        {NAV.map((n, i) => {
          const Icon = n.icon;
          const count = n.key ? counts[n.key] : 0;
          return (
            <Link
              key={n.href}
              href={n.href}
              data-nav
              className={cn(
                "relative z-10 flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                i === active ? "font-medium text-sidebar-accent-foreground" : "text-sidebar-foreground/75 hover:text-sidebar-foreground",
              )}
            >
              <Icon className="size-4" />
              <span className="flex-1">{n.label}</span>
              {count > 0 && (
                <span className="tabular rounded-full bg-primary/10 px-1.5 py-0.5 text-[11px] font-medium text-primary">{count}</span>
              )}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto space-y-3 px-2">
        <div className="rounded-lg border border-sidebar-border bg-background/60 p-3 text-xs text-muted-foreground">
          Emails send only to the owner&apos;s inbox. Research, PoC and drafting run in the Python pipeline
          (<code className="font-mono text-[11px]">python main.py sync</code>).
        </div>
        <ThemeToggle />
      </div>
    </aside>
  );
}

export function MobileNav() {
  const pathname = usePathname();
  return (
    <nav className="sticky top-0 z-30 flex gap-1 overflow-x-auto border-b bg-background/90 px-3 py-2 backdrop-blur md:hidden">
      {NAV.map((n) => {
        const on = n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
        return (
          <Link key={n.href} href={n.href}
            className={cn("whitespace-nowrap rounded-md px-3 py-1.5 text-sm", on ? "bg-accent font-medium text-accent-foreground" : "text-muted-foreground")}>
            {n.label}
          </Link>
        );
      })}
    </nav>
  );
}
