"use client";

import { useTransition } from "react";
import { toast } from "sonner";
import { setOutcome } from "@/app/actions";

const OUTCOMES = [
  ["", "Record outcome…"], ["replied", "Replied"], ["meeting", "Meeting booked"],
  ["not_interested", "Not interested"], ["no_reply", "No reply"], ["bounced", "Bounced"],
] as const;

/** Prospect signal. Emails go to the owner's inbox in this build, so the owner records what happened. */
export function OutcomeSelect({ emailId, value }: { emailId: number; value: string | null }) {
  const [pending, start] = useTransition();
  return (
    <select
      disabled={pending}
      defaultValue={value ?? ""}
      onChange={(e) => {
        const v = e.target.value;
        if (!v) return;
        start(async () => {
          const r = await setOutcome(emailId, v, "");
          (r.ok ? toast.success : toast.error)(r.message);
        });
      }}
      className="h-8 rounded-lg border bg-background px-2 text-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
    >
      {OUTCOMES.map(([v, l]) => <option key={v} value={v} disabled={!v}>{l}</option>)}
    </select>
  );
}
