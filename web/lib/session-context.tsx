"use client";
import { createContext, useContext } from "react";

export type SessionUser = { user_id: string; full_name: string; role: string; must_change_password: boolean; permissions: string[]; request_replay_supported?: boolean; upload_replay_supported?: boolean };
export const SessionContext = createContext<SessionUser | null>(null);
export function useSession() { return useContext(SessionContext); }
