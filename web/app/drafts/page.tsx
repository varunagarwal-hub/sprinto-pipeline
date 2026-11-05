import type { Metadata } from "next";
import Link from "next/link";
import { FlaskConical } from "lucide-react";
import { Reveal } from "@/components/motion/reveal";
import { CompanyLink, Empty, PageHeader, Panel, fmtDate, pct } from "@/components/common";
import { DraftEditor, type Draft } from "@/components/draft-editor";
import { OutcomeSelect } from "@/components/outcome-select";
import { getDrafts, getSent } from "@/lib/queries";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Drafts & sends" };
export const dynamic = "force-dynamic";

export default async function DraftsPage({ searchParams }: PageProps<"/drafts">) {
  const sp = await searchParams;
  const [drafts, sent] = (await Promise.all([getDrafts(), getSent()])) as [Draft[], Record<string, any>[]]; // eslint-disable-line @typescript-eslint/no-explicit-any
  const selectedId = sp.id ? Number(sp.id) : drafts[0]?.id;
  const selected = drafts.find((d) => d.id === selectedId) ?? drafts[0];
  const myEmail = process.env.MY_EMAIL ?? "MY_EMAIL";

  return (
    <Reveal>
      <PageHeader
        title="Drafts & sends"
        description={<>Human gate 2. Read, edit and approve. Every edit, rating and outcome is captured and distilled into learnings for the next drafts. Sends go to <span className="font-medium text-foreground">{myEmail}</span> only.</>}
      />
      {drafts.length === 0 ? (
        <Empty>No drafts waiting. Run <code>python main.py run</code> or <code>python main.py sync</code>.</Empty>
      ) : (
        <div className="grid gap-6 xl:grid-cols-[260px_1fr]">
          <nav data-animate className="space-y-1.5 xl:sticky xl:top-6 xl:self-start">
            {drafts.map((d) => (
              <Link key={d.id} href={`/drafts?id=${d.id}`} scroll={false}
                className={cn("block rounded-lg border px-3 py-2.5 text-sm transition-colors",
                  d.id === selected?.id ? "border-primary/40 bg-accent text-accent-foreground" : "bg-card hover:bg-muted/50")}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{d.company}</span>
                  {d.is_mock && <FlaskConical className="size-3.5 text-warning" aria-label="mock contact" />}
                </div>
                <div className="truncate text-xs text-muted-foreground">{d.full_name} · {d.title}</div>
              </Link>
            ))}
          </nav>
          {selected && <DraftEditor key={selected.id} draft={selected} myEmail={myEmail} />}
        </div>
      )}

      <Panel title="Sent" description="Record what the prospect did: this is the strongest signal the loop gets." className="mt-8">
        {sent.length ? (
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="text-left text-[11px] uppercase tracking-wide text-muted-foreground">
                <tr className="[&>th]:px-5 [&>th]:pb-2 [&>th]:font-medium">
                  <th>Company</th><th>To (PoC)</th><th>Subject</th><th>Sent</th><th className="text-right">Edit rate</th><th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {sent.map((s) => (
                  <tr key={s.id} className="border-t [&>td]:px-5 [&>td]:py-2.5">
                    <td><CompanyLink id={s.company_id} name={s.company} /></td>
                    <td className="text-muted-foreground">{s.full_name}<div className="text-xs">{s.title}</div></td>
                    <td className="max-w-[300px] truncate" title={s.sent_subject}>{s.sent_subject}</td>
                    <td className="whitespace-nowrap text-muted-foreground">{fmtDate(s.sent_at, true)}</td>
                    <td className="tabular text-right">{pct(s.edit_rate, 1)}</td>
                    <td><OutcomeSelect emailId={s.id} value={s.outcome} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>Nothing sent yet.</Empty>}
      </Panel>
    </Reveal>
  );
}
