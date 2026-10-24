import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { api } from "./api";
import type { AppConfig, Me } from "./types";

type Auth = {
  me: Me | null;
  config: AppConfig | null;
  loading: boolean;
  setMe: (me: Me | null) => void;
  signOut: () => Promise<void>;
};

const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    Promise.all([api.me().catch(() => null), api.config().catch(() => null)]).then(([m, c]) => {
      if (!alive) return;
      setMe(m);
      setConfig(c);
      setLoading(false);
    });
    return () => { alive = false; };
  }, []);

  // A 401 from any request means the session ended (expired, signed out elsewhere): RequireAuth then sends the
  // user to sign-in, back to the same page afterwards.
  useEffect(() => {
    const ended = () => setMe(null);
    window.addEventListener("boldoc:signed-out", ended);
    return () => window.removeEventListener("boldoc:signed-out", ended);
  }, []);

  const signOut = useCallback(async () => {
    await api.signOut().catch(() => undefined);
    setMe(null);
  }, []);

  const value = useMemo(() => ({ me, config, loading, setMe, signOut }), [me, config, loading, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): Auth {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return null;
  if (!me) return <Navigate to={`/sign-in?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <>{children}</>;
}
