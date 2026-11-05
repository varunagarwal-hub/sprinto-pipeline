import type { Metadata } from "next";
import { Reveal } from "@/components/motion/reveal";
import { Empty, PageHeader } from "@/components/common";
import { ReviewCard, type ReviewItem } from "@/components/review-card";
import { getReviewQueue } from "@/lib/queries";

export const metadata: Metadata = { title: "Review queue" };
export const dynamic = "force-dynamic";

export default async function ReviewPage() {
  const items = (await getReviewQueue()) as ReviewItem[];
  const flagged = items.filter((i) => i.status === "flagged");
  const hooks = items.filter((i) => i.status !== "flagged");
  return (
    <Reveal>
      <PageHeader
        title="Review queue"
        description="Human gate 1. Companies the rules could not settle, and qualified companies whose hook needs a human eye. Every decision here is logged and fed back into the loop."
      />
      {items.length === 0 ? (
        <Empty>Nothing waiting. The pipeline settled every company on its own.</Empty>
      ) : (
        <div className="space-y-10">
          <section className="space-y-4">
            <h2 data-animate className="text-sm font-semibold">Flagged <span className="tabular text-muted-foreground">({flagged.length})</span></h2>
            {flagged.length ? flagged.map((i) => <ReviewCard key={i.id} item={i} />) : <Empty>No flagged companies.</Empty>}
          </section>
          <section className="space-y-4">
            <h2 data-animate className="text-sm font-semibold">Qualified, hook needs review <span className="tabular text-muted-foreground">({hooks.length})</span></h2>
            {hooks.length ? hooks.map((i) => <ReviewCard key={i.id} item={i} />) : <Empty>No hooks waiting.</Empty>}
          </section>
        </div>
      )}
    </Reveal>
  );
}
