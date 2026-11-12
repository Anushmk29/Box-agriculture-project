"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect, useState } from "react";
import { Copy, KeyRound, Settings2, UserPlus } from "lucide-react";
import { RecordRegister, type RegisterRow } from "@/components/record-register";
import { PageHeader } from "@/components/page-header";
import { confirmAction } from "@/lib/confirm";
import { toast } from "@/lib/toast";
import { StatusMessage } from "@/components/status-message";
import { TaskDialog } from "@/components/task-dialog";
import { api } from "@/lib/api";

type Role = "GENERAL" | "GENERAL_ACCOUNTING" | "OWNER_ADMIN";

type User = {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  permissions: string[];
};

type AccessLevel = { role: Role; permissions: string[] };

const permissionLabels: Record<string, string> = {
  validate_orders: "Order intake",
  confirm_procurement: "Confirm purchases",
  dispatch_handoff_01: "Dispatch procurement handoff",
  return_handoff_02: "Return packing handoff",
  design_packing: "Packing design",
  floor_packing: "Packing floor and scanning",
  lock_final_pack: "Approve final packing",
  draft_invoice: "Draft invoices",
  manage_billing: "Billing and payments",
  manage_users: "Manage accounts",
  edit_buffers: "Edit procurement buffers",
  merge_products: "Merge products",
};

function permissionSummary(permissions: string[]) {
  return permissions.map(permission => t(permissionLabels[permission] ?? permission)).join(", ");
}

const roles: { value: Role; label: string }[] = [
  { value: "GENERAL", label: "General" },
  { value: "GENERAL_ACCOUNTING", label: "General + Accounting" },
  { value: "OWNER_ADMIN", label: "Owner admin" },
];

export default function UsersPage() {
  useLanguage();
  const [showCreateUser, setShowCreateUser] = useState(false);
  const [accessLevels, setAccessLevels] = useState<AccessLevel[]>([]);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<Role>("GENERAL");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [managedUser, setManagedUser] = useState<User | null>(null);

  const [refreshKey, setRefreshKey] = useState(0);
  function loadUsers() { setRefreshKey(value => value + 1); return Promise.resolve(); }

  useEffect(() => {
    api<AccessLevel[]>("/admin/users/access-levels").then(setAccessLevels)
      .catch(reason => setError(reason instanceof Error ? reason.message : "Unable to load access levels"));
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
      setShowCreateUser(false);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to create user");
    } finally {
      setBusy(false);
    }
  }

  async function changeRole(row: RegisterRow, next: Role) {
    const from = roles.find(option => option.value === row.role)?.label ?? String(row.role);
    const to = roles.find(option => option.value === next)?.label ?? next;
    if (!await confirmAction({ title: "Change access level?", body: `${row.full_name} will change from ${from} to ${to}. The new permissions apply immediately.`, confirmLabel: "Change access" })) return;
    await updateUser(String(row.id), { role: next });
  }

  async function updateUser(userId: string, changes: { role?: Role; is_active?: boolean }) {
    setError("");
    setMessage("");
    try {
      await api<User>(`/admin/users/${userId}`, {
        method: "PATCH",
        body: JSON.stringify(changes),
      });
      window.dispatchEvent(new Event("account-access-updated"));
      if (changes.role) setMessage("Access level updated. Permissions apply immediately.");
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to update user");
    }
  }

  async function resetPassword(user: User) {
    if (!await confirmAction({ title: `Reset password for ${user.full_name}?`, body: "Their current password stops working now. You will get a temporary password to share with them.", confirmLabel: "Reset password" })) return;
    setManagedUser(null);
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

  async function changeAccountStatus(user: User) {
    if (user.is_active && !await confirmAction({ title: `Deactivate ${user.full_name}?`, body: "They will be signed out and cannot sign in. Their account and history stay, and you can reactivate them later.", confirmLabel: "Deactivate account", tone: "danger" })) return;
    await updateUser(user.id, { is_active: !user.is_active });
    setManagedUser(null);
  }

  async function deleteUser(user: User) {
    if (!await confirmAction({ title: `Delete ${user.full_name}?`, body: "This permanently removes the account and cannot be undone. Accounts linked to past records must be deactivated instead.", confirmLabel: "Delete permanently", tone: "danger" })) return;
    setError("");
    setMessage("");
    try {
      await api<void>(`/admin/users/${user.id}`, { method: "DELETE" });
      setManagedUser(null);
      setMessage(`${user.full_name} was permanently deleted.`);
      await loadUsers();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to delete user");
    }
  }

  return (
    <div className="page users-workspace">
      <PageHeader eyebrow="Owner only" title={t("Team & access")} description={t("Add people, choose what they can open, and keep accounts secure.")}
        actions={<button type="button" className="primary-button" data-guide={t("Add a team member")} data-guide-detail={t("Enter their name and email, then choose the access needed for their work.")} onClick={() => setShowCreateUser(true)}><UserPlus size={17} aria-hidden="true" />{t("Add person")}</button>} />

      <StatusMessage error={error} success={message} />
      {temporaryPassword ? (
        <TaskDialog title={t("Temporary password")} description={t("Shown once. Share it privately — they must choose a new password at first sign-in.")} size="sm" onClose={() => setTemporaryPassword("")}>
          <div className="temp-password"><KeyRound size={20} aria-hidden /><code>{temporaryPassword}</code>
            <button type="button" className="invoice-secondary-button btn-sm" onClick={() => { void navigator.clipboard?.writeText(temporaryPassword).then(() => toast.success("Temporary password copied")); }}><Copy size={15} />{t("Copy")}</button></div>
          <div className="task-dialog-actions"><button type="button" className="primary-button" onClick={() => setTemporaryPassword("")}>{t("Done")}</button></div>
        </TaskDialog>
      ) : null}

      {showCreateUser && <TaskDialog title={t("Add person")} description={t("Create an account and choose what they can access. They must replace their temporary password at first login.")} busy={busy} onClose={() => setShowCreateUser(false)}>
      <StatusMessage error={error} />
      <form className="users-create-form" onSubmit={createUser}>
        <div className="field-row">
          <label>{t("Full name")}<input value={fullName} onChange={event => setFullName(event.target.value)} required /></label>
          <label>{t("Email")}<input type="email" value={email} onChange={event => setEmail(event.target.value)} required /></label>
        </div>
        <label>{t("Access level")}<select aria-label={t("Access level")} value={role} onChange={event => setRole(event.target.value as Role)}>{roles.map(option => <option key={option.value} value={option.value}>{t(option.label)}</option>)}</select></label>
        <p aria-live="polite"><strong>{t("Includes: ")}</strong>{permissionSummary(accessLevels.find(level => level.role === role)?.permissions ?? []) || t("Loading permissions…")}</p>
        <div className="task-dialog-actions"><button type="button" disabled={busy} onClick={() => setShowCreateUser(false)}>{t("Cancel")}</button><button className="primary-button" disabled={busy}><UserPlus size={17} /> {busy ? t("Creating…") : t("Create user")}</button></div>
      </form></TaskDialog>}

      <RecordRegister title={t("User accounts")} description={t("Choose an access level or manage an individual account.")} searchLabel={t("Find a person")} searchPlaceholder={t("Search by name or email")} endpoint="/admin/users/register" identity="id" views={false} filters={false} statuses={["active", "inactive"]} refreshKey={refreshKey}
        columns={[
          { key: "full_name", label: "Person", sort: "name", render: row => <div className="users-person"><strong>{row.full_name}</strong><small>{row.email}</small></div> },
          { key: "role", label: "Access level", render: row => <div className="users-role"><select aria-label={t(`Access level for ${row.full_name}`)} value={String(row.role)} onChange={event => { void changeRole(row, event.target.value as Role); }}>{roles.map(option => <option key={option.value} value={option.value}>{t(option.label)}</option>)}</select><details className="users-permissions"><summary>{t("View permissions")}</summary><ul>{((row.permissions as string[]) || []).map(permission => <li key={permission}>{t(permissionLabels[permission] ?? permission)}</li>)}</ul></details></div> },
          { key: "is_active", label: "Status", render: row => <div className="users-status"><span className={`chip ${row.is_active ? "chip-success" : "chip-danger"}`}>{row.is_active ? t("Active") : t("Inactive")}</span>{row.is_active && row.must_change_password && <small>{t("Password change required")}</small>}</div> },
          { key: "actions", label: "Actions", render: row => <div className="users-row-actions"><button type="button" className="invoice-secondary-button btn-sm" onClick={() => setManagedUser(row as unknown as User)}><Settings2 size={15} aria-hidden="true" />{t("Manage")}</button></div> },
        ]} />

      {managedUser ? (
        <TaskDialog title={t(`Manage ${managedUser.full_name}`)} description={managedUser.email} onClose={() => setManagedUser(null)}>
            <div className="manage-user">
              <section><h3>{t("Password")}</h3><p>{t("Give them a new temporary password if they are locked out.")}</p>
                <button className="invoice-secondary-button" type="button" onClick={() => resetPassword(managedUser)}><KeyRound size={16} aria-hidden="true" />{t("Reset password")}</button></section>
              <section><h3>{managedUser.is_active ? t("Deactivate access") : t("Reactivate access")}</h3>
              <p>{managedUser.is_active ? t("The person cannot sign in, but their account and activity history remain.") : t("The person can sign in again with their existing password.")}</p>
              <button className="invoice-secondary-button" type="button" onClick={() => changeAccountStatus(managedUser)}>
                {managedUser.is_active ? t("Deactivate account") : t("Reactivate account")}
              </button></section>
              <section className="manage-user-danger"><h3>{t("Delete permanently")}</h3>
              <p>{t("Removes the account. Accounts connected to past records must be deactivated instead.")}</p>
              <button className="danger-button" type="button" onClick={() => deleteUser(managedUser)}>{t("Delete permanently")}</button></section>
            </div>
        </TaskDialog>
      ) : null}
    </div>
  );
}
