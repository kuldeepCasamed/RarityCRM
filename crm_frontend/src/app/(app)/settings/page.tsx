"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { useState } from "react";
import { toast } from "sonner";
import { api, TREATMENTS } from "@/lib/api";

type Me = { id: number; name: string; email: string; role: string; phone: string; timezone: string };
const ROLES = [["admin", "Admin"], ["manager", "Manager"], ["rep", "Sales Rep"], ["front_desk", "Front Desk"]];
const errMsg = (e: any) => {
  const d = e?.response?.data;
  if (!d) return "Something went wrong";
  return typeof d === "string" ? d : d.detail ?? Object.values(d).flat().join(" ");
};

export default function SettingsPage() {
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: async () => (await api.get<Me>("/me/")).data });
  const isManager = me?.role === "admin" || me?.role === "manager";
  const tabs = ["Profile", ...(isManager ? ["Team", "Notifications", "Clinic", "Price list"] : [])];
  const [tab, setTab] = useState("Profile");
  if (!me) return <p className="text-stone-500">Loading…</p>;

  return (
    <div className="space-y-4 max-w-4xl">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm ${tab === t ? "border-b-2 border-brand text-brand font-medium" : "text-stone-500"}`}>{t}</button>
        ))}
      </div>
      {tab === "Profile" && <Profile me={me} />}
      {tab === "Team" && isManager && <Team me={me} />}
      {tab === "Notifications" && isManager && <Notifications />}
      {tab === "Clinic" && isManager && <Clinic />}
      {tab === "Price list" && isManager && <PriceList />}
    </div>
  );
}

const input = "w-full border rounded-md px-3 py-2 bg-white text-sm";

function Profile({ me }: { me: Me }) {
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: (b: object) => api.patch("/me/", b),
    onSuccess: () => { toast.success("Profile saved"); qc.invalidateQueries({ queryKey: ["me"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  async function changePassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const f = new FormData(form);
    if (f.get("new_password") !== f.get("confirm")) return toast.error("Passwords do not match");
    const res = await fetch("/api/auth/password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: f.get("current_password"), new_password: f.get("new_password") }),
    });
    if (res.ok) { toast.success("Password changed"); form.reset(); }
    else { const d = await res.json(); toast.error(Object.values(d).flat().join(" ")); }
  }
  return (
    <div className="grid md:grid-cols-2 gap-6">
      <form className="bg-white border rounded-lg p-4 space-y-3" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); save.mutate({ phone: f.get("phone"), timezone: f.get("timezone") }); }}>
        <h2 className="font-medium">Profile</h2>
        <p className="text-sm text-stone-500">{me.name} · {me.email} · {me.role.replace("_", " ")}</p>
        <label className="text-sm block">Phone<input name="phone" defaultValue={me.phone} className={input} /></label>
        <label className="text-sm block">Timezone<input name="timezone" defaultValue={me.timezone} className={input} placeholder="Asia/Kolkata" /></label>
        <button className="bg-brand text-white rounded-md px-4 py-2 text-sm">Save</button>
      </form>
      <form className="bg-white border rounded-lg p-4 space-y-3" onSubmit={changePassword}>
        <h2 className="font-medium">Change password</h2>
        <input name="current_password" type="password" placeholder="Current password" required className={input} />
        <input name="new_password" type="password" minLength={8} placeholder="New password" required className={input} />
        <input name="confirm" type="password" placeholder="Confirm new password" required className={input} />
        <button className="bg-brand text-white rounded-md px-4 py-2 text-sm">Update password</button>
      </form>
    </div>
  );
}

function Team({ me }: { me: Me }) {
  const qc = useQueryClient();
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: async () => (await api.get<any[]>("/users/")).data });
  const { data: invites } = useQuery({ queryKey: ["invites"], queryFn: async () => (await api.get<any[]>("/team/invites/")).data });
  const [link, setLink] = useState("");

  const patch = useMutation({
    mutationFn: ({ id, ...b }: any) => api.patch(`/users/${id}/`, b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["users"] }),
    onError: (e) => toast.error(errMsg(e)),
  });
  const invite = useMutation({
    mutationFn: (b: object) => api.post("/team/invite/", b),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ["invites"] });
      setLink(`${window.location.origin}/invite/${r.data.token}`);
      toast.success("Invite sent");
    },
    onError: (e) => toast.error(errMsg(e)),
  });
  const revoke = useMutation({ mutationFn: (id: number) => api.delete(`/team/invites/${id}/`), onSuccess: () => qc.invalidateQueries({ queryKey: ["invites"] }) });
  const resend = useMutation({ mutationFn: (id: number) => api.post(`/team/invites/${id}/`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["invites"] }); toast.success("Invite re-sent"); } });
  const roles = me.role === "admin" ? ROLES : ROLES.filter(([v]) => v !== "admin");

  return (
    <div className="space-y-6">
      <form className="bg-white border rounded-lg p-4 flex flex-wrap gap-2 items-end" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); invite.mutate({ email: f.get("email"), role: f.get("role") }); e.currentTarget.reset(); }}>
        <label className="text-sm flex-1 min-w-48">Invite by email<input name="email" type="email" required className={input} placeholder="name@raritydental.com" /></label>
        <label className="text-sm">Role<select name="role" defaultValue="rep" className={input}>{roles.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <button className="bg-brand text-white rounded-md px-4 py-2 text-sm">Send invite</button>
        {link && (
          <p className="w-full text-xs text-stone-500 break-all">Share manually if email isn't configured: <code className="select-all">{link}</code></p>
        )}
      </form>

      {!!invites?.length && (
        <section>
          <h2 className="font-medium mb-2">Pending invites</h2>
          <div className="space-y-2">
            {invites.map((i) => (
              <div key={i.id} className="bg-white border rounded-lg p-3 text-sm flex items-center gap-3">
                <span>{i.email}</span><span className="text-stone-400">{i.role}</span>
                <span className={i.expired ? "text-red-600" : "text-stone-400"}>{i.expired ? "expired" : `expires ${format(new Date(i.expires_at), "d MMM")}`}</span>
                <button onClick={() => resend.mutate(i.id)} className="ml-auto text-brand">Resend</button>
                <button onClick={() => revoke.mutate(i.id)} className="text-red-600">Revoke</button>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2 className="font-medium mb-2">Team members</h2>
        <div className="bg-white border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left text-stone-500"><tr><th className="p-3">Name</th><th>Role</th><th>Daily limit</th><th>Auto-assign</th><th>Active</th></tr></thead>
            <tbody>
              {users?.map((u) => {
                const self = u.id === me.id;
                return (
                  <tr key={u.id} className="border-t">
                    <td className="p-3">{u.name}<div className="text-xs text-stone-400">{u.email}</div></td>
                    <td><select disabled={self} value={u.role} onChange={(e) => patch.mutate({ id: u.id, role: e.target.value })} className="border rounded px-2 py-1 bg-white">
                      {ROLES.map(([v, l]) => <option key={v} value={v} disabled={v === "admin" && me.role !== "admin"}>{l}</option>)}</select></td>
                    <td><input type="number" min={0} defaultValue={u.daily_lead_limit} className="w-20 border rounded px-2 py-1" title="0 = unlimited"
                      onBlur={(e) => Number(e.target.value) !== u.daily_lead_limit && patch.mutate({ id: u.id, daily_lead_limit: Number(e.target.value) })} /></td>
                    <td><input type="checkbox" checked={u.receives_auto_assign} onChange={(e) => patch.mutate({ id: u.id, receives_auto_assign: e.target.checked })} /></td>
                    <td><input type="checkbox" disabled={self} checked={u.is_active} onChange={(e) => patch.mutate({ id: u.id, is_active: e.target.checked })} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function Notifications() {
  const qc = useQueryClient();
  const { data: recipients } = useQuery({ queryKey: ["recipients"], queryFn: async () => (await api.get<any[]>("/notifications/recipients/")).data });
  const { data: status } = useQuery({ queryKey: ["notif-status"], queryFn: async () => (await api.get("/notifications/test/")).data });
  const add = useMutation({
    mutationFn: (b: object) => api.post("/notifications/recipients/", b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["recipients"] }); qc.invalidateQueries({ queryKey: ["notif-status"] }); },
    onError: (e) => toast.error(errMsg(e)),
  });
  const toggle = useMutation({ mutationFn: (r: any) => api.patch(`/notifications/recipients/${r.id}/`, { is_active: !r.is_active }), onSuccess: () => qc.invalidateQueries({ queryKey: ["recipients"] }) });
  const del = useMutation({ mutationFn: (id: number) => api.delete(`/notifications/recipients/${id}/`), onSuccess: () => qc.invalidateQueries({ queryKey: ["recipients"] }) });
  const test = useMutation({ mutationFn: () => api.post("/notifications/test/"), onSuccess: (r) => toast.info(r.data.result), onError: (e) => toast.error(errMsg(e)) });

  return (
    <div className="space-y-4">
      <div className="bg-white border rounded-lg p-4 text-sm flex items-center gap-3">
        <span>Email sending: {status ? (status.email_configured ? <b className="text-green-700">configured</b> : <b className="text-amber-600">RESEND_API_KEY missing (emails are skipped)</b>) : "…"}</span>
        <button onClick={() => test.mutate()} className="ml-auto border rounded-md px-3 py-1.5 bg-white">Send test email</button>
      </div>
      <p className="text-sm text-stone-500">New-lead, stale-lead digest and other manager alerts go to these addresses. If the list is empty, the server falls back to <code>CRM_MANAGER_EMAIL</code>.</p>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); add.mutate({ channel: "email", value: f.get("value"), label: f.get("label") }); e.currentTarget.reset(); }}>
        <input name="value" type="email" required placeholder="email@raritydental.com" className={input} />
        <input name="label" placeholder="Label (e.g. Marketing)" className={input} />
        <button className="bg-brand text-white rounded-md px-4 text-sm">Add</button>
      </form>
      {recipients?.map((r) => (
        <div key={r.id} className="bg-white border rounded-lg p-3 text-sm flex items-center gap-3">
          <span>{r.value}</span><span className="text-stone-400">{r.label}</span>
          <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={r.is_active} onChange={() => toggle.mutate(r)} /> active</label>
          <button onClick={() => del.mutate(r.id)} className="text-red-600">Delete</button>
        </div>
      ))}
    </div>
  );
}

function Clinic() {
  const qc = useQueryClient();
  const { data: branches } = useQuery({ queryKey: ["branches"], queryFn: async () => (await api.get<any[]>("/branches/")).data });
  const { data: doctors } = useQuery({ queryKey: ["doctors"], queryFn: async () => (await api.get<any[]>("/doctors/")).data });
  const invB = () => qc.invalidateQueries({ queryKey: ["branches"] });
  const invD = () => qc.invalidateQueries({ queryKey: ["doctors"] });
  const addBranch = useMutation({ mutationFn: (b: object) => api.post("/branches/", b), onSuccess: invB, onError: (e) => toast.error(errMsg(e)) });
  const addDoctor = useMutation({ mutationFn: (b: object) => api.post("/doctors/", b), onSuccess: invD, onError: (e) => toast.error(errMsg(e)) });
  const toggleB = useMutation({ mutationFn: (b: any) => api.patch(`/branches/${b.id}/`, { is_active: !b.is_active }), onSuccess: invB });
  const toggleD = useMutation({ mutationFn: (d: any) => api.patch(`/doctors/${d.id}/`, { is_active: !d.is_active }), onSuccess: invD });

  return (
    <div className="grid md:grid-cols-2 gap-6">
      <section className="space-y-3">
        <h2 className="font-medium">Branches</h2>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); addBranch.mutate({ name: f.get("name"), address: f.get("address") }); e.currentTarget.reset(); }}>
          <input name="name" required placeholder="Branch name" className={input} />
          <input name="address" placeholder="Address" className={input} />
          <button className="bg-brand text-white rounded-md px-4 text-sm">Add</button>
        </form>
        {branches?.map((b) => (
          <div key={b.id} className="bg-white border rounded-lg p-3 text-sm flex items-center">
            <div><div className={b.is_active ? "" : "line-through text-stone-400"}>{b.name}</div><div className="text-xs text-stone-400">{b.address}</div></div>
            <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={b.is_active} onChange={() => toggleB.mutate(b)} /> active</label>
          </div>
        ))}
      </section>
      <section className="space-y-3">
        <h2 className="font-medium">Doctors</h2>
        <form className="flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); addDoctor.mutate({ name: f.get("name"), speciality: f.get("speciality"), branch: f.get("branch") || null, calendar_id: f.get("calendar_id") }); e.currentTarget.reset(); }}>
          <input name="name" required placeholder="Doctor name" className={input} />
          <input name="speciality" placeholder="Speciality" className={input} />
          <select name="branch" className={input}><option value="">No branch</option>{branches?.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select>
          <input name="calendar_id" placeholder="Google Calendar ID (optional)" className={input} />
          <button className="bg-brand text-white rounded-md px-4 py-2 text-sm">Add doctor</button>
        </form>
        {doctors?.map((d) => (
          <div key={d.id} className="bg-white border rounded-lg p-3 text-sm flex items-center">
            <div><div className={d.is_active ? "" : "line-through text-stone-400"}>{d.name}</div><div className="text-xs text-stone-400">{d.speciality}</div></div>
            <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={d.is_active} onChange={() => toggleD.mutate(d)} /> active</label>
          </div>
        ))}
      </section>
    </div>
  );
}


function PriceList() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["catalog"], queryFn: async () => (await api.get<any[]>("/treatment-catalog/")).data });
  const inv = () => qc.invalidateQueries({ queryKey: ["catalog"] });
  const add = useMutation({ mutationFn: (b: object) => api.post("/treatment-catalog/", b), onSuccess: () => { inv(); toast.success("Added"); }, onError: (e) => toast.error(errMsg(e)) });
  const patch = useMutation({ mutationFn: ({ id, ...b }: any) => api.patch(`/treatment-catalog/${id}/`, b), onSuccess: inv, onError: (e) => toast.error(errMsg(e)) });
  const del = useMutation({ mutationFn: (id: number) => api.delete(`/treatment-catalog/${id}/`), onSuccess: inv });
  return (
    <div className="space-y-4">
      <p className="text-sm text-stone-500">Default treatment prices (₹). Reps pick from this list when building a quote and can still change the price per quote. Past quotes are never affected by edits here.</p>
      <form className="bg-white border rounded-lg p-3 flex flex-wrap gap-2 items-end" onSubmit={(e) => { e.preventDefault(); const f = new FormData(e.currentTarget); add.mutate({ name: f.get("name"), category: f.get("category"), default_price: f.get("price") }); e.currentTarget.reset(); }}>
        <label className="text-xs flex-1 min-w-48">Treatment<input name="name" required className={input + " mt-1"} placeholder="e.g. Dental implant (single)" /></label>
        <label className="text-xs">Category<select name="category" className={input + " mt-1"}><option value="">—</option>{TREATMENTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        <label className="text-xs">Price (₹)<input name="price" type="number" min={0} step="0.01" required className={input + " mt-1 w-32"} /></label>
        <button className="bg-brand text-white rounded-md px-4 py-2 text-sm">Add</button>
      </form>
      <div className="bg-white border rounded-lg divide-y">
        {data?.map((c) => (
          <div key={c.id} className="p-2 px-3 text-sm flex items-center gap-3">
            <span className={`flex-1 ${c.is_active ? "" : "line-through text-stone-400"}`}>{c.name}</span>
            <span className="text-xs text-stone-400">{c.category}</span>
            <input type="number" min={0} step="0.01" defaultValue={c.default_price} className="w-28 border rounded px-2 py-1" onBlur={(e) => e.target.value !== String(c.default_price) && patch.mutate({ id: c.id, default_price: e.target.value })} />
            <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={c.is_active} onChange={() => patch.mutate({ id: c.id, is_active: !c.is_active })} /> active</label>
            <button onClick={() => confirm(`Delete "${c.name}" from the price list?`) && del.mutate(c.id)} className="text-red-600 text-xs">Delete</button>
          </div>
        ))}
        {data?.length === 0 && <p className="p-4 text-sm text-stone-400">No treatments yet.</p>}
      </div>
    </div>
  );
}
