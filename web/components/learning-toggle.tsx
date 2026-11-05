"use client";

import { useTransition } from "react";
import { toast } from "sonner";
import { setLearningStatus } from "@/app/actions";
import { Button } from "@/components/ui/button";

export function LearningToggle({ id, active }: { id: number; active: boolean }) {
  const [pending, start] = useTransition();
  return (
    <Button size="xs" variant={active ? "ghost" : "outline"} disabled={pending}
      onClick={() => start(async () => {
        const r = await setLearningStatus(id, !active);
        (r.ok ? toast.success : toast.error)(r.message);
      })}>
      {active ? "Retire" : "Re-activate"}
    </Button>
  );
}
