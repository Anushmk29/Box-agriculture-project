"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import { StatusMessage } from "@/components/status-message";
import { api } from "@/lib/api";

export default function ChangePasswordPage() {
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      router.replace("/");
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to change password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <p className="eyebrow">Account security</p>
        <h1>Choose a new password</h1>
        <p>Your temporary password must be replaced before you can use the platform.</p>
      </header>
      <form className="panel form-panel" style={{ maxWidth: "520px" }} onSubmit={submit}>
        <label>
          Temporary password
          <input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} required />
        </label>
        <label>
          New password
          <input type="password" autoComplete="new-password" minLength={12} value={newPassword} onChange={event => setNewPassword(event.target.value)} required />
        </label>
        <StatusMessage error={error} />
        <button className="primary-button" disabled={busy}><LockKeyhole size={17} /> {busy ? "Saving…" : "Save password"}</button>
      </form>
    </div>
  );
}
