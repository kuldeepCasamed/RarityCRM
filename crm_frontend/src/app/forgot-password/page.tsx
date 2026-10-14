"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

const COOLDOWN = 60;

async function call(step: "request" | "confirm", body: object) {
  const res = await fetch(`/api/auth/password-reset/${step}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
}

function message(d: Record<string, unknown>) {
  if (typeof d.detail === "string") return d.detail;
  const flat = Object.values(d).flat().filter((x) => typeof x === "string");
  return flat.join(" ") || "Something went wrong. Please try again.";
}

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [step, setStep] = useState<"identify" | "reset">("identify");
  const [identifier, setIdentifier] = useState("");
  const [busy, setBusy] = useState(false);
  const [wait, setWait] = useState(0);

  useEffect(() => {
    if (wait <= 0) return;
    const t = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(t);
  }, [wait]);

  async function sendCode(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    const { ok, data } = await call("request", { identifier });
    setBusy(false);
    if (!ok) return toast.error(message(data));
    toast.success("If that account exists, a code is on its way. Check your inbox and spam.");
    setWait(COOLDOWN);
    setStep("reset");
  }

  async function reset(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    if (f.get("new_password") !== f.get("confirm")) return toast.error("Passwords do not match");
    setBusy(true);
    const { ok, data } = await call("confirm", { identifier, code: f.get("code"), new_password: f.get("new_password") });
    setBusy(false);
    if (!ok) return toast.error(message(data));
    toast.success("Password updated. Sign in with your new password.");
    router.push("/login");
  }

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <div className="w-full max-w-sm bg-white rounded-xl shadow-sm border border-stone-200 p-6 space-y-4">
        <h1 className="text-xl font-semibold text-brand">Reset your password</h1>
        {step === "identify" ? (
          <form onSubmit={sendCode} className="space-y-3">
            <p className="text-sm text-stone-600">Enter your username or email. We will email you a 6-digit code.</p>
            <input value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="Username or email" required autoFocus
              className="w-full border rounded-md px-3 py-2" />
            <button disabled={busy} className="w-full bg-brand text-white rounded-md py-2 disabled:opacity-60">
              {busy ? "Sending…" : "Send code"}
            </button>
          </form>
        ) : (
          <form onSubmit={reset} className="space-y-3">
            <p className="text-sm text-stone-600">Enter the 6-digit code we emailed to you (valid for 10 minutes) and choose a new password.</p>
            <input name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} placeholder="6-digit code" required autoFocus
              className="w-full border rounded-md px-3 py-2 tracking-widest text-center text-lg" />
            <input name="new_password" type="password" minLength={8} autoComplete="new-password" placeholder="New password (min 8 chars)" required
              className="w-full border rounded-md px-3 py-2" />
            <input name="confirm" type="password" autoComplete="new-password" placeholder="Confirm new password" required
              className="w-full border rounded-md px-3 py-2" />
            <button disabled={busy} className="w-full bg-brand text-white rounded-md py-2 disabled:opacity-60">
              {busy ? "Updating…" : "Update password"}
            </button>
            <button type="button" disabled={busy || wait > 0} onClick={() => sendCode()}
              className="w-full text-sm text-stone-600 disabled:opacity-60">
              {wait > 0 ? `Send a new code in ${wait}s` : "Send a new code"}
            </button>
          </form>
        )}
        <Link href="/login" className="block text-center text-sm text-brand">Back to sign in</Link>
      </div>
    </div>
  );
}
