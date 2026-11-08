import { expect, test } from "@playwright/test";

async function openWithMockSession(page: import("@playwright/test").Page, role = "GENERAL") {
  await addValidLookingSessionCookie(page);
  const generalPermissions = ["validate_orders", "confirm_procurement", "design_packing", "floor_packing", "lock_final_pack"];
  const permissions = role === "OWNER_ADMIN"
    ? [...generalPermissions, "dispatch_handoff_01", "return_handoff_02", "draft_invoice", "manage_billing", "manage_users"]
    : role === "GENERAL_ACCOUNTING" ? [...generalPermissions, "draft_invoice", "manage_billing"] : generalPermissions;
  await page.route("**/backend/api/v3/auth/me", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        full_name: "Release Tester",
        role,
        must_change_password: false, user_id: "upload-fixture-user", upload_replay_supported: true,
        permissions,
      }),
    });
  });
  await page.route("**/backend/api/v3/operations/summary", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ permissions }) });
  });
}

async function addValidLookingSessionCookie(
  page: import("@playwright/test").Page,
) {
  // No mock scenario may fall through to the live API.
  await page.route("**/backend/api/v3/**", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
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
    page.getByRole("heading", { name: "Sign in" }),
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
    page.getByRole("heading", { name: "Billing desk" }),
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
    "**/backend/api/v3/packing/files?*",
    async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Packing queue unavailable" }),
      });
    },
  );

  await page.goto("/packing/orders");

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
    "**/backend/api/v3/packing/groups/ALL/sub-packing-lists",
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
    "**/backend/api/v3/master-data/customer-register?*",
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

test("procurement shows a batch-list failure instead of hiding it", async ({
  page,
}) => {
  await openWithMockSession(page);
  await page.route(
    "**/backend/api/v3/procurement/batches?*",
    async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Procurement service unavailable" }),
      });
    },
  );

  await page.goto("/procurement");
  await page.getByRole("link", { name: "Choose batch", exact: true }).click();

  await expect(page.getByText("Procurement service unavailable")).toBeVisible();
});

test("procurement cannot repeat completed workflow actions", async ({ page }) => {
  await openWithMockSession(page);
  await page.route(
    "**/backend/api/v3/procurement/batches/batch-1",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          batch_id: "batch-1",
          batch_reference: "PUR-1",
          status: "HANDOFF_01_SENT",
          total_skus: 0,
          total_ordered_weight_kg: 0,
          total_buffer_weight_kg: 0,
          total_required_weight_kg: 0,
          items: [],
        }),
      });
    },
  );

  await page.route("**/backend/api/v3/procurement/batches?*", route => route.fulfill({ json: { items: [{ batch_id: "batch-1", batch_reference: "PUR-1", customers: ["Completed client"], files: [], total_skus: 0, status: "HANDOFF_01_SENT" }], has_more: false } }));
  await page.goto("/procurement?batchId=batch-1");

  await expect(page.getByRole("heading", { name: "Purchasing" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Next: Packing", exact: true })).toContainText("Purchasing complete");
  await expect(
    page.getByRole("button", { name: "Confirm purchases and send to packing" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Dispatch Signed Handoff 01" }),
  ).toHaveCount(0);
});

test("navigation badges match visible actionable queues", async ({ page }) => {
  await openWithMockSession(page, "OWNER_ADMIN");
  await page.route("**/backend/api/v3/operations/summary", route => route.fulfill({
    status: 200,
    contentType: "application/json",
      body: JSON.stringify({ permissions: ["validate_orders", "confirm_procurement", "floor_packing", "manage_billing", "manage_users"], active_orders: 60, awaiting_intake: 3, awaiting_procurement: 1, awaiting_packing: 49, boxes_missing_weights: 0, awaiting_billing: 10 }),
  }));
  await page.route("**/backend/api/v3/master-data/customer-register?*", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/backend/api/v3/orders/files?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ items: [{ raw_file_id: "raw-1", file_name: "pending-order.xlsx", shipment_date: "2026-09-30", route: "CT", status: "PARSED", customer_count: 2, unresolved_rows: 0 }], total: 3, offset: 0, limit: 25, has_more: false }),
  }));

  await page.goto("/orders/files");

  await expect(page.getByRole("link", { name: /^Orders/ })).toContainText("3");
  await expect(page.getByRole("link", { name: /^Orders/ })).not.toContainText("60");
  await expect(page.getByRole("link", { name: /^Purchasing/ })).toContainText("1");
  await expect(page.getByRole("link", { name: /^Packing/ })).toContainText("49");
  await expect(page.getByRole("link", { name: /Billing/ })).toContainText("10");
  await expect(page.getByRole("link", { name: "Team & access" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Order files", exact: true })).toContainText("pending-order.xlsx");
});

test("general users only see their permitted work areas", async ({ page }) => {
  await openWithMockSession(page);
  await page.route("**/backend/api/v3/operations/summary", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      permissions: ["validate_orders", "confirm_procurement", "design_packing", "floor_packing", "lock_final_pack"],
      awaiting_intake: 3,
      awaiting_procurement: 1,
      awaiting_packing: 49,
      boxes_missing_weights: 0,
      awaiting_billing: 10,
      open_balance_eur: 135.05,
    }),
  }));

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Team work", exact: true })).toBeVisible();
  const navigation = page.getByRole("navigation", { name: "Primary navigation" });
  const workAreas = page.locator(".today-page");
  await expect(navigation.getByRole("link", { name: /^Orders/ })).toBeVisible();
  await expect(workAreas.getByRole("region", { name: "Confirm market purchases" })).toBeVisible();
  await expect(workAreas.getByRole("region", { name: "Pack boxes" })).toBeVisible();
  await expect(navigation.getByRole("link", { name: /Billing/ })).toHaveCount(0);
  await expect(workAreas.getByRole("link", { name: /Billing/ })).toHaveCount(0);
  await expect(navigation.getByRole("link", { name: "Team & access" })).toHaveCount(0);
  await expect(page.getByText("Unpaid invoice balance")).toHaveCount(0);
});

test("packing cannot repeat completed workflow actions", async ({ page }) => {
  await openWithMockSession(page);
  await page.route("**/backend/api/v3/master-data/boxes", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });
  await page.route("**/backend/api/v3/packing/groups/ALL/sub-packing-lists", async (route) => {
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
  });
  await page.route("**/backend/api/v3/packing/orders/order-1/packing-list", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        client_order_id: "order-1",
        order_ref_id: "ORD-1",
        status: "HANDOFF_02_RETURNED",
        sub_customer_code: "CT-Test",
        route_code: "GROUP CT",
        shipment_date: "2026-09-29",
        total_boxes_count: 1,
        boxes: [],
      }),
    });
  });

  await page.goto("/packing?orderId=order-1");

  await expect(page.getByRole("heading", { name: "Packing desk" })).toBeVisible();
  await page.getByText("Order details", { exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Shipment workflow" })).toContainText("Purchasing");
  await expect(page.getByRole("button", { name: /Fast-Track Active Order/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Prepare boxes/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Start packing/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Return packing handoff/ })).toHaveCount(0);
});

test("dashboard directs packing through the order queue without scale prompts", async ({ page }) => {
  await openWithMockSession(page, "OWNER_ADMIN");
  await page.goto("/");
  await expect(page.getByText("Boxes missing weights", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Shipment overview" }).getByRole("link", { name: /Orders awaiting packing:/ })).toHaveAttribute("href", "/packing/orders");
});

test("packed contents start floor work without scale entry", async ({ page }) => {
  await openWithMockSession(page);
  let status = "PACKING_DESIGNED";
  await page.route("**/backend/api/v3/master-data/boxes", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/backend/api/v3/packing/groups/ALL/sub-packing-lists", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ group_name: "CT", total_sub_customers: 0, total_weight_kg: 0, total_boxes_count: 0, sub_customers: [] }) }));
  await page.route("**/backend/api/v3/packing/orders/order-designed/prepare", route => {
    status = "FLOOR_PACKING";
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ order_id: "order-designed", status }) });
  });
  await page.route("**/backend/api/v3/packing/orders/order-designed/packing-list", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      client_order_id: "order-designed", order_ref_id: "ORD-DESIGNED", status,
      sub_customer_code: "CT-1", route_code: "GROUP CT", shipment_date: "2026-09-29", total_boxes_count: 1,
      boxes: [{
        box_id: "box-1", box_number: 1, box_type_name: "Small", safe_net_capacity_kg: 5, is_overridden: false,
        actual_gross_weight_kg: null, actual_net_weight_kg: null, volumetric_weight_kg: null,
        chargeable_weight_kg: null, is_locked: false, qr_code_uid: "qr-1", sticker_label: "CT-1 / 1 of 1",
        items: [{ product_id: "p-1", product_code: "T001", produce_name: "Herb", pack_size: "1kg", packed_count: 2, net_weight_kg: 2, adjustment_type: "AS_ORDERED" }],
      }],
    }),
  }));

  await page.goto("/packing?orderId=order-designed");

  await expect(page.getByText(/1-Click Auto-Pack/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start packing", exact: true })).toHaveCount(0);
  await expect(page.getByLabel(/scale (net|gross)/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save verified weights", exact: true })).toHaveCount(0);
  await expect(page.locator(".packing-box-saved")).toContainText("2.00 kg");
  await expect(page.getByRole("button", { name: "Finalize Packing", exact: true })).toBeEnabled();

  await page.locator(".packing-box-details").first().evaluate(element => { (element as HTMLDetailsElement).open = true; }); await page.getByRole("button", { name: "Sticker" }).click();
  await expect(page.getByText("qr-1", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy code" })).toBeVisible();
});

test("owner manages deactivation and permanent deletion from one account action", async ({ page }) => {
  await openWithMockSession(page, "OWNER_ADMIN");
  let users = [{ id: "user-1", email: "unused@example.com", full_name: "Unused Person", role: "GENERAL", is_active: true, must_change_password: false, user_id: "upload-fixture-user", upload_replay_supported: true }];
  let deletedUserId = "";
  await page.route("**/backend/api/v3/admin/users/user-1", async route => {
    if (route.request().method() === "DELETE") {
      deletedUserId = "user-1";
      users = [];
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    await route.fallback();
  });
  await page.route("**/backend/api/v3/admin/users/register?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ items: users, total: users.length, offset: 0, limit: 25, has_more: false }),
  }));

  await page.goto("/users");
  await page.getByRole("button", { name: "Manage" }).click();
  const dialog = page.getByRole("dialog", { name: "Manage Unused Person" });
  await expect(dialog.getByRole("button", { name: "Deactivate account" })).toBeVisible();
  await dialog.getByRole("button", { name: "Delete permanently" }).click();
  await page.getByRole("dialog", { name: "Delete Unused Person?" }).getByRole("button", { name: "Delete permanently" }).click();

  await expect.poll(() => deletedUserId).toBe("user-1");
  await expect(page.getByText("Unused Person was permanently deleted.")).toBeVisible();
  await expect(page.getByText("unused@example.com")).toHaveCount(0);
});

test("box transfer only offers compatible targets", async ({ page }) => {
  await openWithMockSession(page);
  await page.route("**/backend/api/v3/master-data/boxes", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/backend/api/v3/packing/groups/ALL/sub-packing-lists", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ group_name: "CT", total_sub_customers: 0, total_weight_kg: 0, total_boxes_count: 0, sub_customers: [] }) }));
  await page.route("**/backend/api/v3/packing/orders/order-transfer/packing-list", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      client_order_id: "order-transfer", order_ref_id: "ORD-TRANSFER", status: "FLOOR_PACKING",
      sub_customer_code: "BL-4", route_code: "GROUP TA", shipment_date: "2026-09-29", total_boxes_count: 2,
      boxes: [
        { box_id: "foam", box_number: 1, box_type_name: "Medium", box_material: "FOAM", safe_net_capacity_kg: 7, is_overridden: false, actual_gross_weight_kg: null, actual_net_weight_kg: null, volumetric_weight_kg: null, chargeable_weight_kg: null, is_locked: false, qr_code_uid: "qr-1", sticker_label: "BL-4 / 1 of 2", items: [{ product_id: "p-1", product_code: "T203-1", produce_name: "Rambutan", pack_size: "1kg", packed_count: 5, net_weight_kg: 5, adjustment_type: "AS_ORDERED" }] },
        { box_id: "cardboard", box_number: 2, box_type_name: "Mini", box_material: "CARDBOARD", safe_net_capacity_kg: 2.5, is_overridden: false, actual_gross_weight_kg: null, actual_net_weight_kg: null, volumetric_weight_kg: null, chargeable_weight_kg: null, is_locked: false, qr_code_uid: "qr-2", sticker_label: "BL-4 / 2 of 2", items: [{ product_id: "p-2", product_code: "T102-1", produce_name: "Chaplu leaf", pack_size: "100g", packed_count: 3, net_weight_kg: 0.3, adjustment_type: "AS_ORDERED" }] },
      ],
    }),
  }));

  await page.goto("/packing?orderId=order-transfer");
  await page.getByText("Order tools", { exact: true }).click();
  await page.getByRole("button", { name: /Move items between boxes/ }).click();
  await page.getByLabel("Move from box").selectOption("cardboard");

  await expect(page.getByLabel("Into box").getByRole("option", { name: /Box #1/ })).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "These boxes cannot be combined" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Move items", exact: true })).toBeDisabled();
});

test("scanned editable sticker opens its order and shows who changed it", async ({ page }) => {
  await openWithMockSession(page);
  await page.route("**/backend/api/v3/packing/scan/BOX-GROUP%20CT-20260929-001-6b89f7de?*", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      box_id: "box-1", order_id: "order-1", qr_code_uid: "BOX-GROUP CT-20260929-001-6b89f7de", box_number: 1,
      order_ref_id: "ORD-1", order_status: "FLOOR_PACKING", can_edit: true,
      customer_code: "CT-A", route_code: "CT", shipment_date: "2026-09-30", is_locked: false,
      actual_net_weight_kg: 2.03, actual_gross_weight_kg: 2.53,
      items: [{ product_code: "T001", produce_name: "Herb", pack_size: "1kg", packed_count: 2, net_weight_kg: 2 }],
      change_history: [{ event_type: "BOX_WEIGHT_RECORDED", actor_name: "Floor Packer", actor_role: "GENERAL", changed_at: "2026-09-30T10:30:00Z", before: { actual_net_weight_kg: null }, after: { actual_net_weight_kg: 2.03 } }],
    }),
  }));

  await page.route("**/backend/api/v3/packing/scan/*/history?*", route => route.fulfill({ json: { items: [{ event_type: "BOX_WEIGHT_RECORDED", actor_name: "Floor Packer", actor_role: "GENERAL", changed_at: "2026-09-30T10:30:00Z", before: { actual_net_weight_kg: null }, after: { actual_net_weight_kg: 2.03 } }], total: 1, offset: 0, limit: 25, has_more: false } }));
  await page.goto("/scan");
  await expect(page.getByRole("link", { name: "Scan box" })).toHaveAttribute("aria-current", "page");
  await page.getByLabel("Sticker code or QR link").fill("http://127.0.0.1:3000/scan/BOX-GROUP%20CT-20260929-001-6b89f7de");
  await page.getByRole("button", { name: "Open sticker" }).click();

  await expect(page).toHaveURL(/\/scan\/BOX-GROUP%20CT-20260929-001-6b89f7de$/);
  await expect(page.getByRole("link", { name: "Open this box in packing" })).toHaveAttribute("href", "/packing?orderId=order-1&box=BOX-GROUP%20CT-20260929-001-6b89f7de");
  await expect(page.getByRole("heading", { name: "Change history" })).toBeVisible();
  await page.locator("summary").filter({ hasText: "Change history" }).click();
  await expect(page.getByText(/Floor Packer/)).toBeVisible();
  await expect(page.getByText(/actual net weight kg: — → 2.03/)).toBeVisible();
});

test("billing generates master invoices for selected buyers", async ({ page }) => {
  await openWithMockSession(page, "GENERAL_ACCOUNTING");
  await page.route("**/backend/api/v3/invoicing", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  let createdInvoice: Record<string, unknown> = {};
  await page.route("**/backend/api/v3/invoicing/orders?*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ items: [
        { order_id: "order-a", order_ref_id: "ORD-A", file_name: "A.xlsx", customer_code: "CT-A", customer_name: "Customer A", status: "HANDOFF_02_RETURNED", can_invoice: true, invoice_id: null, invoice_number: null, invoice_status: null },
        { order_id: "order-b", order_ref_id: "ORD-B", file_name: "B.xlsx", customer_code: "CT-B", customer_name: "Customer B", status: "HANDOFF_02_RETURNED", can_invoice: true, invoice_id: null, invoice_number: null, invoice_status: null },
      ], total: 2, offset: 0, limit: 25, has_more: false }),
    });
  });
  let generated: string[] = [];
  await page.route("**/backend/api/v3/invoicing/generate-master", async (route) => {
    const body = route.request().postDataJSON();
    generated = body.client_order_ids;
    createdInvoice = {
      invoice_id: "invoice-master", client_order_id: "order-a", invoice_number: "MINV-1",
      invoice_type: "MASTER_SUMMARY", main_customer_seller: "Main buyer", sub_customer_buyer: "2 sub-customers / orders",
      subtotal_eur: 20, total_amount_eur: 20, amount_paid_eur: 0, status: "INVOICE_DRAFT", internal_notes: null, source_order_count: 2,
      items: [
        { line_item_id: "line-a", source_order_id: "order-a", source_order_ref: "ORD-A", source_file_name: "A.xlsx", source_customer_code: "CT-A", product_code: "T001", product_name: "Herb", box_number: 1, pack_size: "1kg", billed_packs: 2, billed_weight_kg: 2, unit_price_eur: 5, line_total_eur: 10 },
        { line_item_id: "line-b", source_order_id: "order-b", source_order_ref: "ORD-B", source_file_name: "B.xlsx", source_customer_code: "CT-B", product_code: "T002", product_name: "Leaf", box_number: 1, pack_size: "1kg", billed_packs: 2, billed_weight_kg: 2, unit_price_eur: 5, line_total_eur: 10 },
      ],
    };
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(createdInvoice),
    });
  });
  await page.route("**/backend/api/v3/invoicing/invoice-master/draft", async route => {
    const body = route.request().postDataJSON();
    const price = body.items[0].unit_price_eur;
    const items = createdInvoice.items as Record<string, unknown>[];
    createdInvoice = { ...createdInvoice, subtotal_eur: price * 2 + 10, total_amount_eur: price * 2 + 10, internal_notes: body.internal_notes, items: [{ ...items[0], unit_price_eur: price, line_total_eur: price * 2 }, items[1]] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(createdInvoice) });
  });
  await page.route("**/backend/api/v3/invoicing/invoice-master/finalize", route => {
    createdInvoice = { ...createdInvoice, status: "INVOICE_SENT" };
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(createdInvoice) });
  });

  await page.route("**/backend/api/v3/invoicing/invoice-master", route => route.fulfill({ json: createdInvoice }));
  await page.goto("/billing");
  await page.getByRole("checkbox", { name: "Select CT-A" }).click();
  await page.getByRole("checkbox", { name: "Select CT-B" }).click();
  await page.getByRole("button", { name: "Create master draft" }).click();

  await expect.poll(() => generated).toEqual(["order-a", "order-b"]);
  await expect(page.getByText(/Master invoice · 2 sub-orders/)).toBeVisible();
  await expect(page.locator(".invoice-paper")).toContainText("CT-A");
  await expect(page.locator(".invoice-paper")).toContainText("B.xlsx");
  await expect(page.locator(".invoice-paper")).not.toContainText("ORD-B");
  await expect(page.getByRole("button", { name: "Approve and create final invoice" })).toBeVisible();
  await page.getByLabel("Unit price for T001 CT-A").fill("6");
  await page.getByRole("button", { name: "Approve and create final invoice" }).click();
  await expect(page.locator(".invoice-paper")).toContainText("Issued");
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
    "**/backend/api/v3/master-data/customer-register?*",
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
    "**/backend/api/v3/master-data/customer-register?*",
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: [
          {
            id: "customer-1",
            client_code: "MAIN",
            company_name: "Main Customer",
            is_main_customer: true,
          },
        ], total: 1, offset: 0, limit: 25, has_more: false }),
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
        status: "UPLOADED",
      }),
    });
  });
  await page.route("**/backend/api/v3/orders/raw-1/validate", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ unresolved: [{ product_code: "UNKNOWN-1", name_en: "Unknown produce", pack_size: "100g" }], can_split: false }) }));
  await page.route("**/backend/api/v3/master-data/products**", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Product catalogue unavailable" }),
    });
  });

  await page.goto("/orders");
  await page.getByRole("combobox", { name: /Billing customer/ }).selectOption("customer-1");
  await page.locator('input[type="file"]').setInputFiles({
    name: "orders.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("test workbook"),
  });
  await page.getByRole("button", { name: "Upload and check order" }).click();
  await page.getByRole("button", { name: "Match product" }).click();

  await expect(page.getByText("Product catalogue unavailable")).toBeVisible();
});
