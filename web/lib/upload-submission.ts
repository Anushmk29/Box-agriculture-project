export type UploadDetails = {
  channel: "matrix" | "universal"; filename: string; sha256: string; size: number;
  shipment_date: string; route_line: string; main_customer_id: string;
};

export async function fingerprintUpload(file: File) {
  if (!file.size || file.size > 25 * 1024 * 1024) throw new Error("Choose a nonempty order file up to 25 MB.");
  const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return { filename: file.name, size: file.size,
    sha256: Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("") };
}

export function requireOriginalUpload(saved: UploadDetails, actual: Awaited<ReturnType<typeof fingerprintUpload>>) {
  if (saved.filename !== actual.filename || saved.size !== actual.size || saved.sha256 !== actual.sha256) {
    throw new Error("Select the original file, with the same name and contents, to confirm the pending upload.");
  }
}
