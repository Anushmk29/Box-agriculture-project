import { expect, test } from "@playwright/test";

for (const logoutWorks of [true, false]) {
  test(`deleted account reaches login without a redirect loop (logout works: ${logoutWorks})`, async ({ page, context }) => {
    const payload = Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString("base64url");
    await context.addCookies([{ name: "access_token", value: `qa.${payload}.qa`, domain: "127.0.0.1", path: "/", httpOnly: true }]);
    let sessionRequests = 0;
    let logouts = 0;
    await page.route("**/backend/api/v3/**", async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname.endsWith("/auth/logout")) {
        logouts++;
        await route.fulfill(logoutWorks ? { status: 204, headers: { "set-cookie": "access_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" } }
          : { status: 500, json: { detail: "Logout unavailable" } });
      } else {
        if (pathname.endsWith("/auth/me")) sessionRequests++;
        await route.fulfill({ status: 401, json: { detail: "Valid authentication is required" } });
      }
    });
    await page.goto("/procurement");
    await expect(page).toHaveURL(/\/login\?next=/);
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    expect(logouts).toBe(1);
    expect(sessionRequests).toBeGreaterThan(0);
    const requestsAtLogin = sessionRequests;
    await page.reload();
    await expect(page.getByRole("heading", { name: "Sign in", exact: true })).toBeVisible();
    expect(sessionRequests).toBe(requestsAtLogin);
    if (logoutWorks) expect((await context.cookies()).some(cookie => cookie.name === "access_token")).toBe(false);
    else expect((await context.cookies()).some(cookie => cookie.name === "access_token")).toBe(true);
  });
}
