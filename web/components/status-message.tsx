"use client";

import { t, useLanguage } from "@/lib/language";

import { useEffect } from "react";
import { toast } from "@/lib/toast";

// Errors stay on the page next to what needs fixing; successes go to the shared toast.
export function StatusMessage({ error, success }: { error?: string; success?: string }) {
  useLanguage();
  useEffect(() => { if (success) toast.success(success); }, [success]);
  if (!error) return null;
  return <div role="alert" aria-atomic="true" className="message error">{t(error)}</div>;
}
