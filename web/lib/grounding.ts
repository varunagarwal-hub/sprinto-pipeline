/**
 * Client-side mirror of write.ungrounded_terms(): flags numbers and capitalised names in an edited
 * body that appear nowhere in the evidence catalog or Sprinto's value props. Advisory only: the
 * reviewer may add things on purpose, but they should see it before sending.
 */
export const SPRINTO_VALUE_PROPS = `Sprinto automates compliance certifications (SOC 2, ISO 27001, GDPR and more) for SaaS companies.
Faster audit readiness: guided, automated controls get a team audit-ready faster than a manual, spreadsheet-driven process.
Automated evidence collection: integrations with cloud, HR and code tools collect evidence continuously, so engineers don't screenshot consoles before an audit.
Multi-framework from one setup: controls map across SOC 2, ISO 27001 and GDPR, so adding a second framework reuses most of the work.
Unblocks enterprise deals: a certification plus a trust center answers security questionnaires that stall enterprise and regulated-industry sales.`;

const ALWAYS_OK = new Set(
  ("i hi hello hey best thanks thank cheers regards sprinto soc iso gdpr hipaa pci dss type ii ai saas cto ciso vp ceo grc api us eu " +
   "monday tuesday wednesday thursday friday january february march april may june july august september october november december " +
   "jan feb mar apr jun jul aug sep sept oct nov dec series would worth open quick could happy").split(" "),
);

export function ungrounded(body: string, corpusParts: (string | null | undefined)[]) {
  const corpus = corpusParts.filter(Boolean).join(" ").toLowerCase() + " " + SPRINTO_VALUE_PROPS.toLowerCase();
  const lines = body.split("\n").filter((l) => l.trim());
  const text = lines.length > 1 ? lines.slice(1).join(" ") : body;
  const nums = [...text.matchAll(/\d[\d,.]*/g)].map((m) => m[0].replace(/[.,]+$/, ""));
  const badNums = [...new Set(nums.filter((n) => n !== "15" && !corpus.replace(/,/g, "").includes(n.replace(/,/g, ""))))];
  const names = new Set<string>();
  for (const sentence of text.split(/(?<=[.!?])\s+|\n/)) {
    const words = sentence.split(/\s+/).slice(1);
    for (let w of words) {
      w = w.replace(/^[,.;:!?()"“”]+|[,.;:!?()"“”]+$/g, "").replace(/['’]s$/, "");
      if (w && /^[A-Z]/.test(w) && !ALWAYS_OK.has(w.toLowerCase()) && !corpus.includes(w.toLowerCase())) names.add(w);
    }
  }
  return [...badNums, ...names];
}
