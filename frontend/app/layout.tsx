import Link from "next/link";
import "./globals.css";

export const metadata = {
  title: "LOYAL EDGE - Transparent Sports Prediction Intelligence",
  description: "Football and basketball predictions with honest verdicts, model reasoning, market analysis, and a fully public tracked record.",
  icons: { icon: "/favicon.svg" },
};

const NAV = [
  { href: "/", label: "Today" },
  { href: "/live", label: "Live" },
  { href: "/predictions", label: "Board" },
  { href: "/history", label: "Track Record" },
  { href: "/performance", label: "Performance" },
  { href: "/fixtures", label: "Fixtures" },
  { href: "/how-it-works", label: "How It Works" },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="sticky top-0 z-50 border-b border-white/10 bg-slate-950/90 backdrop-blur-xl">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 px-3 py-3 sm:px-6 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-4">
            <Link href="/" className="text-xl font-black tracking-tight sm:text-2xl">
              LOYAL <span className="text-emerald-400">EDGE</span>
            </Link>
            <span className="hidden rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-300 sm:inline">
              by REEDS
            </span>
          </div>
          <div className="-mx-3 flex gap-2 overflow-x-auto px-3 pb-1 [scrollbar-width:none] md:mx-0 md:px-0 md:pb-0">
            {NAV.map((item) => (
              <Link key={item.href} className="glass-nav-link" href={item.href}>{item.label}</Link>
            ))}
            <Link className="glass-nav-link" href="/community-leaderboard">Tipsters</Link>
          </div>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
