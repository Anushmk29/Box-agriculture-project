function validationMessage(detail: unknown): string | null {
  if (!Array.isArray(detail)) return null;
  const messages = detail.flatMap(error => {
    if (!error || typeof error !== "object" || !("msg" in error) || typeof error.msg !== "string") return [];
    const location = "loc" in error && Array.isArray(error.loc) ? error.loc : [];
    const last = location[location.length - 1];
    const field = typeof last === "string" && !["body", "query", "path"].includes(last)
      ? last.replace(/_eur\b/gi, "").replace(/_/g, " ") : "";
    const label = field ? field[0].toUpperCase() + field.slice(1) + ": " : "";
    const index = location.find((value: unknown) => typeof value === "number");
    const line = typeof index === "number" ? `Line ${index + 1}: ` : "";
    return [line + label + error.msg.replace(/^Value error, /, "")];
  });
  return messages.length ? messages.slice(0, 3).join("; ") : null;
}

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    const message = typeof detail === "string" ? detail
      : detail && typeof detail === "object" && "message" in detail ? String(detail.message)
      : validationMessage(detail) ?? `Request failed with status ${status}`;
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function isRevisionConflict(error: unknown): boolean {
  return error instanceof ApiError && Boolean(error.detail && typeof error.detail === "object" &&
    "code" in error.detail && ["STALE_REVISION", "REVISION_REQUIRED"].includes(String(error.detail.code)));
}
