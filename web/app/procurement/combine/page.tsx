"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { RecordRegister, type RegisterRow } from "@/components/record-register";
import { fileColumns } from "@/lib/register-columns";
import { api } from "@/lib/api";
import { ArrowLeft } from "lucide-react";
import { HintedButton } from "@/components/disabled-hint";
import { StatusMessage } from "@/components/status-message";
import { toast } from "@/lib/toast";

export default function CombineFiles() {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const router = useRouter();
  async function combine(rows: RegisterRow[]) {
    if (new Set(rows.map(row => row.shipment_date)).size !== 1) { setError("Choose files with the same shipment date."); return; }
    setBusy(true); setError("");
    try { const result = await api<{ batch_id: string }>("/procurement/combine-and-consolidate", { method: "POST", body: JSON.stringify({ raw_file_ids: rows.map(row => row.raw_file_id) }) }); toast.success(`${rows.length} files combined into one purchase batch.`); router.push(`/procurement?batchId=${encodeURIComponent(result.batch_id)}`); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to combine files"); }
    finally { setBusy(false); }
  }
  return <div className="page"><Link className="ghost-button" href="/procurement/batches"><ArrowLeft size={16} aria-hidden />Back to batches</Link><StatusMessage error={error} /><RecordRegister title="Combine order files" description="Select complete files for the same shipment date. Combining replaces their source batches to prevent duplicate buying." endpoint="/procurement/source-files" identity="raw_file_id" views={false}
    emptyMessage="No order files are ready to combine." columns={fileColumns.filter(column => !["status", "unresolved_rows"].includes(column.key))}
    defaultSort="shipment_date" defaultDirection="asc"
    selectable={() => !busy} selectionAction={rows => <div className="combine-review">
      {rows.length > 0 && <><p><strong>Review selection:</strong> {rows.map(row => `${row.customer} · ${row.file_name} · ${row.shipment_date}`).join("; ")}</p><p>The source purchase batches will be replaced by one combined batch.</p></>}
      {new Set(rows.map(row => row.shipment_date)).size > 1 && <p role="alert">Selected files have different shipment dates. Remove incompatible files before combining.</p>}
      <HintedButton disabled={busy || rows.length < 2 || rows.length > 500 || new Set(rows.map(row => row.shipment_date)).size !== 1} reason={rows.length < 2 ? "Select 2 or more files with the same shipment date" : "Selected files must share one shipment date"} onClick={() => combine(rows)}>{busy ? "Combining…" : `Combine ${rows.length} files`}</HintedButton>
    </div>} /></div>;
}
