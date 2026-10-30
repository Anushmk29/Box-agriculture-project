import { expect, test, type Page } from "@playwright/test";
import QRCode from "qrcode";

declare global {
  interface Window {
    cameraTest: {
      requests: MediaStreamConstraints[];
      streams: MediaStream[];
      deny: boolean;
      release?: () => void;
    };
  }
}

async function setup(page: Page, mode: "qr" | "blank" | "denied" | "delayed" = "blank") {
  const token = Buffer.from(JSON.stringify({ exp: Date.now() / 1000 + 3600 })).toString("base64url");
  await page.context().addCookies([{ name: "access_token", value: `qa.${token}.qa`, domain: "127.0.0.1", path: "/" }]);
  await page.route("**/backend/api/v3/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me")) return route.fulfill({ json: { full_name: "Scanner Staff", role: "GENERAL", permissions: ["floor_packing"], must_change_password: false } });
    if (path.endsWith("/operations/summary")) return route.fulfill({ json: {} });
    if (path.endsWith("/history")) return route.fulfill({ json: { items: [], total: 0, has_more: false } });
    return route.fulfill({ json: { order_id: "order-1", qr_code_uid: "QR-1", box_number: 1, order_ref_id: "ORD-1", order_status: "FLOOR_PACKING", can_edit: true, customer_code: "SUNNY", route_code: "CT", shipment_date: "2026-10-26", is_locked: false, items: [], change_history: [] } });
  });
  const qr = await QRCode.toDataURL("https://packing.example/scan/QR-1", { width: 400, margin: 4 });
  await page.addInitScript(({ mode, qr }) => {
    Reflect.deleteProperty(window, "BarcodeDetector");
    const state = window.cameraTest = { requests: [], streams: [], deny: mode === "denied" } as Window["cameraTest"];
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { configurable: true, value: async (constraints: MediaStreamConstraints) => {
      state.requests.push(constraints);
      if (state.deny) throw new DOMException("Permission denied", "NotAllowedError");
      if (mode === "delayed") await new Promise<void>(resolve => { state.release = resolve; });
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 600;
      const context = canvas.getContext("2d")!;
      context.fillStyle = "white";
      context.fillRect(0, 0, 600, 600);
      if (mode === "qr") {
        const image = new Image();
        image.src = qr;
        await image.decode();
        context.drawImage(image, 100, 100, 400, 400);
      }
      const stream = canvas.captureStream(10);
      state.streams.push(stream);
      return stream;
    } });
  }, { mode, qr });
  await page.goto("/scan");
}

test("reads a real QR camera frame without BarcodeDetector and opens the exact box", async ({ page }) => {
  await setup(page, "qr");
  expect(await page.evaluate(() => window.cameraTest.requests.length)).toBe(0);
  await page.getByRole("button", { name: "Scan QR with camera" }).click();
  await expect(page).toHaveURL(/\/scan\/QR-1$/);
  await expect(page.getByRole("link", { name: "Open this box in packing" })).toHaveAttribute("href", "/packing?orderId=order-1&box=QR-1");
  const camera = await page.evaluate(() => ({ requests: window.cameraTest.requests, tracks: window.cameraTest.streams.flatMap(stream => stream.getTracks().map(track => track.readyState)) }));
  expect(camera.requests.length).toBeGreaterThan(0);
  expect(camera.requests.every(request => request.audio === false && typeof request.video === "object" && request.video.facingMode && JSON.stringify(request.video.facingMode).includes("environment"))).toBe(true);
  expect(camera.tracks.length).toBeGreaterThan(0);
  expect(camera.tracks.every(state => state === "ended")).toBe(true);
});

test("closing the preview stops the camera and allows another scan", async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 390, height: 844 });
  for (let attempt = 0; attempt < 2; attempt++) {
    await page.getByRole("button", { name: "Scan QR with camera" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Point the camera" })).toBeVisible();
    await expect(page.getByLabel("Live camera preview")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "Close Scan with camera" }).click();
    await expect(page.getByRole("dialog", { name: "Scan with camera" })).toHaveCount(0);
    expect(await page.evaluate(() => window.cameraTest.streams.every(stream => stream.getTracks().every(track => track.readyState === "ended")))).toBe(true);
  }
});

test("denied camera permission explains recovery and can be retried", async ({ page }) => {
  await setup(page, "denied");
  await page.getByRole("button", { name: "Scan QR with camera" }).click();
  await expect(page.getByRole("dialog", { name: "Scan with camera" }).getByRole("alert")).toContainText("Allow camera access for this website");
  await page.evaluate(() => { window.cameraTest.deny = false; });
  await page.getByRole("button", { name: "Try camera again" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Point the camera" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Scan with camera" })).toHaveCount(0);
  await page.getByLabel("Sticker code or QR link").fill("QR-1");
  await page.getByRole("button", { name: "Open sticker" }).click();
  await expect(page).toHaveURL(/\/scan\/QR-1$/);
});

test("permission granted after closing does not leave the camera running", async ({ page }) => {
  await setup(page, "delayed");
  await page.getByRole("button", { name: "Scan QR with camera" }).click();
  await expect.poll(() => page.evaluate(() => Boolean(window.cameraTest.release))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Scan with camera" })).toHaveCount(0);
  await page.evaluate(() => window.cameraTest.release?.());
  await expect.poll(() => page.evaluate(() => window.cameraTest.streams.length)).toBeGreaterThan(0);
  expect(await page.evaluate(() => window.cameraTest.streams.every(stream => stream.getTracks().every(track => track.readyState === "ended")))).toBe(true);
});

test("camera option stays available with an explanation on insecure connections", async ({ page }) => {
  await setup(page);
  await page.evaluate(() => Object.defineProperty(window, "isSecureContext", { value: false }));
  await page.getByRole("button", { name: "Scan QR with camera" }).click();
  await expect(page.getByRole("dialog", { name: "Scan with camera" }).getByRole("alert")).toContainText("Open this website using HTTPS");
  expect(await page.evaluate(() => window.cameraTest.requests.length)).toBe(0);
});

test("a browser without camera access keeps manual sticker entry usable", async ({ page }) => {
  await setup(page);
  await page.evaluate(() => Object.defineProperty(navigator, "mediaDevices", { value: undefined }));
  await page.getByRole("button", { name: "Scan QR with camera" }).click();
  await expect(page.getByRole("dialog", { name: "Scan with camera" }).getByRole("alert")).toContainText("This browser cannot access a camera");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Scan with camera" })).toHaveCount(0);
  await page.getByLabel("Sticker code or QR link").fill("QR-1");
  await page.getByRole("button", { name: "Open sticker" }).click();
  await expect(page).toHaveURL(/\/scan\/QR-1$/);
});
