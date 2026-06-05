export default function Admin() {
  return <main className="mx-auto max-w-4xl px-6 py-10"><h1 className="text-4xl font-black">Admin</h1><div className="mt-8 card"><p className="text-slate-300">Use backend admin endpoints with your private <code>X-Admin-Key</code> header:</p><pre className="mt-4 overflow-auto rounded-xl bg-black p-4 text-xs text-emerald-300">POST /api/admin/train{"\n"}POST /api/admin/predict</pre><p className="mt-4 text-sm text-slate-500">For security, this MVP does not expose admin keys in the browser.</p></div></main>;
}
