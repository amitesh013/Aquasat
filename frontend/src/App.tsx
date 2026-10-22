import { lazy, Suspense, useEffect, useState, type FormEvent } from "react";
import {
  Activity, ArrowRight, CloudSun, Droplets, Moon, Satellite, ShieldCheck, Sun,
} from "lucide-react";
import { createAccount, getAuthStatus, signIn, signOut } from "./api";
import type { AuthStatus } from "./api";

const DashboardApp = lazy(() => import("./DashboardApp"));

type SiteRoute = "home" | "login" | "signup" | "dashboard";
type Theme = "light" | "dark";

function initialTheme(): Theme {
  try {
    const saved = window.localStorage.getItem("aquasat-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Continue with the system preference if browser storage is unavailable.
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function routeFromPath(pathname: string): SiteRoute {
  if (pathname.startsWith("/dashboard")) return "dashboard";
  if (pathname.startsWith("/login")) return "login";
  if (pathname.startsWith("/signup") || pathname.startsWith("/register")) return "signup";
  return "home";
}

function goTo(path: string) {
  if (window.location.pathname !== path) window.history.pushState({}, "", path);
  window.dispatchEvent(new Event("popstate"));
}

export default function App() {
  const [route, setRoute] = useState<SiteRoute>(() => routeFromPath(window.location.pathname));
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [checkingAuth, setCheckingAuth] = useState(false);
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { window.localStorage.setItem("aquasat-theme", theme); } catch { /* Theme still applies for this visit. */ }
  }, [theme]);

  const toggleTheme = () => setTheme((current) => current === "light" ? "dark" : "light");

  useEffect(() => {
    const onPopState = () => setRoute(routeFromPath(window.location.pathname));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  useEffect(() => {
    if (route === "home") return;
    let active = true;
    setCheckingAuth(true);
    setAuthError("");
    getAuthStatus()
      .then((status) => {
        if (!active) return;
        setAuth(status);
        if (route === "dashboard" && !status.authenticated) goTo("/login");
        if (route !== "dashboard" && status.authenticated) goTo("/dashboard");
      })
      .catch((error: unknown) => {
        if (active) setAuthError(error instanceof Error ? error.message : "Could not reach the sign-in service.");
      })
      .finally(() => { if (active) setCheckingAuth(false); });
    return () => { active = false; };
  }, [route]);

  if (route === "home") return <HomePage theme={theme} onToggleTheme={toggleTheme} onSignIn={() => goTo("/login")} onCreateAccount={() => goTo("/signup")} />;
  if (route === "login") {
    return <LoginPage
      theme={theme}
      onToggleTheme={toggleTheme}
      checking={checkingAuth}
      initialError={authError}
      onSignIn={async (username, password) => { await signIn(username, password); goTo("/dashboard"); }}
      onCreateAccount={() => goTo("/signup")}
      onHome={() => goTo("/")}
    />;
  }
  if (route === "signup") {
    return <SignUpPage
      theme={theme}
      onToggleTheme={toggleTheme}
      checking={checkingAuth}
      initialError={authError}
      onCreateAccount={async (username, email, password) => { await createAccount(username, email, password); goTo("/dashboard"); }}
      onSignIn={() => goTo("/login")}
      onHome={() => goTo("/")}
    />;
  }
  if (checkingAuth || !auth) return <div className="site-loading"><span className="loader" /><strong>Checking your AquaSat access…</strong>{authError && <small>{authError}</small>}</div>;
  if (!auth.authenticated) return <LoginPage
    theme={theme}
    onToggleTheme={toggleTheme}
    checking={false}
    initialError={authError}
    onSignIn={async (username, password) => { await signIn(username, password); goTo("/dashboard"); }}
    onCreateAccount={() => goTo("/signup")}
    onHome={() => goTo("/")}
  />;
  return <Suspense fallback={<div className="site-loading"><span className="loader" /><strong>Opening your AquaSat dashboard…</strong></div>}><DashboardApp theme={theme} onToggleTheme={toggleTheme} onLogout={async () => { await signOut().catch(() => undefined); goTo("/"); }} /></Suspense>;
}

function ThemeToggle({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  const label = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
  const Icon = theme === "light" ? Moon : Sun;
  return <button className="theme-toggle" type="button" onClick={onToggle} aria-label={label} aria-pressed={theme === "dark"} title={label}><Icon size={16} /><span>{theme === "light" ? "Dark" : "Light"}</span></button>;
}

function HomePage({ theme, onToggleTheme, onSignIn, onCreateAccount }: { theme: Theme; onToggleTheme: () => void; onSignIn: () => void; onCreateAccount: () => void }) {
  return <div className="site-home">
    <header className="site-header">
      <a className="site-brand" href="/" onClick={(event) => { event.preventDefault(); goTo("/"); }}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a>
      <nav aria-label="Public navigation"><a href="#how-it-works">How it works</a><button className="site-create-account" onClick={onCreateAccount}>Create account</button><ThemeToggle theme={theme} onToggle={onToggleTheme} /><button className="site-signin" onClick={onSignIn}>Sign in <ArrowRight size={15} /></button></nav>
    </header>
    <main>
      <section className="home-hero">
        <div className="home-copy">
          <span className="home-eyebrow"><i /> CHENNAI RESERVOIR INTELLIGENCE</span>
          <h1>See the water.<br /><em>Plan what comes next.</em></h1>
          <p>AquaSat brings official reservoir readings, satellite-derived storage estimates, rainfall context, and a 15-day outlook together in one research dashboard.</p>
          <div className="home-actions"><button className="primary-cta" onClick={onCreateAccount}>Create your account <ArrowRight size={16} /></button><a href="/login" onClick={(event) => { event.preventDefault(); onSignIn(); }}>Already have an account? Sign in</a></div>
          <div className="home-coverage"><span><b>5</b> reservoirs</span><i /><span><b>15</b> day outlook</span><i /><span><b>3</b> data sources</span></div>
        </div>
        <div className="home-visual" aria-label="Illustration of reservoir storage and forecast">
          <div className="visual-topline"><span><i /> CHENNAI RESERVOIRS</span><span>CHENNAI · TN</span></div>
          <div className="visual-water"><div className="visual-sun" /><div className="visual-hill hill-one" /><div className="visual-hill hill-two" /><div className="visual-ripple ripple-one" /><div className="visual-ripple ripple-two" /><div className="visual-ripple ripple-three" /><div className="visual-water-label"><Droplets size={17} /><span>Reservoirs in focus</span><b>CMWSSB · Sentinel · GPM</b></div></div>
          <div className="visual-chart-card"><div><span>15-DAY OUTLOOK</span><strong>Storage forecast</strong></div><span className="visual-chart-status"><i /> XGBoost</span><svg viewBox="0 0 420 112" role="img" aria-label="Illustrative rising forecast trend"><defs><linearGradient id="waterFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#65b5a4" stopOpacity=".22"/><stop offset="1" stopColor="#65b5a4" stopOpacity="0"/></linearGradient></defs><path d="M0 84 C42 82 54 65 90 70 S138 87 170 65 S222 61 250 52 S301 66 325 44 S370 34 420 18 L420 112 L0 112 Z" fill="url(#waterFill)"/><path d="M0 84 C42 82 54 65 90 70 S138 87 170 65 S222 61 250 52 S301 66 325 44 S370 34 420 18" fill="none" stroke="#1769aa" strokeWidth="3"/><circle cx="420" cy="18" r="5" fill="#1769aa"/><path d="M0 100 H420 M0 62 H420 M0 24 H420" stroke="#e8efec" strokeDasharray="4 6"/></svg><div className="visual-chart-foot"><span>Official storage + satellite history</span><span>Advisory estimate</span></div></div>
          <div className="visual-float"><span className="float-icon"><ShieldCheck size={15} /></span><span><b>Evidence-led</b><small>Each source kept traceable</small></span></div>
        </div>
      </section>
      <section className="home-trust-strip">
        <div><span className="trust-icon"><Droplets size={17} /></span><span><b>Official reservoir updates</b><small>See storage figures published by Chennai’s water authority.</small></span></div>
        <div><span className="trust-icon satellite"><Satellite size={17} /></span><span><b>Views from satellites</b><small>Use clear images to follow water area, with radar to help on cloudy dates.</small></span></div>
        <div><span className="trust-icon rain"><CloudSun size={17} /></span><span><b>Recent rainfall</b><small>Review rain over the past week, month and three months.</small></span></div>
        <div><span className="trust-icon forecast"><Activity size={17} /></span><span><b>A 15-day outlook</b><small>See an estimate of how reservoir storage may change.</small></span></div>
      </section>
      <section className="home-approach" id="how-it-works">
        <div className="approach-heading"><span className="eyebrow"><span className="eyebrow-line" /> HOW AQUASAT HELPS</span><h2>From today’s reservoir picture to a 15-day outlook.</h2><p>Understand what is reported, what is estimated, and what may change.</p></div>
        <div className="approach-grid">
          <article><span>01</span><Droplets size={19} /><h3>Start with reported storage</h3><p>See the latest storage readings from CMWSSB and how each reservoir has changed over time.</p></article>
          <article><span>02</span><Satellite size={19} /><h3>Add another view</h3><p>Satellite images help track water area. Radar can help when clouds block a clear optical image.</p></article>
          <article><span>03</span><CloudSun size={19} /><h3>Look ahead with context</h3><p>Recent storage, satellite views and rainfall from the past 7, 30 and 90 days inform an advisory 15-day estimate.</p></article>
        </div>
        <div className="home-disclaimer">CMWSSB reports remain the official storage reference. AquaSat’s outlook is an estimate to help you explore what may come next.</div>
      </section>
    </main>
    <footer className="site-footer"><a className="site-brand compact" href="/" onClick={(event) => { event.preventDefault(); goTo("/"); }}><span className="brand-mark"><Droplets size={17} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a><span>Chennai reservoir research dashboard</span><button onClick={onSignIn}>Sign in <ArrowRight size={14} /></button></footer>
  </div>;
}

function LoginPage({ theme, onToggleTheme, checking, initialError, onSignIn, onCreateAccount, onHome }: {
  theme: Theme;
  onToggleTheme: () => void;
  checking: boolean;
  initialError: string;
  onSignIn: (username: string, password: string) => Promise<void>;
  onCreateAccount: () => void;
  onHome: () => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try { await onSignIn(username, password); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to sign in. Please try again."); }
    finally { setBusy(false); }
  };

  return <div className="login-page">
    <header className="site-header login-header"><a className="site-brand" href="/" onClick={(event) => { event.preventDefault(); onHome(); }}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a><div className="login-header-actions"><ThemeToggle theme={theme} onToggle={onToggleTheme} /><button className="login-home-link" onClick={onHome}>Back to home</button></div></header>
    <main className="login-layout"><section className="login-aside"><span className="home-eyebrow"><i /> CHENNAI RESERVOIRS, MADE CLEARER</span><h1>See where water stands and what may come next.</h1><p>AquaSat brings reservoir updates, satellite views, recent rainfall and a 15-day outlook together in one easy-to-follow place.</p><div className="login-aside-points"><span><Droplets size={16} /> Follow storage across five reservoirs</span><span><Satellite size={16} /> Compare official updates with satellite views</span><span><CloudSun size={16} /> See recent rainfall beside the outlook</span></div><div className="login-aside-water"><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /></div></section>
      <section className="login-card"><span className="login-lock"><ShieldCheck size={20} /></span><span className="eyebrow">AQUASAT WORKSPACE</span><h2>Welcome back</h2><p>Sign in with your dashboard account.</p>
        {checking ? <div className="auth-message">Checking sign-in service…</div> : <form onSubmit={submit}>
          <label htmlFor="aquasat-username">Username</label><input id="aquasat-username" name="username" type="text" autoComplete="username" minLength={3} maxLength={30} value={username} onChange={(event) => setUsername(event.target.value)} required />
          <label htmlFor="aquasat-password">Password</label><input id="aquasat-password" name="password" type="password" autoComplete="current-password" maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required />
          {(error || initialError) && <div className="auth-message error" role="alert">{error || initialError}</div>}
          <button className="primary-cta login-submit" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in securely"}<ArrowRight size={16} /></button>
        </form>}
        <button className="login-back" onClick={onCreateAccount}>New to AquaSat? Create an account</button>
        <button className="login-back" onClick={onHome}>Return to AquaSat home</button>
      </section>
    </main>
    <footer className="login-footer">AquaSat · Chennai reservoir research</footer>
  </div>;
}

function SignUpPage({ theme, onToggleTheme, checking, initialError, onCreateAccount, onSignIn, onHome }: {
  theme: Theme;
  onToggleTheme: () => void;
  checking: boolean;
  initialError: string;
  onCreateAccount: (username: string, email: string, password: string) => Promise<void>;
  onSignIn: () => void;
  onHome: () => void;
}) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    if (password !== confirmPassword) {
      setError("The passwords do not match.");
      return;
    }
    setBusy(true);
    try { await onCreateAccount(username, email, password); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to create your account. Please try again."); }
    finally { setBusy(false); }
  };

  return <div className="login-page">
    <header className="site-header login-header"><a className="site-brand" href="/" onClick={(event) => { event.preventDefault(); onHome(); }}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a><div className="login-header-actions"><ThemeToggle theme={theme} onToggle={onToggleTheme} /><button className="login-home-link" onClick={onHome}>Back to home</button></div></header>
    <main className="login-layout"><section className="login-aside"><span className="home-eyebrow"><i /> YOUR RESERVOIR VIEW, IN ONE PLACE</span><h1>Make the water picture easier to follow.</h1><p>Create an account to explore reported storage, satellite views, recent rainfall and the 15-day outlook for Chennai’s reservoirs.</p><div className="login-aside-points"><span><Droplets size={16} /> Updates for five reservoirs</span><span><Satellite size={16} /> Official readings alongside satellite estimates</span><span><CloudSun size={16} /> Rainfall context and a short-range outlook</span></div><div className="login-aside-water"><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /></div></section>
      <section className="login-card"><span className="login-lock"><ShieldCheck size={20} /></span><span className="eyebrow">AQUASAT WORKSPACE</span><h2>Create account</h2><p>Choose a username, add your email, and create a password with at least 6 characters.</p>
        {checking ? <div className="auth-message">Checking sign-in service…</div> : <form onSubmit={submit}>
          <label htmlFor="aquasat-signup-username">Username</label><input id="aquasat-signup-username" name="username" type="text" autoComplete="username" minLength={3} maxLength={30} pattern="[A-Za-z0-9][A-Za-z0-9._-]{1,28}[A-Za-z0-9]" title="Use 3–30 letters, numbers, dots, underscores, or hyphens; begin and end with a letter or number." value={username} onChange={(event) => setUsername(event.target.value)} required />
          <label htmlFor="aquasat-signup-email">Email</label><input id="aquasat-signup-email" name="email" type="email" autoComplete="email" maxLength={320} value={email} onChange={(event) => setEmail(event.target.value)} required />
          <label htmlFor="aquasat-signup-password">Password</label><input id="aquasat-signup-password" name="new-password" type="password" autoComplete="new-password" minLength={6} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required />
          <label htmlFor="aquasat-confirm-password">Confirm password</label><input id="aquasat-confirm-password" name="confirm-password" type="password" autoComplete="new-password" minLength={6} maxLength={128} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required />
          {(error || initialError) && <div className="auth-message error" role="alert">{error || initialError}</div>}
          <button className="primary-cta login-submit" type="submit" disabled={busy}>{busy ? "Creating account…" : "Create account"}<ArrowRight size={16} /></button>
        </form>}
        <div className="auth-message">No email verification or password recovery is set up yet. Choose a password you can keep safe.</div>
        <button className="login-back" onClick={onSignIn}>Already have an account? Sign in</button>
      </section>
    </main>
    <footer className="login-footer">AquaSat · Chennai reservoir research</footer>
  </div>;
}
