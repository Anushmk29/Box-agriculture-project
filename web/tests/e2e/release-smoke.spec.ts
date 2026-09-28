import { expect, test } from "@playwright/test";

async function openWithMockSession(page: import("@playwright/test").Page) {
  await addValidLookingSessionCookie(page);
  await page.route("**/backend/api/v3/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        full_name: "Release Tester",
        role: "GENERAL",
        must_change_password: false,
      }),
    });
  });
}

async function addValidLookingSessionCookie(
  page: import("@playwright/test").Page,
) {
  const payload = Buffer.from(
    JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }),
  ).toString("base64url");
  await page.context().addCookies([
    {
      name: "access_token",
      value: `eyJhbGciOiJub25lIn0.${payload}.test`,
      domain: "127.0.0.1",
      path: "/",
      httpOnly: true,
      sameSite: "Lax",
    },
  ]);
}

test("protected routes redirect signed-out users and preserve the destination", async ({
  page,
}) => {
  await page.goto("/billing");

  await expect(page).toHaveURL(/\/login\?next=%2Fbilling$/);
  await expect(
    page.getByRole("heading", { name: "Welcome back" }),
  ).toBeVisible();
});

test("invalid credentials are rejected without exposing a protected page", async ({
  page,
}) => {
  await page.route("**/backend/api/v3/auth/login", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Invalid email or password" }),
    });
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill("not-a-user@example.invalid");
  await page.getByLabel("Password").fill("definitely-not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.getByText("Invalid email or password")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});

test("a valid session opens the requested workflow and can sign out", async ({
  page,
}) => {
  const email = process.env.E2E_EMAIL;
  const password = process.env.E2E_PASSWORD;
  test.skip(!email || !password, "E2E_EMAIL and E2E_PASSWORD are required");

  await page.goto("/billing");
  await page.getByLabel("Email").fill(email!);
  await page.getByLabel("Password").fill(password!);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/billing$/);
  await expect(
    page.getByRole("heading", { name: "Accounting settlement" }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("packing floor shows a group queue failure instead of hiding it", async ({
  page,
}) => {
  await openWithMockSession(page);
  await page.route("**/backend/api/v3/master-data/boxes", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route(
    "**/backend/api/v3/packing/groups/CT/sub-packing-lists",
    async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Packing queue unavailable" }),
      });
    },
  );

  await page.goto("/packing");

  await expect(page.getByText("Packing queue unavailable")).toBeVisible();
});

test("packing floor shows a box catalogue failure instead of hiding it", async ({
  page,
}) => {
  await openWithMockSession(page);
  await page.route("**/backend/api/v3/master-data/boxes", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Box catalogue unavailable" }),
    });
  });
  await page.route(
    "**/backend/api/v3/packing/groups/CT/sub-packing-lists",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          group_name: "CT",
          total_sub_customers: 0,
          total_weight_kg: 0,
          total_boxes_count: 0,
          sub_customers: [],
        }),
      });
    },
  );

  await page.goto("/packing");

  await expect(page.getByText("Box catalogue unavailable")).toBeVisible();
});

test("order intake shows a customer-data failure instead of hiding it", async ({
  page,
}) => {
  await openWithMockSession(page);
  await page.route(
    "**/backend/api/v3/master-data/customers?main_only=true",
    async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Customer data unavailable" }),
      });
    },
  );

  await page.goto("/orders");

  await expect(page.getByText("Customer data unavailable")).toBeVisible();
});

test("procurement shows a latest-batch failure instead of hiding it", async ({
  page,
}) => {
  await openWithMockSession(page);
  await page.route(
    "**/backend/api/v3/procurement/latest-batch",
    async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Procurement service unavailable" }),
      });
    },
  );

  await page.goto("/procurement");

  await expect(page.getByText("Procurement service unavailable")).toBeVisible();
});

test("the application shell reports a session lookup failure", async ({ page }) => {
  await addValidLookingSessionCookie(page);
  await page.route("**/backend/api/v3/auth/me", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Authentication service unavailable" }),
    });
  });
  await page.route(
    "**/backend/api/v3/master-data/customers?main_only=true",
    async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    },
  );

  await page.goto("/orders");

  await expect(page.getByText("Authentication service unavailable")).toBeVisible();
});

test("SKU mapping reports a master-product search failure", async ({ page }) => {
  await openWithMockSession(page);
  await page.route(
    "**/backend/api/v3/master-data/customers?main_only=true",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "customer-1",
            client_code: "MAIN",
            company_name: "Main Customer",
            is_main_customer: true,
          },
        ]),
      });
    },
  );
  await page.route("**/backend/api/v3/orders/upload-matrix", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        raw_file_id: "raw-1",
        file_name: "orders.xlsx",
        main_customer_id: "customer-1",
        total_sub_customers_detected: 1,
        sub_customer_codes: ["CT-TEST"],
        sheets_detected: [],
        group_summary: { CT: 1 },
        sub_customer_details: [],
        unresolved_products: [
          {
            product_code: "UNKNOWN-1",
            name_en: "Unknown produce",
            pack_size: "100g",
          },
        ],
        can_split: false,
        verification_notice: "Review unresolved products.",
        status: "INSPECTED",
      }),
    });
  });
  await page.route("**/backend/api/v3/master-data/products**", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Product catalogue unavailable" }),
    });
  });

  await page.goto("/orders");
  await page.locator('input[type="file"]').setInputFiles({
    name: "orders.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("test workbook"),
  });
  await page.getByRole("button", { name: "Upload & Inspect Matrix" }).click();
  await page.getByRole("button", { name: "Map SKU" }).click();

  await expect(page.getByText("Product catalogue unavailable")).toBeVisible();
});
