import { expect, test } from "@playwright/test";

test("running app: read-only desktop/mobile audit with the configured backend", async ({ page }) => {
  test.skip(!process.env.QA_LIVE_EMAIL || !process.env.QA_LIVE_PASSWORD, "Live read-only audit requires an existing authorized local account.");
  test.setTimeout(90_000);
  const issues: string[] = [];
  page.on("pageerror", error => issues.push(error.message));
  page.on("response", response => { if (response.status() >= 500) issues.push(`${response.status()} ${new URL(response.url()).pathname}`); });
  await page.route("**/backend/api/v3/**", async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET" && !["/backend/api/v3/auth/login", "/backend/api/v3/auth/logout"].includes(pathname)) {
      issues.push(`Blocked unexpected mutation: ${pathname}`);
      await route.abort();
    } else await route.continue();
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(process.env.QA_LIVE_EMAIL!);
  await page.getByLabel("Password").fill(process.env.QA_LIVE_PASSWORD!);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  for (const route of ["/", "/orders", "/procurement", "/packing", "/billing", "/users", "/scan"]) {
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(route, { waitUntil: "networkidle" });
      await expect(page.locator("h1")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${route}: overflow at ${width}px`).toBe(true);
      await page.screenshot({ path: `../outputs/browser-live-${route === "/" ? "operations" : route.slice(1)}-${width}.png`, fullPage: true });
    }
  }
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  expect(issues).toEqual([]);
});
