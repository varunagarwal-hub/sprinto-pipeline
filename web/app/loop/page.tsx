import type { Metadata } from "next";
import { CheckCircle2, CircleDashed, Terminal } from "lucide-react";
import { Reveal } from "@/components/motion/reveal";
import { CompanyLink, Empty, PageHeader, Panel, StatusBadge, fmtDate, pct } from "@/components/common";
import { LoopDiagram } from "@/components/loop-diagram";
import { LearningToggle } from "@/components/learning-toggle";
import { BorderBeam } from "@/components/ui/border-beam";
import { getLoop } from "@/lib/queries";
import { loopStages } from "@/lib/loop";

export const metadata: Metadata = { title: "Feedback loop" };
export const dynamic = "force-dynamic";

const HOW: Record<string, string> = {
  act: "Pipeline drafts emails; a reviewer approves and sends them.",
  signal: "Edits before sending, 👍/👎 with reasons, hook switches, qualify/drop overrides, prospect outcomes, Resend delivery webhooks.",
  capture: "Each signal is written to feedback_events with the context needed to generalise: PoC role, industry, stage, hook type.",
  store: "python main.py learn (also at the start of every run): phrases reviewers cut in ≥2 sends, hook-type reply rates, bounded industry rank deltas, and Gemini-summarised style lessons that must cite real events.",
  retrieve: "Before drafting, learnings matching the prospect's role / industry / stage are looked up; hook and ranking scores are read.",
  apply: "Lessons go into the writer prompt, learned avoid-phrases are enforced by the validator, hooks are re-ordered, Stage 1 rank is nudged. Every use is logged.",
};

export default async function LoopPage() {
  const { stages: s, learnings, events, byKind, effect, perScope } = await getLoop();
  const stages = loopStages(s as never);
  const live = stages.filter((x) => x.live).length;
  const withL = effect.find((e) => e.with_learnings);
  const without = effect.find((e) => !e.with_learnings);

  return (
    <Reveal>
      <PageHeader
        title="Feedback loop"
        description="Act → Signal → Capture → Store → Retrieve → Apply. All six stages are wired; the counts show which ones have data yet. Every number below comes from Neon."
        actions={<span data-animate className="rounded-full border bg-card px-3 py-1 text-xs"><b className="tabular">{live}/6</b> stages live</span>}
      />

      <section data-animate className="relative mb-8 overflow-hidden rounded-2xl border bg-card p-4 sm:p-6">
        <BorderBeam size={180} duration={12} colorFrom="var(--brand)" colorTo="var(--brand-2)" />
        <LoopDiagram stages={stages} />
      </section>

      <div className="mb-8 grid gap-6 lg:grid-cols-3">
        <Panel title="Is the loop working?" description="Sent emails with vs without retrieved learnings" className="lg:col-span-1">
          <dl className="grid grid-cols-2 gap-4 text-sm">
            {[["Without learnings", without], ["With learnings", withL]].map(([label, e]) => {
              const row = e as { n: number; edit_rate: number | null; positive: number } | undefined;
              return (
                <div key={label as string} className="space-y-1 rounded-lg border p-3">
                  <dt className="text-xs text-muted-foreground">{label as string}</dt>
                  <dd className="tabular text-xl font-semibold">{pct(row?.edit_rate, 1)}</dd>
                  <div className="text-[11px] text-muted-foreground">avg edit rate · {row?.n ?? 0} sent · {row?.positive ?? 0} replies</div>
                </div>
              );
            })}
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">Falling edit rate and rising replies on the right means the stored lessons are paying off.</p>
        </Panel>
        <Panel title="Signals by kind" description={`${s.captured} captured · ${s.unprocessed} waiting for the next learn`}>
          {byKind.length ? (
            <ul className="space-y-2 text-sm">
              {byKind.map((k) => (
                <li key={k.kind} className="flex justify-between"><span>{k.kind.replace(/_/g, " ")}</span><span className="tabular text-muted-foreground">{k.n}</span></li>
              ))}
            </ul>
          ) : <Empty>No signals yet.</Empty>}
        </Panel>
        <Panel title="Store" description={`Last distilled ${fmtDate(s.last_learned, true)}`}>
          <ul className="mb-4 space-y-2 text-sm">
            {["writer", "hook", "ranking"].map((scope) => (
              <li key={scope} className="flex justify-between"><span className="capitalize">{scope}</span>
                <span className="tabular text-muted-foreground">{perScope.find((p) => p.scope === scope)?.n ?? 0} active</span></li>
            ))}
          </ul>
          <div className="flex items-start gap-2 rounded-lg bg-muted/50 p-3 text-xs text-muted-foreground">
            <Terminal className="mt-0.5 size-3.5 shrink-0" />
            <span><code>python main.py sync</code> runs queued research/redrafts and distils new signals. Every <code>run</code> does this first.</span>
          </div>
        </Panel>
      </div>

      <Panel title="How each stage works" className="mb-8">
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {stages.map((st, i) => (
            <li key={st.key} className="flex gap-3">
              {st.live ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <CircleDashed className="mt-0.5 size-4 shrink-0 text-warning" />}
              <div><div className="text-sm font-medium">{i + 1}. {st.label}</div><p className="text-xs leading-relaxed text-muted-foreground">{HOW[st.key]}</p></div>
            </li>
          ))}
        </ol>
      </Panel>

      <Panel title="Learnings (memory)" description="Retired learnings are kept for audit but never retrieved" className="mb-8">
        {learnings.length ? (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <tr className="[&>th]:px-5 [&>th]:pb-2 [&>th]:font-medium"><th>Scope</th><th>Lesson</th><th>Applies to</th>
                  <th className="text-right">Support</th><th className="text-right">Used</th><th>Status</th><th /></tr>
              </thead>
              <tbody>
                {learnings.map((l) => (
                  <tr key={l.id} className="border-t align-top [&>td]:px-5 [&>td]:py-2.5">
                    <td className="text-xs capitalize text-muted-foreground">{l.scope}<div>{l.kind.replace(/_/g, " ")}</div></td>
                    <td className="max-w-[420px]">{l.lesson}</td>
                    <td className="text-xs text-muted-foreground">{Object.entries(l.applies_to ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ") || "all"}</td>
                    <td className="tabular text-right">{l.support}<div className="text-[11px] text-muted-foreground">{l.evidence} events</div></td>
                    <td className="tabular text-right">{l.times_applied}</td>
                    <td><StatusBadge status={l.status} /></td>
                    <td><LearningToggle id={l.id} active={l.status === "active"} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No learnings yet. They appear once reviewers edit, rate or record outcomes and <code>python main.py learn</code> runs. Phrase rules need the same cut in 2 sends; hook preferences need 3 outcomes.</Empty>
        )}
      </Panel>

      <Panel title="Signal feed" description="Most recent 40 events">
        {events.length ? (
          <ul className="divide-y text-sm">
            {events.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                <span className="flex items-center gap-2">
                  <span className={`size-1.5 rounded-full ${e.processed_at ? "bg-success" : "bg-warning"}`} title={e.processed_at ? "distilled" : "waiting"} />
                  <b>{e.kind.replace(/_/g, " ")}</b>
                  {e.company && <>· <CompanyLink id={e.company_id} name={e.company} /></>}
                  {e.edit_rate && <span className="text-muted-foreground">· edit {pct(Number(e.edit_rate), 1)}</span>}
                  {e.outcome && <span className="text-muted-foreground">· {e.outcome}</span>}
                  {e.rating != null && <span>{e.rating > 0 ? "👍" : "👎"}</span>}
                  {e.tags?.length > 0 && <span className="text-xs text-muted-foreground">[{e.tags.join(", ")}]</span>}
                </span>
                <span className="text-xs text-muted-foreground">{e.source} · {fmtDate(e.created_at, true)}</span>
              </li>
            ))}
          </ul>
        ) : <Empty>No signals captured yet.</Empty>}
      </Panel>
    </Reveal>
  );
}
