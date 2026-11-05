import type { Metadata } from "next";
import Link from "next/link";
import { Search } from "lucide-react";
import { Reveal } from "@/components/motion/reveal";
import { CompanyLink, Empty, PageHeader, StatusBadge, fmtDate } from "@/components/common";
import { getCompanies } from "@/lib/queries";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Companies" };
export const dynamic = "force-dynamic";

const STATUSES = ["qualified", "drafted", "sent", "flagged", "dropped", "pooled"];

export default async function CompaniesPage({ searchParams }: PageProps<"/companies">) {
  const sp = await searchParams;
  const q = typeof sp.q === "string" && sp.q.trim() ? sp.q.trim() : null;
  const status = typeof sp.status === "string" && STATUSES.includes(sp.status) ? sp.status : null;
  const rows = await getCompanies(q, status);
  const href = (s: string | null) => {
    const p = new URLSearchParams();
    if (q) p.set("q", q);
    if (s) p.set("status", s);
    return `/companies${p.size ? `?${p}` : ""}`;
  };

  return (
    <Reveal>
      <PageHeader title="Companies" description="Every company the pipeline has researched or that was added by hand. Open one to see each decision with its evidence." />
      <div data-animate className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <form className="relative w-full sm:max-w-xs">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <input name="q" defaultValue={q ?? ""} placeholder="Search name or domain"
            className="h-9 w-full rounded-lg border bg-card pr-3 pl-9 text-sm shadow-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/40" />
          {status && <input type="hidden" name="status" value={status} />}
        </form>
        <div className="flex flex-wrap gap-1.5">
          {[null, ...STATUSES].map((s) => (
            <Link key={s ?? "all"} href={href(s)}
              className={cn("rounded-full border px-3 py-1 text-xs capitalize transition-colors",
                s === status ? "border-primary bg-primary/10 text-primary" : "bg-card text-muted-foreground hover:text-foreground")}>
              {s ?? "all"}
            </Link>
          ))}
        </div>
      </div>
      {rows.length === 0 ? <Empty>No companies match.</Empty> : (
        <div data-animate className="overflow-x-auto rounded-xl border bg-card">
          <table className="w-full min-w-[820px] text-sm">
            <thead className="border-b bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <tr className="[&>th]:px-4 [&>th]:py-2.5 [&>th]:font-medium">
                <th>Company</th><th>Stage</th><th>Industry</th><th>Status</th>
                <th className="text-right">Rank</th><th className="text-right">Fit</th><th>Reason</th><th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className="border-b last:border-0 transition-colors hover:bg-muted/40 [&>td]:px-4 [&>td]:py-2.5">
                  <td><CompanyLink id={c.id} name={c.name} /><div className="text-xs text-muted-foreground">{c.domain ?? "no domain"}</div></td>
                  <td className="text-muted-foreground">{c.funding_stage ?? "?"}</td>
                  <td className="text-xs text-muted-foreground">{(c.industry_tags ?? []).join(", ") || "—"}</td>
                  <td><StatusBadge status={c.status} /></td>
                  <td className="tabular text-right">{c.rank?.toFixed(3)}</td>
                  <td className="tabular text-right">{c.fit != null ? c.fit.toFixed(2) : "—"}</td>
                  <td className="max-w-[260px] truncate text-xs text-muted-foreground" title={c.reason ?? ""}>{c.reason ?? "—"}</td>
                  <td className="whitespace-nowrap text-xs text-muted-foreground">{fmtDate(c.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Reveal>
  );
}
