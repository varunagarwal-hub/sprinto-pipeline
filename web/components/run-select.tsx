"use client";

import { useRouter, useSearchParams } from "next/navigation";

export function RunSelect({ runs, current }: { runs: { id: number; started_at: string; status: string }[]; current: number | null }) {
  const router = useRouter();
  const params = useSearchParams();
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Run</span>
      <select
        className="h-9 rounded-lg border bg-card px-3 text-sm shadow-xs outline-none focus-visible:ring-3 focus-visible:ring-ring/40"
        value={current ?? ""}
        onChange={(e) => {
          const p = new URLSearchParams(params.toString());
          p.set("run", e.target.value);
          router.push(`/?${p.toString()}`);
        }}
      >
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            Run {r.id} · {new Date(r.started_at).toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} · {r.status}
          </option>
        ))}
      </select>
    </label>
  );
}
