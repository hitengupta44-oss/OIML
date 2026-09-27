import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { getMe, signIn, signOut, supabase, type Me } from "./lib/supabase";
import { pendingCount, sync, watchConnectivity } from "./lib/offline";
import { wake } from "./lib/space";
import Evaluate from "./routes/Evaluate";
import Repository from "./routes/Repository";
import Dashboard from "./routes/Dashboard";

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [online, setOnline] = useState(navigator.onLine);
  const [pending, setPending] = useState(0);

  useEffect(() => {
    getMe().then((m) => {
      setMe(m);
      setReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange(() => {
      getMe().then(setMe);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => watchConnectivity(setOnline), []);

  useEffect(() => {
    const tick = () => pendingCount().then(setPending);
    tick();
    const id = setInterval(tick, 4000);
    return () => clearInterval(id);
  }, []);

  // A free CPU Space sleeps after ~48 h and takes 30-60 s to wake. Ping on
  // load so a user never triggers the first request themselves.
  useEffect(() => {
    if (online) void wake();
  }, [online]);

  if (!ready) return <div className="empty">Loading…</div>;
  if (!me) return <SignIn onSignedIn={setMe} />;

  return (
    <>
      <div className="mast">
        <div className="mark">
          <b>NAWI Type Evaluation</b>
          <span>OIML R 76-1:2006</span>
        </div>
        <nav className="tabs">
          <NavLink to="/evaluate" className={({ isActive }) => (isActive ? "on" : "")}>
            Evaluate
          </NavLink>
          <NavLink to="/repository" className={({ isActive }) => (isActive ? "on" : "")}>
            Repository
          </NavLink>
          <NavLink to="/dashboard" className={({ isActive }) => (isActive ? "on" : "")}>
            Dashboard
          </NavLink>
        </nav>
        <span className="note">
          {me.email} · {me.role}
        </span>
        <button className="btn quiet" onClick={() => signOut()}>
          Sign out
        </button>
      </div>
      <div className="grad" />

      {(!online || pending > 0) && (
        <div className="offline">
          <strong>{online ? "Syncing" : "Offline"}</strong>
          <span>
            {online
              ? `${pending} observation${pending === 1 ? "" : "s"} waiting to sync.`
              : `Recording locally. ${pending} observation${
                  pending === 1 ? "" : "s"
                } will sync when a network is available.`}
          </span>
          {online && pending > 0 && (
            <button className="btn quiet" onClick={() => void sync()}>
              Sync now
            </button>
          )}
        </div>
      )}

      <div className="wrap">
        <Routes>
          <Route path="/" element={<Navigate to="/evaluate" replace />} />
          <Route path="/evaluate" element={<Evaluate me={me} />} />
          <Route path="/evaluate/:ref" element={<Evaluate me={me} />} />
          <Route path="/repository" element={<Repository />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="*" element={<div className="empty">Not found.</div>} />
        </Routes>
      </div>
    </>
  );
}

function SignIn({ onSignedIn }: { onSignedIn: (m: Me) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const m = await signIn(email, password);
      if (m) onSignedIn(m);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <h1 style={{ fontSize: 18, marginBottom: 4 }}>NAWI Type Evaluation</h1>
      <p className="note" style={{ marginBottom: 18 }}>
        OIML R 76 test reports for non-automatic weighing instruments.
      </p>
      <div className="panel">
        <div className="pad">
          <div className="field">
            <label htmlFor="email">Email</label>
            <input
              id="email"
              value={email}
              autoComplete="username"
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">Password</label>
            <input
              id="password"
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          </div>
          {error && (
            <div className="finding ERROR">
              <span>{error}</span>
            </div>
          )}
          <button className="btn" disabled={busy} onClick={submit}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </div>
      </div>
    </div>
  );
}
