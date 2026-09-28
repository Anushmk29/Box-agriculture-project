"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LockKeyhole } from "lucide-react";
import { api } from "@/lib/api";
import { StatusMessage } from "@/components/status-message";

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
      const destination = session.must_change_password
        ? "/change-password"
        : requestedPath?.startsWith("/") && !requestedPath.startsWith("//")
          ? requestedPath
          : "/";
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
    <main className="login-page">
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
            <input
              type="password"
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
