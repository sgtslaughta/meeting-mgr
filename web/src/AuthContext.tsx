import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { me, type AccountView } from "./auth";

interface AuthState {
  account: AccountView | null;
  loading: boolean;
  refresh: () => void;
}

const AuthContext = createContext<AuthState>({ account: null, loading: true, refresh: () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<AccountView | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    me().then(setAccount).finally(() => setLoading(false));
  }, []);

  useEffect(refresh, [refresh]);

  return (
    <AuthContext.Provider value={{ account, loading, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
