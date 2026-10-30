export type GuidanceTarget = { element: HTMLElement; title: string; detail: string; priority: number };

function label(element: HTMLElement) {
  const field = element as HTMLInputElement;
  const labelledBy = element.getAttribute("aria-labelledby")?.split(/\s+/).map(id => document.getElementById(id)?.textContent?.trim()).filter(Boolean).join(" ");
  const labels = Array.from(field.labels || []).map(label => {
    const copy = label.cloneNode(true) as HTMLElement;
    copy.querySelectorAll("input, select, textarea, button, datalist").forEach(control => control.remove());
    return copy.textContent?.trim().replace(/\s+/g, " ");
  }).filter(Boolean).join(" ");
  return element.getAttribute("aria-label") || labelledBy || labels
    || element.textContent?.trim().replace(/\s+/g, " ") || "Continue";
}

/** Explicit page state wins; required fields and enabled primary actions provide
 * the same guidance inside forms and dialogs. Never invoke a business action. */
export function nextGuidance(scope: HTMLElement, dialog = false): GuidanceTarget | undefined {
  const available = (element: HTMLElement) => {
    if (element.closest("[data-guide-ui], [hidden], [inert], [aria-busy='true']")
      || element.matches(":disabled, [aria-disabled='true'], [readonly], input[type='hidden']")) return false;
    if (!dialog && element.closest("dialog")) return false;
    if (element.closest("dialog:not([open])")) return false;
    return element.getClientRects().length > 0 || Boolean(element.closest("details:not([open])"));
  };
  const targets: GuidanceTarget[] = [];
  scope.querySelectorAll<HTMLElement>("[data-guide]").forEach(element => {
    if (element.dataset.guide && available(element)) targets.push({ element,
      title: element.dataset.guide, detail: element.dataset.guideDetail || "Use the highlighted control to continue.",
      priority: Number(element.dataset.guidePriority || 30) });
  });
  scope.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input[required], select[required], textarea[required]").forEach(element => {
    if (!available(element) || element.validity.valid) return;
    targets.push({ element, title: `Complete ${label(element)}`, detail: element.validationMessage || "Enter this value before continuing.", priority: 5 });
  });
  scope.querySelectorAll<HTMLElement>("button.primary-button, a.primary-button, button[type='submit']").forEach(element => {
    if (!available(element) || element.hasAttribute("data-guide") || element.matches(".danger-button") || element.closest(".register-pagination")) return;
    targets.push({ element, title: label(element), detail: "Review the information, then use this action when ready.", priority: 80 });
  });
  return targets.sort((a, b) => a.priority - b.priority)[0];
}
