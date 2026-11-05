import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { MagicCard } from "@/components/ui/magic-card";
import { NumberTicker } from "@/components/ui/number-ticker";

export function PageHeader({ title, description, actions, eyebrow }: {
  title: string; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode;
}) {
  return (
    <header data-animate className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="space-y-1.5">
        {eyebrow}
        <h1 className="text-2xl font-semibold tracking-tight sm:text-[28px]">{title}</h1>
        {description && <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

const STATUS: Record<string, string> = {
  pooled: "bg-muted text-muted-foreground",
  researching: "bg-info/12 text-info",
  qualified: "bg-success/12 text-success",
  drafted: "bg-primary/10 text-primary",
  sent: "bg-brand/12 text-brand",
  flagged: "bg-warning/15 text-[color-mix(in_oklch,var(--warning),black_25%)] dark:text-warning",
  dropped: "bg-destructive/10 text-destructive",
  pass: "bg-success/12 text-success",
  fail: "bg-destructive/10 text-destructive",
  unknown: "bg-warning/15 text-[color-mix(in_oklch,var(--warning),black_25%)] dark:text-warning",
  override: "bg-brand/12 text-brand",
  info: "bg-muted text-muted-foreground",
  active: "bg-success/12 text-success",
  retired: "bg-muted text-muted-foreground",
};

export function StatusBadge({ status, className }: { status: string | null | undefined; className?: string }) {
  const s = status ?? "unknown";
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium capitalize", STATUS[s] ?? STATUS.info, className)}>
      {s.replace(/_/g, " ")}
    </span>
  );
}

export function Kpi({ label, value, suffix, hint, decimals = 0, delay = 0 }: {
  label: string; value: number; suffix?: ReactNode; hint?: ReactNode; decimals?: number; delay?: number;
}) {
  return (
    <div data-animate>
      <MagicCard className="rounded-xl" gradientColor="color-mix(in oklch, var(--primary) 12%, transparent)">
        <div className="space-y-2 p-4">
          <div className="text-xs font-medium text-muted-foreground">{label}</div>
          <div className="flex items-baseline gap-1">
            <NumberTicker value={value} decimalPlaces={decimals} delay={delay}
              className="tabular text-2xl font-semibold tracking-tight text-foreground" />
            {suffix && <span className="text-sm text-muted-foreground">{suffix}</span>}
          </div>
          {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
        </div>
      </MagicCard>
    </div>
  );
}

export function StatText({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div data-animate>
      <MagicCard className="rounded-xl" gradientColor="color-mix(in oklch, var(--primary) 12%, transparent)">
        <div className="space-y-2 p-4">
          <div className="text-xs font-medium text-muted-foreground">{label}</div>
          <div className="text-2xl font-semibold tracking-tight">{value}</div>
          {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
        </div>
      </MagicCard>
    </div>
  );
}

export function Panel({ title, description, children, className, action }: {
  title?: ReactNode; description?: ReactNode; children: ReactNode; className?: string; action?: ReactNode;
}) {
  return (
    <section data-animate className={cn("rounded-xl border bg-card p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)]", className)}>
      {(title || action) && (
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-tight">{title}</h2>}
            {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">{children}</div>;
}

export function SourceLink({ href, children = "source" }: { href?: string | null; children?: ReactNode }) {
  if (!href) return <span className="text-muted-foreground">—</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer"
      className="inline-flex items-center gap-0.5 text-primary underline-offset-2 hover:underline">
      {children}
      <ArrowUpRight className="size-3" />
    </a>
  );
}

export function CompanyLink({ id, name }: { id: number; name: string }) {
  return <Link href={`/companies/${id}`} className="font-medium hover:text-primary hover:underline">{name}</Link>;
}

export const pct = (x: number | null | undefined, digits = 0) =>
  x == null || Number.isNaN(x) ? "—" : `${(x * 100).toFixed(digits)}%`;

export function fmtDate(d: string | Date | null | undefined, withTime = false) {
  if (!d) return "—";
  const dt = typeof d === "string" ? new Date(d) : d;
  return dt.toLocaleString("en-GB", withTime
    ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "short", year: "numeric" });
}
