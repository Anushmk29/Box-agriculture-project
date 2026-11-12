"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";
import { PasswordInput } from "@/components/password-input";
import { safeDestination } from "@/lib/workflow";
import { LanguageSwitcher } from "@/components/language-switcher";

export default function LoginPage() {
  useLanguage();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retryUntil, setRetryUntil] = useState(0);
  const [retrySeconds, setRetrySeconds] = useState(0);
  useEffect(() => {
    if (!retryUntil) return;
    function refresh() {
      const seconds = Math.max(0, Math.ceil((retryUntil - Date.now()) / 1000));
      setRetrySeconds(seconds);
      if (!seconds) { setRetryUntil(0); setError(""); }
    }
    const timer = window.setInterval(refresh, 1000);
    document.addEventListener("visibilitychange", refresh);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", refresh); };
  }, [retryUntil]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || retrySeconds > 0) return;
    setBusy(true);
    setError("");
    try {
      const session = await api<{ must_change_password: boolean }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      const requestedPath = new URLSearchParams(window.location.search).get(
        "next",
      );
      const next = safeDestination(requestedPath);
      const destination = session.must_change_password ? `/change-password?next=${encodeURIComponent(next)}` : next;
      router.replace(destination);
      router.refresh();
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 429 && reason.retryAfterSeconds) {
        setRetrySeconds(reason.retryAfterSeconds);
        setRetryUntil(Date.now() + reason.retryAfterSeconds * 1000);
      }
      setError(
        reason instanceof Error ? reason.message : "Unable to sign in",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-page" id="workspace-content">
      <aside className="login-aside">
        <div className="brand">
          <span className="brand-mark">
            <img src="/logo.png" alt="" />
          </span>
          <span>{t("Agriculture is life")}<small>{t("Packing & export operations")}</small></span>
        </div>
        <blockquote>{t("From the field to the pallet, every crate accounted for.")}</blockquote>
        <p>{t("Order intake, procurement, packing, and billing for fresh herbs and produce shipped from Thailand.")}</p>
      </aside>

      <section className="login-card">
        <LanguageSwitcher />
        <div>
          <h1>{t("Sign in")}</h1>
          <p>{t("Use the account your administrator created for you.")}</p>
          <p>{t("Forgot your password? Ask the owner to reset it. A temporary password must be changed at first sign-in.")}</p>
        </div>

        <form onSubmit={submit}>
          <label>
            {t("Email")}<input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </label>
          <label>
            {t("Password")}<PasswordInput
              aria-label={t("Password")}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </label>
          <StatusMessage error={error} />
          {retrySeconds > 0 && <p role="timer" aria-live="off">{t(`Try again in ${Math.floor(retrySeconds / 60)}:${String(retrySeconds % 60).padStart(2, "0")}.`)}</p>}
          <button className="primary-button" disabled={busy || retrySeconds > 0}>
            <LockKeyhole size={17} /> {busy ? t("Signing in…") : t("Sign in")}
          </button>
        </form>
      </section>
    </main>
  );
}
