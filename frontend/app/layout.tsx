import Link from "next/link";
import "./globals.css";

export const metadata = {
  title: "LOYAL EDGE — Transparent Sports Prediction Intelligence",
  description: "Football and basketball predictions with risk ratings, model validation, and transparent performance tracking.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <nav className="sticky top-0 z-50 border-b border-white/10 bg-slate-950/75 backdrop-blur-xl">
          <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:px-6 md:flex-row md:items-center md:justify-between">
          <Link href="/" className="text-xl font-black tracking-tight sm:text-2xl">
            LOYAL <span className="text-emerald-400">EDGE</span>
          </Link>
          <div className="flex gap-2 overflow-x-auto pb-1 md:pb-0">
            <Link className="glass-nav-link" href="/predictions">AI Picks</Link>
            <Link className="glass-nav-link" href="/fixtures">Fixtures</Link>
            <Link className="glass-nav-link" href="/community-leaderboard">Community</Link>
            <Link className="glass-nav-link" href="/predictions/submit">+ Post</Link>
            <Link className="glass-nav-link" href="/combo">Combo</Link>
            <Link className="glass-nav-link" href="/stats">Stats</Link>
          </div>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
