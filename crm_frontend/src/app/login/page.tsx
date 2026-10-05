"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

export default function LoginPage() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    const f = new FormData(e.currentTarget);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: f.get("username"), password: f.get("password") }),
    });
    setBusy(false);
    if (res.ok) router.push("/dashboard");
    else toast.error((await res.json()).detail ?? "Login failed");
  }

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm bg-white rounded-xl shadow-sm border border-stone-200 p-6 space-y-4">
        <h1 className="text-xl font-semibold text-brand">Rarity CRM</h1>
        <input name="username" placeholder="Username" required className="w-full border rounded-md px-3 py-2" />
        <input name="password" type="password" placeholder="Password" required className="w-full border rounded-md px-3 py-2" />
        <button disabled={busy} className="w-full bg-brand text-white rounded-md py-2 disabled:opacity-60">
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
