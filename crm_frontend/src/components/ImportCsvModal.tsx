"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";

type Result = { created: number; duplicates: number; errors: { row: number; error: string }[] };
const TEMPLATE = "name,phone,email,country\nAsha Verma,+919810012345,asha@example.com,India\n";

export default function ImportCsvModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");

  const upload = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("file", file!);
      return (await api.post<Result>("/leads/import/", fd)).data;
    },
    onSuccess: (r) => { setResult(r); setError(""); qc.invalidateQueries({ queryKey: ["leads"] }); },
    onError: (e) => setError(errMsg(e)),
  });

  const download = (name: string, text: string) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    a.download = name;
    a.click();
  };

  return (
    <div className="fixed inset-0 bg-black/40 grid place-items-center z-10" onClick={onClose}>
      <div className="bg-white rounded-xl p-6 w-full max-w-lg space-y-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center"><h2 className="font-semibold">Import leads from CSV</h2><button onClick={onClose} className="text-stone-400">✕</button></div>
        <p className="text-sm text-stone-600">Columns: <code>name</code> (required), <code>phone</code> or <code>email</code> (at least one), <code>country</code>. Duplicates (same phone/email) are skipped. Max 2 MB / 5,000 rows.</p>
        <button onClick={() => download("leads-template.csv", TEMPLATE)} className="text-sm text-brand underline">Download template</button>
        {!result && (
          <>
            <input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(""); }} className="block text-sm" />
            {error && <p className="text-sm text-red-600">{error}</p>}
            <button disabled={!file || upload.isPending} onClick={() => upload.mutate()} className="bg-brand text-white rounded-md px-4 py-2 text-sm disabled:opacity-50">
              {upload.isPending ? "Importing…" : "Import"}
            </button>
          </>
        )}
        {result && (
          <div className="space-y-3 text-sm">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="bg-green-50 rounded-lg p-3"><div className="text-xl font-semibold text-green-700">{result.created}</div>created</div>
              <div className="bg-stone-100 rounded-lg p-3"><div className="text-xl font-semibold">{result.duplicates}</div>duplicates skipped</div>
              <div className="bg-red-50 rounded-lg p-3"><div className="text-xl font-semibold text-red-700">{result.errors.length}</div>rows failed</div>
            </div>
            {result.errors.length > 0 && (
              <>
                <ul className="max-h-40 overflow-y-auto border rounded-md divide-y">{result.errors.map((e) => <li key={e.row} className="px-3 py-1.5">Row {e.row}: {e.error}</li>)}</ul>
                <button onClick={() => download("import-errors.csv", "row,error\n" + result.errors.map((e) => `${e.row},"${e.error.replace(/"/g, '""')}"`).join("\n"))} className="text-brand underline">Download error report</button>
              </>
            )}
            <button onClick={onClose} className="bg-brand text-white rounded-md px-4 py-2">Done</button>
          </div>
        )}
      </div>
    </div>
  );
}
