import Link from "next/link";
import "./globals.css";

export const metadata = {
  title: "LOYAL EDGE — Sports Intelligence",
  description: "Clear, accountable sports intelligence with predictions, evidence and a public track record.",
  icons: { icon: "/favicon.svg" },
};

const PRIMARY_NAV = [
  { href: "/", label: "Today", icon: "⌂" },
  { href: "/live", label: "Live", icon: "●" },
  { href: "/predictions", label: "Reads", icon: "✦" },
  { href: "/history", label: "History", icon: "↗" },
  { href: "/performance", label: "Performance", icon: "◒" },
];

const SECONDARY_NAV = [
  { href: "/fixtures", label: "Fixtures" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/community-leaderboard", label: "Tipsters" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="pb-20 md:pb-0">
        <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-[#060913]/90 backdrop-blur-2xl">
          <div className="mx-auto flex h-16 max-w-7xl items-center gap-5 px-4 sm:px-6">
            <Link href="/" className="shrink-0 leading-none">
              <span className="block text-[18px] font-black tracking-[-0.04em] text-white">LOYAL</span>
              <span className="block text-[18px] font-black tracking-[-0.04em] text-emerald-400">EDGE</span>
            </Link>

            <nav className="hidden min-w-0 flex-1 items-center justify-center gap-1 md:flex" aria-label="Primary navigation">
              {PRIMARY_NAV.map((item) => (
                <Link key={item.href} href={item.href} className="nav-link">
                  <span aria-hidden="true" className="text-[11px] opacity-60">{item.icon}</span>{item.label}
                </Link>
              ))}
            </nav>

            <nav className="ml-auto hidden items-center gap-1 lg:flex" aria-label="More navigation">
              {SECONDARY_NAV.map((item) => <Link key={item.href} href={item.href} className="nav-link-secondary">{item.label}</Link>)}
            </nav>

            <div className="ml-auto flex items-center gap-2 md:hidden">
              <Link href="/live" className="mobile-live" aria-label="Live matches"><span className="live-dot" /> Live</Link>
            </div>
          </div>
          <div className="hidden border-t border-white/[0.05] md:block">
            <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2 sm:px-6">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">Sports intelligence · evidence first</p>
              <div className="flex gap-4">{SECONDARY_NAV.map((item) => <Link key={item.href} href={item.href} className="text-[11px] font-semibold text-slate-500 transition hover:text-slate-200 lg:hidden">{item.label}</Link>)}</div>
            </div>
          </div>
        </header>

        {children}

        <nav className="fixed inset-x-0 bottom-0 z-50 border-t border-white/[0.08] bg-[#080d18]/95 px-2 pb-[max(0.55rem,env(safe-area-inset-bottom))] pt-2 backdrop-blur-2xl md:hidden" aria-label="Mobile navigation">
          <div className="mx-auto grid max-w-md grid-cols-5 gap-1">
            {PRIMARY_NAV.map((item) => (
              <Link key={item.href} href={item.href} className="mobile-nav-link">
                <span className="text-sm opacity-70" aria-hidden="true">{item.icon}</span>
                <span>{item.label}</span>
              </Link>
            ))}
          </div>
        </nav>
      </body>
    </html>
  );
}
