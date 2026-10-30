// Guides the eye to the next task: scroll to it, flash it once, then put focus inside it.
export function guideTo(selector: string) {
  const target = document.querySelector<HTMLElement>(selector);
  if (!target) return false;
  return guideElement(target);
}

export function guideElement(target: HTMLElement) {
  for (let parent: HTMLElement | null = target; parent; parent = parent.parentElement) {
    if (parent instanceof HTMLDetailsElement) parent.open = true;
  }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
  target.classList.remove("guide-flash");
  void target.offsetWidth; // restart the animation when the same target is chosen twice
  target.classList.add("guide-flash");
  window.setTimeout(() => target.classList.remove("guide-flash"), 1400);
  const fields = "input:not([disabled]):not([readonly]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]):not([readonly])";
  const actions = "button:not([disabled]), a[href], summary";
  const field = target.matches(`${fields}, ${actions}`) ? target
    : target.querySelector<HTMLElement>(fields) ?? target.querySelector<HTMLElement>(actions);
  if (field) field.focus({ preventScroll: true });
  else {
    if (!target.hasAttribute("tabindex")) target.tabIndex = -1;
    target.focus({ preventScroll: true });
  }
  return true;
}
