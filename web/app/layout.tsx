import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/sonner";
import { MobileNav, Sidebar } from "@/components/shell/sidebar";
import { sql } from "@/lib/db";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "Sprinto Outreach", template: "%s · Sprinto Outreach" },
  description: "Research, qualify, contact and learn: the Sprinto cold outreach pipeline console.",
};
export const dynamic = "force-dynamic";

async function navCounts() {
  try {
    const [r] = await sql`
      SELECT (SELECT count(*)::int FROM companies WHERE status = 'flagged'
                OR (needs_review AND status IN ('qualified','drafted'))) review,
             (SELECT count(*)::int FROM emails WHERE status IN ('drafted','flagged')) drafts,
             (SELECT count(*)::int FROM feedback_events WHERE processed_at IS NULL) loop`;
    return r as { review: number; drafts: number; loop: number };
  } catch {
    return { review: 0, drafts: 0, loop: 0 };
  }
}

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const counts = await navCounts();
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full">
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <div className="flex min-h-dvh">
            <Sidebar counts={counts} />
            <div className="flex min-w-0 flex-1 flex-col">
              <MobileNav />
              <main className="mx-auto w-full max-w-[1280px] flex-1 px-4 py-8 sm:px-8 lg:py-10">{children}</main>
            </div>
          </div>
          <Toaster richColors position="bottom-right" />
        </ThemeProvider>
      </body>
    </html>
  );
}
