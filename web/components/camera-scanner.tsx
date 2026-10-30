"use client";

import { useEffect, useRef, useState } from "react";
import { TaskDialog } from "@/components/task-dialog";

function cameraError(error: unknown) {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Camera access is blocked. Allow camera access for this website in your browser settings, then try again.";
  }
  if (name === "NotFoundError") return "No camera was found. Connect a camera or type the sticker code instead.";
  if (name === "NotReadableError") return "The camera is busy or unavailable. Close other apps using it, then try again.";
  return "The camera could not start. Try again or type the sticker code instead.";
}

export function CameraScanner({ onCode, onClose }: { onCode: (value: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const preview = video.current;
    let stream: MediaStream | undefined;
    let timer = 0;
    let stopped = false;

    function stop() {
      stopped = true;
      window.clearTimeout(timer);
      stream?.getTracks().forEach(track => track.stop());
      if (preview) preview.srcObject = null;
    }

    async function start() {
      if (!window.isSecureContext) {
        setError("Camera scanning needs a secure connection. Open this website using HTTPS, or type the sticker code instead.");
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia) {
        setError("This browser cannot access a camera. Try another browser or type the sticker code instead.");
        return;
      }
      try {
        const media = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: "environment" } } });
        if (stopped || !preview) { media.getTracks().forEach(track => track.stop()); return; }
        stream = media;
        preview.srcObject = media;
        await preview.play();
        // Load the decoder only when scanning is requested; no native QR API is required.
        const { default: jsQR } = await import("jsqr");
        if (stopped) return;
        const canvas = document.createElement("canvas");
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) throw new Error("Could not read camera frames");
        setReady(true);

        const tick = () => {
          if (stopped) return;
          try {
            if (preview.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && preview.videoWidth && preview.videoHeight) {
              const scale = Math.min(1, 960 / Math.max(preview.videoWidth, preview.videoHeight));
              canvas.width = Math.max(1, Math.round(preview.videoWidth * scale));
              canvas.height = Math.max(1, Math.round(preview.videoHeight * scale));
              context.drawImage(preview, 0, 0, canvas.width, canvas.height);
              const frame = context.getImageData(0, 0, canvas.width, canvas.height);
              const code = jsQR(frame.data, frame.width, frame.height);
              if (code?.data) { stop(); onCode(code.data); return; }
            }
          } catch {
            stop();
            setError("The camera preview could not be read. Try again or type the sticker code instead.");
            return;
          }
          timer = window.setTimeout(tick, 250);
        };
        tick();
      } catch (reason) {
        if (stopped) return;
        stop();
        setError(cameraError(reason));
      }
    }

    void start();
    return stop;
  }, [onCode, attempt]);

  return <TaskDialog title="Scan with camera" description="Allow camera access, then hold the QR sticker inside the frame. The box opens automatically." size="sm" onClose={onClose}>
    {!error && <p className="camera-status" role="status">{ready ? "Point the camera at the QR sticker." : "Starting camera. Allow access when your browser asks."}</p>}
    <div className="camera-scan">
      {error ? <><p role="alert" className="message error">{error}</p><button type="button" className="invoice-secondary-button" onClick={() => { setError(""); setReady(false); setAttempt(value => value + 1); }}>Try camera again</button></> : <><video ref={video} aria-label="Live camera preview" autoPlay muted playsInline /><span className="camera-frame" aria-hidden /></>}
    </div>
  </TaskDialog>;
}
