"use client";
import { createContext, useContext, type ReactNode } from "react";
import { useApi } from "@/lib/api";
import type { Role, User } from "@/lib/types";

const Ctx = createContext<User | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const { data } = useApi<User>("/auth/me");
  return <Ctx.Provider value={data ?? null}>{children}</Ctx.Provider>;
}

export const useUser = () => useContext(Ctx);

const RANK: Record<Role, number> = { rep: 1, manager: 2, admin: 3 };
export function useCan(min: Role): boolean {
  const u = useUser();
  return !!u && RANK[u.role] >= RANK[min];
}
