import { ApiError } from "./api";

export interface AccountView {
  id: number;
  email: string;
  role: "admin" | "member" | "auditor";
  organization_id: number;
}

async function jsonOrThrow<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) throw new ApiError(r.status, `${url} failed: ${r.status}`);
  return r.json() as Promise<T>;
}

export const login = (email: string, password: string) =>
  jsonOrThrow<AccountView>("/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

export async function logout(): Promise<void> {
  await fetch("/auth/logout", { method: "POST" });
}

export async function me(): Promise<AccountView | null> {
  try {
    return await jsonOrThrow<AccountView>("/auth/me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}
