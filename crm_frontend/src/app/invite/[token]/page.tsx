"use client";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import { toast } from "sonner";

export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const [invite, setInvite] = useState<{ email: string; role: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/invite/${token}`).then(async (r) => {
      const d = await r.json();
      if (r.ok) setInvite(d);
      else setError(Array.isArray(d) ? d[0] : d.detail ?? "This invite link is not valid.");
    });
  }, [token]);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (f.get("password") !== f.get("confirm")) return toast.error("Passwords do not match");
    setBusy(true);
    const res = await fetch(`/api/invite/${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ first_name: f.get("first_name"), last_name: f.get("last_name"), password: f.get("password") }),
    });
    setBusy(false);
    if (res.ok) { toast.success("Welcome to Rarity CRM"); router.push("/dashboard"); return; }
    const d = await res.json();
    toast.error(Object.values(d).flat().join(" ") || "Could not accept invite");
  }

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <div className="w-full max-w-sm bg-white rounded-xl shadow-sm border border-stone-200 p-6 space-y-4">
        <h1 className="text-xl font-semibold text-brand">Join Rarity CRM</h1>
        {error && <p className="text-sm text-red-600">{error}</p>}
        {!invite && !error && <p className="text-sm text-stone-500">Checking invite…</p>}
        {invite && (
          <form onSubmit={submit} className="space-y-3">
            <p className="text-sm text-stone-600">Signing up as <b>{invite.email}</b> ({invite.role.replace("_", " ")}).</p>
            <input name="first_name" placeholder="First name" required className="w-full border rounded-md px-3 py-2" />
            <input name="last_name" placeholder="Last name" className="w-full border rounded-md px-3 py-2" />
            <input name="password" type="password" minLength={8} placeholder="Password (min 8 chars)" required className="w-full border rounded-md px-3 py-2" />
            <input name="confirm" type="password" placeholder="Confirm password" required className="w-full border rounded-md px-3 py-2" />
            <button disabled={busy} className="w-full bg-brand text-white rounded-md py-2 disabled:opacity-60">{busy ? "Creating…" : "Create account"}</button>
          </form>
        )}
      </div>
    </div>
  );
}
