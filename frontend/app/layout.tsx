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
        <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <Link href="/" className="text-2xl font-black tracking-tight">
            LOYAL <span className="text-emerald-400">EDGE</span>
          </Link>
          <div className="flex gap-4 text-sm text-slate-300">
            <Link href="/predictions">Predictions</Link>
            <Link href="/combo">3-Combo</Link>
            <Link href="/stats">Stats</Link>
          </div>
        </nav>
        {children}
      </body>
    </html>
  );
}
