"use client";

import { useState, useTransition } from "react";
import { toast } from "sonner";
import { Check, Globe, Loader2, X } from "lucide-react";
import { approveReview, dropCompany, provideDomain, qualifyCompany, type ActionResult } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CompanyLink, SourceLink, StatusBadge } from "@/components/common";

type Gate = { id: number; stage: string; gate: string; result: string; evidence_quote: string | null;
  source_url: string | null; evidence_date: string | null; reason: string | null; decided_by: string };

export type ReviewItem = {
  id: number; name: string; domain: string | null; funding_stage: string | null; status: string; needs_review: boolean;
  flag_reason: string | null; unknown_fields: string[] | null; queued_action: string | null; article_url: string | null;
  funding: { headline?: string; url?: string; date?: string } | null; gates: Gate[] | null; has_contact: boolean;
};

export function ReviewCard({ item }: { item: ReviewItem }) {
  const [pending, start] = useTransition();
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [domain, setDomain] = useState(item.domain ?? "");
  const flagged = item.status === "flagged";

  const run = (fn: () => Promise<ActionResult>) =>
    start(async () => {
      const r = await fn();
      (r.ok ? toast.success : toast.error)(`${item.name}: ${r.message}`);
    });

  const gates = (item.gates ?? []).filter((g) => g.stage !== "human");
  return (
    <article data-animate className="rounded-xl border bg-card shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b p-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <CompanyLink id={item.id} name={item.name} />
            <StatusBadge status={item.status} />
            {!flagged && <span className="rounded-full bg-info/12 px-2 py-0.5 text-[11px] font-medium text-info">hook review</span>}
            {item.queued_action && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">queued: {item.queued_action}</span>}
          </div>
          <div className="text-xs text-muted-foreground">
            {item.funding_stage ?? "stage ?"} · {item.domain ?? "no domain"}
          </div>
        </div>
        <div className="max-w-md text-right text-sm">
          <div className="font-medium">{item.flag_reason ?? "Needs review"}</div>
          {!!item.unknown_fields?.length && (
            <div className="text-xs text-muted-foreground">Unknown after research: {item.unknown_fields.join(", ")}</div>
          )}
        </div>
      </div>

      <div className="grid gap-5 p-5 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-3 text-sm">
          {item.funding?.headline && (
            <p className="text-muted-foreground">
              <span className="font-medium text-foreground">Funding:</span> {item.funding.headline}
              {item.funding.date && <> ({item.funding.date})</>} <SourceLink href={item.article_url ?? item.funding.url} />
            </p>
          )}
          {gates.length > 0 ? (
            <ul className="divide-y rounded-lg border">
              {gates.slice(0, 7).map((g) => (
                <li key={g.id} className="grid grid-cols-[150px_70px_1fr] items-start gap-3 px-3 py-2 text-xs">
                  <span className="font-medium text-foreground">{g.gate.replace(/_/g, " ")}</span>
                  <StatusBadge status={g.result} />
                  <span className="text-muted-foreground">
                    {g.evidence_quote ? <>&ldquo;{g.evidence_quote.slice(0, 140)}&rdquo; </> : g.reason}
                    {g.source_url && <SourceLink href={g.source_url} />}
                  </span>
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted-foreground">No gate evidence yet: the company has not been researched.</p>}
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <Input placeholder={flagged ? "Why qualify? (optional)" : "Note (optional)"} value={note} onChange={(e) => setNote(e.target.value)} />
            <Button className="w-full" disabled={pending}
              onClick={() => run(() => (flagged ? qualifyCompany(item.id, note) : approveReview(item.id, note)))}>
              {pending ? <Loader2 className="animate-spin" /> : <Check />}
              {flagged ? "Qualify" : "Approve hook as is"}
            </Button>
          </div>
          <div className="flex gap-2">
            <Input placeholder="Drop reason, e.g. not B2B" value={reason} onChange={(e) => setReason(e.target.value)} />
            <Button variant="destructive" disabled={pending} onClick={() => run(() => dropCompany(item.id, reason))}>
              <X /> Drop
            </Button>
          </div>
          {flagged && (
            <div className="flex gap-2">
              <Input placeholder="acme.io" value={domain} onChange={(e) => setDomain(e.target.value)} />
              <Button variant="outline" disabled={pending || !domain.trim()} onClick={() => run(() => provideDomain(item.id, domain))}>
                <Globe /> Research
              </Button>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
