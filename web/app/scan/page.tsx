"use client";

import { t, useLanguage } from "@/lib/language";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { Camera, QrCode, ScanLine } from "lucide-react";
import { CameraScanner } from "@/components/camera-scanner";
import { PageHeader } from "@/components/page-header";

function stickerCode(value: string) {
  const input = value.trim();
  if (!input) return "";
  try {
    const url = new URL(input);
    return decodeURIComponent(url.pathname.split("/").filter(Boolean).at(-1) || "");
  } catch {
    return decodeURIComponent(input.split("/").filter(Boolean).at(-1) || "");
  }
}

export default function ScanStickerPage() {
  useLanguage();
  const router = useRouter();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [cameraOpen, setCameraOpen] = useState(false);
  const openScanned = useCallback((raw: string) => {
    setCameraOpen(false);
    try { const code = stickerCode(raw); if (code) { router.push(`/scan/${encodeURIComponent(code)}`); return; } } catch { /* fall through */ }
    setError("That QR code is not a box sticker. Try again or type the printed code.");
  }, [router]);

  function openSticker(event: React.FormEvent) {
    event.preventDefault();
    let code: string;
    try { code = stickerCode(value); }
    catch { setError("This code could not be read. Copy the original printed code or scan the sticker again."); return; }
    if (!code) {
      setError("Enter the sticker code printed below the QR image.");
      return;
    }
    router.push(`/scan/${encodeURIComponent(code)}`);
  }

  return (
    <div className="page scan-entry-page">
      <PageHeader eyebrow="Packing floor" title={t("Scan a box sticker")} description={t("Point your phone camera at the QR image, or type the code printed under it.")} />
      <form className="panel scan-entry-card" onSubmit={openSticker}>
        <div className="scan-entry-mark"><QrCode aria-hidden /></div>
        <div>
          <h2>{t("Find the exact box")}</h2>
          <p>{t("Check the box, its shipment and saved weights. Open editable boxes in Packing; finalized boxes remain read-only.")}</p>
        </div>
        <button type="button" className="primary-button scan-camera-button" data-guide={!value.trim() ? t("Scan the box sticker") : undefined} data-guide-detail={t("Use your camera, or type the code printed below the QR image.")} aria-haspopup="dialog" onClick={() => { setError(""); setCameraOpen(true); }}><Camera size={18} aria-hidden />{t("Scan QR with camera")}</button>
        <label htmlFor="sticker-code">{t("Sticker code or QR link")}</label>
        <div className="scan-entry-control">
          <input id="sticker-code" value={value} onChange={event => { setValue(event.target.value); setError(""); }} placeholder={t("Example: BOX-TETE-20260930-001")} autoComplete="off" autoFocus />
          <button className="primary-button" type="submit" data-guide={value.trim() ? t("Open the entered box code") : undefined} data-guide-priority="10" data-guide-detail={t("Check the code, then open the saved box record.")}><ScanLine size={18} /> {t("Open sticker")}</button>
        </div>
        {error && <p className="field-error" role="alert">{t(error)}</p>}
        {cameraOpen && <CameraScanner onCode={openScanned} onClose={() => setCameraOpen(false)} />}
      </form>
    </div>
  );
}
