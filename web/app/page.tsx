import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import { Reveal, GrowBar } from "@/components/motion/reveal";
import { CompanyLink, Empty, Kpi, PageHeader, Panel, StatusBadge, fmtDate, pct } from "@/components/common";
import { RunSelect } from "@/components/run-select";
import { AnimatedShinyText } from "@/components/ui/animated-shiny-text";
import { BATCH_SIZE, MAX_BATCHES, MAX_FETCHES, TARGET_QUALIFIED, getLoop, getOverview, getRuns } from "@/lib/queries";
import { loopStages } from "@/lib/loop";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function Overview({ searchParams }: PageProps<"/">) {
  const sp = await searchParams;
  const runs = await getRuns();
  const runId = sp.run ? Number(sp.run) : (runs[0]?.id ?? null);
  const run = runs.find((r) => r.id === runId);
  const [o, loop] = await Promise.all([getOverview(runId), getLoop()]);
  const stages = loopStages(loop.stages as never);
  const liveStages = stages.filter((s) => s.live).length;

  const f = o.funnel;
  const funnel = [
    { label: "In pool", value: f.pool, hint: `${o.eligible} still unseen` },
    { label: "Researched", value: f.researched, hint: "Crawled, extracted, gated" },
    { label: "Qualified", value: f.qualified, hint: "Passed every gate" },
    { label: "Drafted", value: f.drafted, hint: "Email written + validated" },
    { label: "Sent", value: f.sent, hint: "Approved by a human" },
  ];
  const hit = BATCH_SIZE ? o.batch1.q / BATCH_SIZE : 0;
  const flagRate = run?.researched ? run.flagged / run.researched : null;

  return (
    <Reveal>
      <PageHeader
        eyebrow={
          <Link href="/loop" data-animate
            className="group inline-flex items-center rounded-full border bg-card px-3 py-1 text-xs shadow-xs transition hover:border-primary/40">
            <AnimatedShinyText className="inline-flex items-center gap-1.5">
              <Sparkles className="size-3" /> Feedback loop: {liveStages}/6 stages have data
              <ArrowRight className="size-3 transition-transform group-hover:translate-x-0.5" />
            </AnimatedShinyText>
          </Link>
        }
        title="Pipeline overview"
        description="Funded SaaS companies, shortlisted by rules, researched in batches of 20, qualified in Python, contacted only after a human approves."
        actions={runs.length ? <RunSelect runs={runs as never} current={runId} /> : null}
      />

      {!run ? (
        <Empty>No runs yet. Run <code>python main.py stage1</code> then <code>python main.py run</code>.</Empty>
      ) : (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            <Kpi label="Pool at start" value={run.pool_size} hint={`${o.eligible} unseen now`} />
            <Kpi label="Batches used" value={run.batches_used} suffix={`/ ${MAX_BATCHES}`} hint={`Stop: ${run.stop_reason ?? "running"}`} delay={0.05} />
            <Kpi label="Researched" value={run.researched} hint={`Started ${fmtDate(run.started_at, true)}`} delay={0.1} />
            <Kpi label="Qualified" value={run.qualified} suffix={`/ ${TARGET_QUALIFIED}`} hint="Run target" delay={0.15} />
            <Kpi label="Flagged" value={run.flagged} hint="Waiting on a human" delay={0.2} />
            <Kpi label="Hunter searches left" value={o.hunter.remaining ?? 50} hint={`${o.hunter.spent} spent`} delay={0.25} />
          </div>

          <div className="grid gap-6 lg:grid-cols-5">
            <Panel title="Funnel" description="All runs, current state" className="lg:col-span-3">
              <div className="space-y-3.5">
                {funnel.map((s, i) => (
                  <div key={s.label} className="grid grid-cols-[110px_1fr_48px] items-center gap-3 text-sm">
                    <div>
                      <div className="font-medium">{s.label}</div>
                      <div className="text-[11px] text-muted-foreground">{s.hint}</div>
                    </div>
                    <div className="h-2.5 overflow-hidden rounded-full bg-muted">
                      <GrowBar pct={f.pool ? (s.value / f.pool) * 100 : 0} delay={0.15 + i * 0.08}
                        className={cn("h-full rounded-full", i < 2 ? "bg-chart-2" : i < 4 ? "bg-primary" : "bg-brand")} />
                    </div>
                    <div className="tabular text-right font-semibold">{s.value}</div>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel title="Measurement" description="Spec §9: how well the batch and the drafts perform" className="lg:col-span-2">
              <dl className="grid grid-cols-2 gap-4">
                {[
                  { k: "Batch-1 hit rate", v: `${o.batch1.q}/${BATCH_SIZE}`, sub: `${o.batch1.q}/${o.batch1.n} of those actually researched`, p: hit },
                  { k: "Flag rate", v: pct(flagRate), sub: `${run.flagged} of ${run.researched} researched`, p: flagRate ?? 0 },
                  { k: "Fetches / company", v: (o.fetches.avg ?? 0).toFixed(1), sub: `max ${o.fetches.max ?? 0}, cap ${MAX_FETCHES}`, p: (o.fetches.avg ?? 0) / MAX_FETCHES },
                  { k: "Avg edit rate", v: pct(o.editRate.avg, 1), sub: `${o.editRate.n} sent emails`, p: o.editRate.avg ?? 0 },
                ].map((m, i) => (
                  <div key={m.k} className="space-y-1.5">
                    <dt className="text-xs text-muted-foreground">{m.k}</dt>
                    <dd className="tabular text-xl font-semibold tracking-tight">{m.v}</dd>
                    <div className="h-1 overflow-hidden rounded-full bg-muted">
                      <GrowBar pct={m.p * 100} delay={0.3 + i * 0.08} className="h-full rounded-full bg-primary/70" />
                    </div>
                    <div className="text-[11px] text-muted-foreground">{m.sub}</div>
                  </div>
                ))}
              </dl>
            </Panel>
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <Panel title="Drop reasons" description="Gate that failed first">
              <ReasonList rows={o.drops as never} empty="Nothing dropped in this run." tone="destructive" />
            </Panel>
            <Panel title="Flag reasons" description="Why a human is needed">
              <ReasonList rows={o.flags as never} empty="Nothing flagged in this run." tone="warning" />
            </Panel>
            <Panel title="Gemini tokens by stage" description="This run, non-cached">
              {o.tokens.length ? (
                <table className="w-full text-sm">
                  <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    <tr><th className="pb-2 font-medium">Stage</th><th className="pb-2 text-right font-medium">Calls</th>
                      <th className="pb-2 text-right font-medium">In</th><th className="pb-2 text-right font-medium">Out</th></tr>
                  </thead>
                  <tbody className="tabular">
                    {o.tokens.map((t) => (
                      <tr key={t.stage} className="border-t">
                        <td className="py-1.5 capitalize">{t.stage}</td>
                        <td className="py-1.5 text-right">{t.calls}</td>
                        <td className="py-1.5 text-right">{t.tokens_in.toLocaleString()}</td>
                        <td className="py-1.5 text-right">{t.tokens_out.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <Empty>No Gemini calls logged for this run.</Empty>}
            </Panel>
          </div>

          <div className="grid gap-6 lg:grid-cols-3">
            <Panel title="Companies in this run" className="lg:col-span-2"
              action={<Link href="/companies" className="text-xs text-primary hover:underline">All companies</Link>}>
              <div className="-mx-5 overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                    <tr className="[&>th]:px-5 [&>th]:pb-2 [&>th]:font-medium">
                      <th>Company</th><th>Stage</th><th>Status</th><th className="text-right">Fit</th><th>Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {o.companies.map((c) => (
                      <tr key={c.id} className="border-t transition-colors hover:bg-muted/40 [&>td]:px-5 [&>td]:py-2">
                        <td><CompanyLink id={c.id} name={c.name} /><div className="text-xs text-muted-foreground">{c.domain ?? "no domain"}</div></td>
                        <td className="text-muted-foreground">{c.funding_stage ?? "?"}</td>
                        <td><StatusBadge status={c.status} />{c.queued_action && <span className="ml-1.5 text-[11px] text-info">queued</span>}</td>
                        <td className="tabular text-right">{c.fit != null ? c.fit.toFixed(2) : "—"}</td>
                        <td className="max-w-[280px] truncate text-xs text-muted-foreground" title={c.reason ?? ""}>{c.reason ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
            <Panel title="Recent signals" description="Captured into the feedback loop"
              action={<Link href="/loop" className="text-xs text-primary hover:underline">Open loop</Link>}>
              {o.activity.length ? (
                <ol className="space-y-3">
                  {o.activity.map((a) => (
                    <li key={a.id} className="flex gap-3 text-sm">
                      <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" />
                      <div className="min-w-0">
                        <div className="truncate"><span className="font-medium">{a.kind.replace(/_/g, " ")}</span>
                          {a.company && <> · <CompanyLink id={a.company_id} name={a.company} /></>}</div>
                        <div className="text-xs text-muted-foreground">{a.source} · {fmtDate(a.created_at, true)}</div>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : <Empty>No signals yet. Edit, rate or send a draft.</Empty>}
            </Panel>
          </div>
        </div>
      )}
    </Reveal>
  );
}

function ReasonList({ rows, empty, tone }: { rows: { reason: string; n: number; companies: string }[]; empty: string; tone: "destructive" | "warning" }) {
  if (!rows.length) return <Empty>{empty}</Empty>;
  const max = Math.max(...rows.map((r) => r.n));
  return (
    <ul className="space-y-3">
      {rows.map((r, i) => (
        <li key={r.reason} className="space-y-1">
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span className="font-medium first-letter:uppercase">{r.reason.replace(/_/g, " ")}</span>
            <span className="tabular text-muted-foreground">{r.n}</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
            <GrowBar pct={(r.n / max) * 100} delay={0.2 + i * 0.06}
              className={cn("h-full rounded-full", tone === "destructive" ? "bg-destructive/70" : "bg-warning")} />
          </div>
          <div className="truncate text-[11px] text-muted-foreground" title={r.companies}>{r.companies}</div>
        </li>
      ))}
    </ul>
  );
}
