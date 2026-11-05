import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { Reveal } from "@/components/motion/reveal";
import { Empty, Kpi, PageHeader, Panel, SourceLink, StatText, StatusBadge, fmtDate, pct } from "@/components/common";
import { Tabs } from "@/components/tabs";
import { getCompany } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: PageProps<"/companies/[id]">): Promise<Metadata> {
  const { id } = await params;
  const d = await getCompany(Number(id));
  return { title: d?.company.name ?? "Company" };
}

function Json({ value }: { value: unknown }) {
  return <pre className="max-h-96 overflow-auto rounded-lg bg-muted/50 p-3 text-xs leading-relaxed scrollbar-thin">{JSON.stringify(value, null, 2)}</pre>;
}

export default async function CompanyPage({ params }: PageProps<"/companies/[id]">) {
  const { id } = await params;
  const d = await getCompany(Number(id));
  if (!d) notFound();
  const { company: c, decisions, pages, contacts, emails, events, applied } = d;
  const ev = c.evidence ?? {};
  const loop = (ev._loop ?? []) as { iteration: number; target: string; page_type: string; url: string | null; ok: boolean }[];
  const rejected = ev._meta?.rejected_facts ?? [];
  const contact = contacts[0];

  const gates = (
    <div className="overflow-x-auto rounded-xl border bg-card">
      <table className="w-full min-w-[860px] text-sm">
        <thead className="border-b bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
          <tr className="[&>th]:px-4 [&>th]:py-2.5 [&>th]:font-medium"><th>Stage</th><th>Gate</th><th>Result</th><th>Evidence</th><th>Date</th><th>By</th></tr>
        </thead>
        <tbody>
          {decisions.map((g) => (
            <tr key={g.id} className="border-b align-top last:border-0 [&>td]:px-4 [&>td]:py-2.5">
              <td className="text-xs text-muted-foreground">{g.stage}</td>
              <td className="font-medium">{g.gate.replace(/_/g, " ")}</td>
              <td><StatusBadge status={g.result} /></td>
              <td className="max-w-[460px] text-xs">
                {g.evidence_quote && <p className="text-foreground">&ldquo;{g.evidence_quote.slice(0, 260)}&rdquo;</p>}
                {g.reason && <p className="text-muted-foreground">{g.reason}</p>}
                {g.source_url && <SourceLink href={g.source_url} />}
              </td>
              <td className="whitespace-nowrap text-xs text-muted-foreground">{g.evidence_date ?? "—"}</td>
              <td className="text-xs text-muted-foreground">{g.decided_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const research = (
    <div className="grid gap-6 lg:grid-cols-2">
      <Panel title="Agent loop" description="Pages fetched to resolve unknown gates">
        {loop.length ? (
          <ol className="space-y-2 text-sm">
            {loop.map((l) => (
              <li key={l.iteration} className="flex items-start gap-3">
                <span className="tabular mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-primary/10 text-[11px] font-medium text-primary">{l.iteration}</span>
                <span>Chased <b>{l.target}</b> via <b>{l.page_type}</b> → {l.ok ? "found text" : "nothing"} {l.url && <SourceLink href={l.url} />}</span>
              </li>
            ))}
          </ol>
        ) : <Empty>No refetches were needed.</Empty>}
      </Panel>
      <Panel title="Pages fetched" description={`${pages.length} fetches (cap 6)`}>
        <ul className="space-y-1.5 text-xs">
          {pages.map((p) => (
            <li key={p.id} className="flex items-center gap-2">
              <span className="tabular w-6 text-muted-foreground">#{p.iteration}</span>
              <span className="w-16 font-medium">{p.page_type}</span>
              <span className="tabular w-10 text-muted-foreground">{p.http_status ?? "—"}</span>
              <span className="tabular w-16 text-right text-muted-foreground">{p.text_chars} ch</span>
              <SourceLink href={p.page_url}>{p.page_url.replace(/^https?:\/\//, "").slice(0, 48)}</SourceLink>
            </li>
          ))}
        </ul>
      </Panel>
      <Panel title="Extracted evidence" description="Verified quotes only" className="lg:col-span-2"><Json value={Object.fromEntries(Object.entries(ev).filter(([k]) => !k.startsWith("_")))} /></Panel>
      {rejected.length > 0 && (
        <Panel title={`${rejected.length} rejected fact(s)`} description="Quote not found on the cited page, so the fact was nulled" className="lg:col-span-2">
          <Json value={rejected} />
        </Panel>
      )}
    </div>
  );

  const poc = contact ? (
    <div className="space-y-4">
      <Panel title="Point of contact">
        <div className="space-y-1 text-sm">
          <div className="text-base font-semibold">{contact.full_name}</div>
          <div>{contact.title} · <code className="text-xs">{contact.email}</code> · {contact.verification_status}, {contact.confidence}%
            {contact.is_mock && <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning"><FlaskConical className="size-3" /> mock</span>}</div>
          <p className="text-xs text-muted-foreground">{contact.pick_reason}</p>
        </div>
      </Panel>
      {contacts.length > 1 && (
        <Panel title="Earlier contacts" description="Contacts are append-only; the newest is current">
          <ul className="space-y-1 text-xs text-muted-foreground">
            {contacts.slice(1).map((k) => <li key={k.id}>{k.full_name} · {k.title} {k.is_mock ? "(mock)" : ""} · {fmtDate(k.created_at, true)}</li>)}
          </ul>
        </Panel>
      )}
    </div>
  ) : <Empty>No contact yet.</Empty>;

  const mail = emails.length ? (
    <div className="space-y-4">
      {emails.map((e) => (
        <Panel key={e.id} title={<span className="flex items-center gap-2">{e.subject ?? "(no subject)"} <StatusBadge status={e.status} /></span>}
          description={e.sent_at ? `Sent ${fmtDate(e.sent_at, true)} to ${e.sent_to} · edit rate ${pct(e.edit_rate, 1)}${e.outcome ? ` · outcome: ${e.outcome}` : ""}` : `Created ${fmtDate(e.created_at, true)}`}>
          <div className="grid gap-4 lg:grid-cols-2">
            <div><div className="mb-1 text-xs font-medium text-muted-foreground">Generated</div>
              <pre className="whitespace-pre-wrap rounded-lg bg-muted/40 p-3 font-sans text-sm leading-relaxed">{e.original_body}</pre></div>
            <div><div className="mb-1 text-xs font-medium text-muted-foreground">Sent</div>
              <pre className="whitespace-pre-wrap rounded-lg bg-muted/40 p-3 font-sans text-sm leading-relaxed">{e.sent_body ?? "(not sent)"}</pre></div>
          </div>
        </Panel>
      ))}
    </div>
  ) : <Empty>No emails yet.</Empty>;

  const feedback = (
    <div className="grid gap-6 lg:grid-cols-2">
      <Panel title="Signals captured" description="feedback_events for this company">
        {events.length ? (
          <ul className="space-y-2 text-sm">
            {events.map((f) => (
              <li key={f.id} className="flex justify-between gap-3 border-b pb-2 last:border-0">
                <span><b>{f.kind.replace(/_/g, " ")}</b> <span className="text-muted-foreground">· {f.source}{f.note ? ` · ${f.note}` : ""}</span></span>
                <span className="whitespace-nowrap text-xs text-muted-foreground">{fmtDate(f.created_at, true)}</span>
              </li>
            ))}
          </ul>
        ) : <Empty>No signals yet.</Empty>}
      </Panel>
      <Panel title="Learnings applied" description="Memory retrieved while drafting for this company">
        {applied.length ? <ul className="space-y-2 text-sm">{applied.map((l) => <li key={l.id}><span className="text-xs text-muted-foreground">{l.scope}</span> · {l.lesson}</li>)}</ul>
          : <Empty>No learnings were applied.</Empty>}
      </Panel>
    </div>
  );

  return (
    <Reveal>
      <Link data-animate href="/companies" className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" /> Companies
      </Link>
      <PageHeader
        title={c.name}
        description={<>{c.domain ? <SourceLink href={`https://${c.domain}`}>{c.domain}</SourceLink> : "no domain"} · domain {c.domain_source ?? "unresolved"}
          {c.article_url && <> · <SourceLink href={c.article_url}>funding article</SourceLink></>}</>}
        actions={<StatusBadge status={c.status} className="text-xs" />}
      />
      {(c.drop_reason || c.flag_reason) && (
        <div data-animate className="mb-6 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">{c.drop_reason ?? c.flag_reason}</div>
      )}
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatText label="Funding stage" value={c.funding_stage ?? "Unknown"} hint={c.funding_date ? `announced ${fmtDate(c.funding_date)}` : "from Stage 1"} />
        <Kpi label="Rank score" value={c.rank ?? 0} decimals={3} hint="Stage 1" />
        <Kpi label="Fit score" value={c.fit ?? 0} decimals={2} hint={c.fit == null ? "not qualified" : "qualifier"} />
        <Kpi label="Fetches" value={ev._fetches ?? 0} hint="of 6 allowed" />
      </div>
      <Tabs tabs={[
        { id: "gates", label: `Gates & evidence (${decisions.length})`, content: gates },
        { id: "research", label: "Research", content: research },
        { id: "poc", label: "Contact", content: poc },
        { id: "email", label: `Emails (${emails.length})`, content: mail },
        { id: "feedback", label: `Feedback (${events.length})`, content: feedback },
      ]} />
    </Reveal>
  );
}
