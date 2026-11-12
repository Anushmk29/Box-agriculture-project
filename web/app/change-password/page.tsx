"use client";

import { t, useLanguage } from "@/lib/language";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import { StatusMessage } from "@/components/status-message";
import { PageHeader } from "@/components/page-header";
import { PasswordInput } from "@/components/password-input";
import { api } from "@/lib/api";
import { safeDestination } from "@/lib/workflow";

export default function ChangePasswordPage() {
  useLanguage();
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const passwordLength = Array.from(newPassword).length;

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (passwordLength < 12 || passwordLength > 128) {
      setError("Choose a password with 12 to 128 characters.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      const next = safeDestination(new URLSearchParams(window.location.search).get("next"));
      router.replace(`/login?next=${encodeURIComponent(next)}`);
      router.refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to change password");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageHeader eyebrow="Account security" title={t("Choose a new password")} description={t("Replace your temporary password to start using the platform.")} />
      <form className="panel form-panel" style={{ maxWidth: "520px" }} onSubmit={submit}>
        <label>
          {t("Temporary password")}<PasswordInput aria-label={t("Temporary password")} autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} required />
        </label>
        <label>
          {t("New password")}<PasswordInput aria-label={t("New password")} autoComplete="new-password" minLength={12} value={newPassword} onChange={event => setNewPassword(event.target.value)} required aria-describedby="new-password-hint" />
          <small id="new-password-hint" className={`password-hint${passwordLength >= 12 && passwordLength <= 128 ? " is-ok" : ""}`} aria-live="polite">{passwordLength > 128 ? t("Use 128 characters or fewer") : passwordLength >= 12 ? t("Long enough") : t(`At least 12 characters · ${12 - passwordLength} to go`)}</small>
        </label>
        <StatusMessage error={error} />
        <button className="primary-button" disabled={busy}><LockKeyhole size={17} /> {busy ? t("Saving…") : t("Save password")}</button>
      </form>
    </div>
  );
}
