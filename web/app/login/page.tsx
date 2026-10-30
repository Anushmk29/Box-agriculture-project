"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import { api } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";
import { PasswordInput } from "@/components/password-input";
import { safeDestination } from "@/lib/workflow";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
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
          <span>Agriculture is life<small>Packing &amp; export operations</small></span>
        </div>
        <blockquote>From the field to the pallet, every crate accounted for.</blockquote>
        <p>Order intake, procurement, packing, and billing for fresh herbs and produce shipped from Thailand.</p>
      </aside>

      <section className="login-card">
        <div>
          <h1>Sign in</h1>
          <p>Use the account your administrator created for you.</p>
          <p>Forgot your password? Ask the owner to reset it. A temporary password must be changed at first sign-in.</p>
        </div>

        <form onSubmit={submit}>
          <label>
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
            />
          </label>
          <label>
            Password
            <PasswordInput
              aria-label="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="current-password"
            />
          </label>
          <StatusMessage error={error} />
          <button className="primary-button" disabled={busy}>
            <LockKeyhole size={17} /> {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
