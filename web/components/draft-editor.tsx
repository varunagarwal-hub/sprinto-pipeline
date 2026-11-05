"use client";

import { useMemo, useState, useTransition } from "react";
import { toast } from "sonner";
import { distance } from "fastest-levenshtein";
import { BrainCircuit, FlaskConical, Loader2, RefreshCw, Send, ThumbsDown, ThumbsUp } from "lucide-react";
import { rateDraft, sendDraft, switchHook, type ActionResult } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ShimmerButton } from "@/components/ui/shimmer-button";
import { BorderBeam } from "@/components/ui/border-beam";
import { SourceLink, StatusBadge } from "@/components/common";
import { ungrounded } from "@/lib/grounding";
import { cn } from "@/lib/utils";

type Item = { id: string; field: string; fact: string; quote: string | null; url: string | null; date: string | null };
export type Draft = {
  id: number; company_id: number; status: string; subject: string | null; original_body: string | null;
  hook: { evidence_id?: string; date?: string; quote?: string; url?: string } | null;
  pain_points: { text: string; evidence_id: string }[] | null; evidence_catalog: Item[] | null;
  hook_candidates: Item[] | null; validator_log: { attempt: number; errors: string[] }[] | null; rating: number | null;
  company: string; domain: string | null; funding_stage: string | null; needs_review: boolean; queued_action: string | null;
  full_name: string | null; first_name: string | null; title: string | null; poc_email: string | null; is_mock: boolean | null;
  confidence: number | null; verification_status: string | null; pick_reason: string | null;
  lessons: { id: number; scope: string; lesson: string }[];
};

const TAGS = ["great hook", "too generic", "too long", "wrong tone", "wrong person", "weak hook", "factual issue"];

export function DraftEditor({ draft, myEmail }: { draft: Draft; myEmail: string }) {
  const [subject, setSubject] = useState(draft.subject ?? "");
  const [body, setBody] = useState(draft.original_body ?? "");
  const [hook, setHook] = useState(draft.hook?.evidence_id ?? "");
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [pending, start] = useTransition();
  const catalog = draft.evidence_catalog ?? [];
  const byId = Object.fromEntries(catalog.map((i) => [i.id, i]));
  const original = draft.original_body ?? "";

  const words = body.trim() ? body.trim().split(/\s+/).length : 0;
  const editRate = distance(original, body) / Math.max(original.length, body.length, 1);
  const warnings = useMemo(
    () => ungrounded(body, [draft.company, draft.domain, draft.full_name, draft.title,
      ...catalog.flatMap((i) => [i.fact, i.quote, i.date])]),
    [body], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const run = (fn: () => Promise<ActionResult>, after?: () => void) =>
    start(async () => {
      const r = await fn();
      (r.ok ? toast.success : toast.error)(r.message);
      if (r.ok) after?.();
    });

  const lastErrors = draft.validator_log?.at(-1)?.errors ?? [];
  return (
    <div data-animate className="relative overflow-hidden rounded-xl border bg-card shadow-sm">
      <BorderBeam size={120} duration={10} colorFrom="var(--brand)" colorTo="var(--brand-2)" />
      <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold">{draft.company}</h2>
            <StatusBadge status={draft.status === "flagged" ? "flagged" : "drafted"} />
            {draft.needs_review && <span className="rounded-full bg-info/12 px-2 py-0.5 text-[11px] font-medium text-info">hook review</span>}
            {draft.queued_action && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px]">queued: {draft.queued_action}</span>}
          </div>
          <div className="text-sm">
            <span className="font-medium">{draft.full_name}</span>
            <span className="text-muted-foreground"> · {draft.title} · </span>
            <code className="text-xs">{draft.poc_email}</code>
            <span className="text-muted-foreground"> · {draft.verification_status}, {draft.confidence}%</span>
            {draft.is_mock && <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning"><FlaskConical className="size-3" /> mock contact</span>}
          </div>
          {draft.pick_reason && <p className="max-w-2xl text-xs text-muted-foreground">{draft.pick_reason}</p>}
        </div>
      </div>

      <div className="grid gap-0 lg:grid-cols-[1fr_1.25fr]">
        {/* evidence side */}
        <div className="space-y-5 border-b p-5 lg:border-r lg:border-b-0">
          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Hook</h3>
            <div className="rounded-lg border bg-muted/40 p-3 text-sm">
              <div className="mb-1 text-xs text-muted-foreground">{draft.hook?.evidence_id} · {draft.hook?.date}</div>
              <p>&ldquo;{draft.hook?.quote}&rdquo; <SourceLink href={draft.hook?.url} /></p>
            </div>
            {(draft.hook_candidates?.length ?? 0) > 1 && (
              <div className="flex gap-2">
                <select value={hook} onChange={(e) => setHook(e.target.value)}
                  className="h-8 min-w-0 flex-1 rounded-lg border bg-background px-2 text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/40">
                  {draft.hook_candidates!.map((c) => (
                    <option key={c.id} value={c.id}>{c.id} · {c.date} · {c.fact.slice(0, 60)}</option>
                  ))}
                </select>
                <Button size="sm" variant="outline" disabled={pending || hook === draft.hook?.evidence_id}
                  onClick={() => run(() => switchHook(draft.id, hook))}>
                  <RefreshCw /> Regenerate
                </Button>
              </div>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Pain points</h3>
            <ul className="space-y-2">
              {(draft.pain_points ?? []).map((p, i) => {
                const ev = byId[p.evidence_id];
                return (
                  <li key={i} className="rounded-lg border p-3 text-sm">
                    <p>{p.text}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {p.evidence_id}: &ldquo;{(ev?.quote ?? "").slice(0, 120)}&rdquo; <SourceLink href={ev?.url} />
                    </p>
                  </li>
                );
              })}
            </ul>
          </section>

          {draft.lessons.length > 0 && (
            <section className="space-y-2">
              <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <BrainCircuit className="size-3.5" /> Learnings applied
              </h3>
              <ul className="space-y-1.5 text-xs text-muted-foreground">
                {draft.lessons.map((l) => <li key={l.id}>· {l.lesson}</li>)}
              </ul>
            </section>
          )}

          <details className="group rounded-lg border text-sm">
            <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-muted-foreground">
              Evidence catalog ({catalog.length} items)
            </summary>
            <ul className="max-h-64 space-y-2 overflow-auto px-3 pb-3 text-xs scrollbar-thin">
              {catalog.map((i) => (
                <li key={i.id}><span className="font-medium">{i.id}</span> <span className="text-muted-foreground">{i.field}</span> · {i.fact}
                  {i.date && <span className="text-muted-foreground"> · {i.date}</span>} <SourceLink href={i.url} /></li>
              ))}
            </ul>
          </details>

          {draft.status === "flagged" && lastErrors.length > 0 && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
              Validator: {lastErrors.join("; ")}
            </div>
          )}
        </div>

        {/* editor side */}
        <div className="space-y-4 p-5">
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Subject</span>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="block space-y-1.5">
            <span className="text-xs font-medium text-muted-foreground">Body</span>
            <Textarea value={body} onChange={(e) => { setBody(e.target.value); setConfirm(false); }}
              className="min-h-[300px] font-[450] leading-relaxed" />
          </label>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span className={cn("tabular", words >= 120 ? "font-medium text-destructive" : "text-muted-foreground")}>{words} / 120 words</span>
            <span className="tabular text-muted-foreground">edit rate {(editRate * 100).toFixed(1)}%</span>
            {warnings.length ? (
              <span className="text-warning">Not in evidence: {warnings.join(", ")}</span>
            ) : <span className="text-success">All terms grounded</span>}
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t pt-4">
            <ShimmerButton
              disabled={pending || !body.trim()}
              background="var(--primary)" shimmerColor="#ffffff" borderRadius="10px"
              className="h-10 px-5 text-sm font-medium text-primary-foreground disabled:opacity-60"
              onClick={() => (confirm ? run(() => sendDraft(draft.id, subject, body)) : setConfirm(true))}>
              {pending ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Send className="mr-2 size-4" />}
              {confirm ? `Confirm: send to ${myEmail}` : "Approve & send"}
            </ShimmerButton>
            <span className="text-xs text-muted-foreground">Goes to your inbox only, never to {draft.first_name ?? "the prospect"}.</span>
          </div>

          <div className="space-y-2 rounded-lg border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs font-medium">Rate this draft <span className="text-muted-foreground">(feeds the loop)</span></span>
              {draft.rating != null && <span className="text-xs text-muted-foreground">rated {draft.rating > 0 ? "👍" : "👎"}</span>}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {TAGS.map((t) => (
                <button key={t} type="button" onClick={() => setTags((x) => (x.includes(t) ? x.filter((y) => y !== t) : [...x, t]))}
                  className={cn("rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                    tags.includes(t) ? "border-primary bg-primary/10 text-primary" : "text-muted-foreground hover:border-foreground/30")}>
                  {t}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <Input placeholder="What should the writer do differently? (optional)" value={note} onChange={(e) => setNote(e.target.value)} className="h-8 text-xs" />
              <Button size="sm" variant="outline" disabled={pending} onClick={() => run(() => rateDraft(draft.id, 1, tags, note), () => { setTags([]); setNote(""); })}>
                <ThumbsUp />
              </Button>
              <Button size="sm" variant="outline" disabled={pending} onClick={() => run(() => rateDraft(draft.id, -1, tags, note), () => { setTags([]); setNote(""); })}>
                <ThumbsDown />
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
