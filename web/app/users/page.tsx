"use client";

import { useEffect, useState } from "react";
import { KeyRound, UserPlus } from "lucide-react";
import { StatusMessage } from "@/components/status-message";
import { api } from "@/lib/api";

type Role = "GENERAL" | "GENERAL_ACCOUNTING" | "OWNER_ADMIN";

type User = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
};

const roles: { value: Role; label: string }[] = [
  { value: "GENERAL", label: "General" },
  { value: "GENERAL_ACCOUNTING", label: "General + Accounting" },
  { value: "OWNER_ADMIN", label: "Main Owner Admin" },
];

export default function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<Role>("GENERAL");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function loadUsers() {
    return api<User[]>("/admin/users").then(setUsers);
  }

  useEffect(() => {
    loadUsers().catch(reason => setError(reason instanceof Error ? reason.message : "Unable to load users"));
  }, []);

  async function createUser(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    setTemporaryPassword("");
    try {
      const result = await api<{ user: User; temporary_password: string }>("/admin/users", {
        method: "POST",
        body: JSON.stringify({ email, full_name: fullName, role }),
      });
      setTemporaryPassword(result.temporary_password);
      setMessage(`${result.user.full_name} can now sign in. Share the temporary password securely.`);
      setEmail("");
      setFullName("");
      setRole("GENERAL");
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create user");
    } finally {
      setBusy(false);
    }
  }

  async function updateUser(userId: string, changes: { role?: Role; is_active?: boolean }) {
    setError("");
    setMessage("");
    try {
      await api<User>(`/admin/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify(changes),
      });
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update user");
    }
  }

  async function resetPassword(user: User) {
    setError("");
    setMessage("");
    setTemporaryPassword("");
    try {
      const result = await api<{ temporary_password: string }>(`/admin/users/${user.id}/reset-password`, { method: "POST" });
      setTemporaryPassword(result.temporary_password);
      setMessage(`Password reset for ${user.full_name}. Share the temporary password securely.`);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to reset password");
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <p className="eyebrow">Main Owner Admin</p>
        <h1>Users and access</h1>
        <p>Add people, assign one of the three access levels, reset passwords, or deactivate access.</p>
      </header>

      <StatusMessage error={error} success={message} />
      {temporaryPassword ? (
        <aside className="invariant-note" style={{ marginBottom: "20px" }}>
          <KeyRound size={20} />
          <div><strong>Temporary password — shown once</strong><p style={{ fontFamily: "var(--font-mono)" }}>{temporaryPassword}</p></div>
        </aside>
      ) : null}

      <form className="panel form-panel" style={{ marginBottom: "20px" }} onSubmit={createUser}>
        <div className="section-heading"><div><h2>Add person</h2><p>They must replace the temporary password at first login.</p></div><UserPlus /></div>
        <div className="field-row">
          <label>Full name<input value={fullName} onChange={event => setFullName(event.target.value)} required /></label>
          <label>Email<input type="email" value={email} onChange={event => setEmail(event.target.value)} required /></label>
        </div>
        <label>Access level<select value={role} onChange={event => setRole(event.target.value as Role)}>{roles.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <button className="primary-button" disabled={busy}><UserPlus size={17} /> {busy ? "Creating…" : "Create user"}</button>
      </form>

      <section className="panel" style={{ overflowX: "auto" }}>
        <table className="batch-table">
          <thead><tr><th>Person</th><th>Access level</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>
            {users.map(user => (
              <tr key={user.id}>
                <td><strong>{user.full_name}</strong><br /><small>{user.email}</small></td>
                <td><select value={user.role} onChange={event => updateUser(user.id, { role: event.target.value as Role })}>{roles.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></td>
                <td>{user.is_active ? (user.must_change_password ? "Password change required" : "Active") : "Inactive"}</td>
                <td style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                  <button className="table-btn" type="button" onClick={() => resetPassword(user)}>Reset password</button>
                  <button className="table-btn" type="button" onClick={() => updateUser(user.id, { is_active: !user.is_active })}>{user.is_active ? "Deactivate" : "Reactivate"}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
