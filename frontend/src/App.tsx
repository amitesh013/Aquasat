import { useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Activity, ArrowDownRight, ArrowUpRight, BarChart3,
  ArrowRight, CalendarDays, ChevronDown, CircleHelp, CloudSun, Database,
  Droplets, Gauge, Layers3, LogOut, RefreshCw, Satellite, ShieldCheck, Waves,
} from "lucide-react";
import {
  Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { createAccount, getAuthStatus, getDashboard, getRainfallCandidate, signIn, signOut } from "./api";
import type { AuthStatus } from "./api";
import type { DashboardData, ForecastRow, HistoryRow, MetricRow, RainfallCandidateResult } from "./types";

const reservoirs = [
  { key: "poondi", name: "Poondi", short: "POO", capacity: 3231, color: "#11796f" },
  { key: "cholavaram", name: "Cholavaram", short: "CHO", capacity: 1081, color: "#438b79" },
  { key: "puzhal", name: "Puzhal (Redhills)", short: "PUZ", capacity: 3300, color: "#d79248" },
  { key: "chembarambakkam", name: "Chembarambakkam", short: "CHE", capacity: 3645, color: "#5683a1" },
  { key: "veeranam", name: "Veeranam", short: "VEE", capacity: 1465, color: "#8975ad" },
];

type Page = "overview" | "reservoir" | "forecast" | "validation" | "method";
const nav: { id: Page; label: string; icon: typeof Waves }[] = [
  { id: "overview", label: "Overview", icon: BarChart3 },
  { id: "reservoir", label: "Reservoir detail", icon: Waves },
  { id: "forecast", label: "Forecast", icon: CalendarDays },
  { id: "validation", label: "Results", icon: Satellite },
  { id: "method", label: "Methodology", icon: CircleHelp },
];

const fmt = (value: number | null | undefined, digits = 0) =>
  value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
const dateLabel = (value: string | null | undefined, options: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric" }) =>
  value ? new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString("en-IN", options) : "—";
const number = (value: unknown): number | null => value == null || value === "" || !Number.isFinite(Number(value)) ? null : Number(value);

function latest<T extends { date?: string; ds?: string }>(rows: T[]) {
  return [...rows].sort((a, b) => (a.date ?? a.ds ?? "").localeCompare(b.date ?? b.ds ?? "")).at(-1);
}

type SiteRoute = "home" | "login" | "signup" | "dashboard";

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
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [checkingAuth, setCheckingAuth] = useState(false);
  const [authError, setAuthError] = useState("");

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

  if (route === "home") return <HomePage onSignIn={() => goTo("/login")} onCreateAccount={() => goTo("/signup")} />;
  if (route === "login") {
    return <LoginPage
      checking={checkingAuth}
      initialError={authError}
      onSignIn={async (usernameOrEmail, password) => { await signIn(usernameOrEmail, password); goTo("/dashboard"); }}
      onCreateAccount={() => goTo("/signup")}
      onHome={() => goTo("/")}
    />;
  }
  if (route === "signup") {
    return <SignUpPage
      checking={checkingAuth}
      initialError={authError}
      onCreateAccount={async (username, email, password) => { await createAccount(username, email, password); goTo("/dashboard"); }}
      onSignIn={() => goTo("/login")}
      onHome={() => goTo("/")}
    />;
  }
  if (checkingAuth || !auth) return <div className="site-loading"><span className="loader" /><strong>Checking your AquaSat access…</strong>{authError && <small>{authError}</small>}</div>;
  if (!auth.authenticated) return <LoginPage
    checking={false}
    initialError={authError}
    onSignIn={async (usernameOrEmail, password) => { await signIn(usernameOrEmail, password); goTo("/dashboard"); }}
    onCreateAccount={() => goTo("/signup")}
    onHome={() => goTo("/")}
  />;
  return <DashboardApp onLogout={async () => { await signOut().catch(() => undefined); goTo("/"); }} />;
}

function HomePage({ onSignIn, onCreateAccount }: { onSignIn: () => void; onCreateAccount: () => void }) {
  return <div className="site-home">
    <header className="site-header">
      <a className="site-brand" href="/" onClick={(event) => { event.preventDefault(); goTo("/"); }}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a>
      <nav aria-label="Public navigation"><a href="#how-it-works">How it works</a><button className="site-create-account" onClick={onCreateAccount}>Create account</button><button className="site-signin" onClick={onSignIn}>Sign in <ArrowRight size={15} /></button></nav>
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
          <div className="visual-chart-card"><div><span>15-DAY OUTLOOK</span><strong>Storage forecast</strong></div><span className="visual-chart-status"><i /> XGBoost</span><svg viewBox="0 0 420 112" role="img" aria-label="Illustrative rising forecast trend"><defs><linearGradient id="waterFill" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#65b5a4" stopOpacity=".22"/><stop offset="1" stopColor="#65b5a4" stopOpacity="0"/></linearGradient></defs><path d="M0 84 C42 82 54 65 90 70 S138 87 170 65 S222 61 250 52 S301 66 325 44 S370 34 420 18 L420 112 L0 112 Z" fill="url(#waterFill)"/><path d="M0 84 C42 82 54 65 90 70 S138 87 170 65 S222 61 250 52 S301 66 325 44 S370 34 420 18" fill="none" stroke="#16796f" strokeWidth="3"/><circle cx="420" cy="18" r="5" fill="#16796f"/><path d="M0 100 H420 M0 62 H420 M0 24 H420" stroke="#e8efec" strokeDasharray="4 6"/></svg><div className="visual-chart-foot"><span>Official storage + satellite history</span><span>Advisory estimate</span></div></div>
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

function LoginPage({ checking, initialError, onSignIn, onCreateAccount, onHome }: {
  checking: boolean;
  initialError: string;
  onSignIn: (usernameOrEmail: string, password: string) => Promise<void>;
  onCreateAccount: () => void;
  onHome: () => void;
}) {
  const [usernameOrEmail, setUsernameOrEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try { await onSignIn(usernameOrEmail, password); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Unable to sign in. Please try again."); }
    finally { setBusy(false); }
  };

  return <div className="login-page">
    <header className="site-header login-header"><a className="site-brand" href="/" onClick={(event) => { event.preventDefault(); onHome(); }}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a><button className="login-home-link" onClick={onHome}>Back to home</button></header>
    <main className="login-layout"><section className="login-aside"><span className="home-eyebrow"><i /> CHENNAI RESERVOIRS, MADE CLEARER</span><h1>See where water stands and what may come next.</h1><p>AquaSat brings reservoir updates, satellite views, recent rainfall and a 15-day outlook together in one easy-to-follow place.</p><div className="login-aside-points"><span><Droplets size={16} /> Follow storage across five reservoirs</span><span><Satellite size={16} /> Compare official updates with satellite views</span><span><CloudSun size={16} /> See recent rainfall beside the outlook</span></div><div className="login-aside-water"><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /><span /></div></section>
      <section className="login-card"><span className="login-lock"><ShieldCheck size={20} /></span><span className="eyebrow">AQUASAT WORKSPACE</span><h2>Welcome back</h2><p>Sign in with your dashboard account.</p>
        {checking ? <div className="auth-message">Checking sign-in service…</div> : <form onSubmit={submit}>
          <label htmlFor="aquasat-username">Username or email</label><input id="aquasat-username" name="username_or_email" type="text" autoComplete="username" minLength={3} maxLength={320} value={usernameOrEmail} onChange={(event) => setUsernameOrEmail(event.target.value)} required />
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

function SignUpPage({ checking, initialError, onCreateAccount, onSignIn, onHome }: {
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
    <header className="site-header login-header"><a className="site-brand" href="/" onClick={(event) => { event.preventDefault(); onHome(); }}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a><button className="login-home-link" onClick={onHome}>Back to home</button></header>
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

function DashboardApp({ onLogout }: { onLogout: () => void }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [rainfallCandidate, setRainfallCandidate] = useState<RainfallCandidateResult | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<Page>("overview");
  const [selected, setSelected] = useState("poondi");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      const [dashboardResult, rainfallResult] = await Promise.all([
        getDashboard(),
        getRainfallCandidate().catch(() => null),
      ]);
      setData(dashboardResult);
      setRainfallCandidate(rainfallResult);
    }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load AquaSat data."); }
    finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, []);

  const selectedReservoir = reservoirs.find((lake) => lake.key === selected) ?? reservoirs[0];
  const selectedHistory = useMemo(() => (data?.history ?? []).filter((row) => row.reservoir === selected), [data, selected]);
  const selectedForecast = useMemo(() => (data?.forecasts ?? []).filter((row) => row.reservoir === selected), [data, selected]);
  const metric = (data?.metrics?.[selected] ?? {}) as MetricRow;

  const overview = page === "overview";
  const title = overview ? "Chennai reservoir storage" : page === "reservoir" ? "Reservoir detail" : page === "forecast" ? "15-day outlook" : page === "validation" ? "Research results" : "How AquaSat works";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#overview" onClick={() => setPage("overview")}><span className="brand-mark"><Droplets size={20} /></span><span>AquaSat<small>WATER INTELLIGENCE</small></span></a>
        <div className="sidebar-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {nav.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-link ${page === id ? "active" : ""}`} onClick={() => setPage(id)}><Icon size={17} />{label}</button>)}
        </nav>
        <div className="sidebar-divider" />
        <div className="sidebar-label">RESERVOIRS</div>
        <div className="lake-nav">
          {reservoirs.map((lake) => <button key={lake.key} className={`lake-nav-item ${selected === lake.key ? "selected" : ""}`} onClick={() => { setSelected(lake.key); setPage("reservoir"); }}><span className="lake-dot" style={{ background: lake.color }} /><span>{lake.name}</span><ChevronDown size={14} className="lake-arrow" /></button>)}
        </div>
        <div className="sidebar-spacer" />
        <div className="service-card"><div className="service-icon"><Database size={16} /></div><div><strong>{data?.data_status.database ?? "Data service"}</strong><small>{data?.data_status.last_synced_at_utc ? `Synced ${dateLabel(data.data_status.last_synced_at_utc, { day: "numeric", month: "short" })}` : "Waiting for API"}</small></div><span className={`service-dot ${error ? "offline" : ""}`} /></div>
        <div className="sidebar-foot">CHENNAI RESERVOIR OUTLOOK<br />Official updates · Satellite views · Rainfall</div>
      </aside>

      <main className="main-content">
        <header className="topbar"><div className="breadcrumbs">AquaSat <span>/</span> Chennai <span>/</span> <b>{title}</b></div><div className="top-actions"><button className="icon-button" title="Refresh data" onClick={() => void refresh()} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /></button><button className="logout-button" onClick={onLogout}><LogOut size={14} /> Sign out</button></div></header>

        <div className="content-wrap">
          {error && <div className="error-banner" role="alert"><div><strong>We couldn’t load the latest reservoir update.</strong><span>Please check your connection and try again.</span></div><button onClick={() => void refresh()}>Try again</button></div>}
          <section className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> CHENNAI · TAMIL NADU</div><h1>{title}</h1><p>Satellite-derived storage estimates and short-range outlooks for five reservoirs.</p></div><div className="heading-meta"><span className="heading-meta-icon"><CalendarDays size={17} /></span><span><small>OFFICIAL READING THROUGH</small><b>{dateLabel(latest((data?.history ?? []).filter((row) => row.official_mcft != null))?.date)}</b></span></div></section>

          <div className="page-tabs">{nav.map(({ id, label }) => <button key={id} className={page === id ? "tab-active" : ""} onClick={() => setPage(id)}>{label}</button>)}</div>

          {loading && !data ? <div className="loading-panel"><span className="loader" /><strong>Connecting to AquaSat data service…</strong><small>Fetching current dashboard history and forecast.</small></div> : data && data.history.length === 0 ? <div className="loading-panel"><Database size={24} /><strong>No dashboard snapshot is in Neon yet.</strong><small>Import the reviewed dashboard files into Neon, then refresh this page.</small></div> : data && <>
            {overview && <Overview data={data} onChoose={(key) => { setSelected(key); setPage("reservoir"); }} />}
            {page === "reservoir" && <ReservoirDetail lake={selectedReservoir} rows={selectedHistory} forecast={selectedForecast} metric={metric} />}
            {page === "forecast" && <ForecastView lake={selectedReservoir} rows={selectedHistory} forecast={selectedForecast} />}
            {page === "validation" && <Validation data={data} selected={selected} rainfallCandidate={rainfallCandidate} />}
            {page === "method" && <Methodology data={data} />}
            <footer className="app-footer"><span><span className="footer-dot" /> Data snapshot · {data.data_status.database}</span><span>Official figures: CMWSSB via data.opencity.in</span><span>Satellite estimates are advisory; verify against official figures.</span></footer>
          </>}
        </div>
      </main>
    </div>
  );
}

function Overview({ data, onChoose }: { data: DashboardData; onChoose: (key: string) => void }) {
  const summary = reservoirs.map((lake) => {
    const history = data.history.filter((row) => row.reservoir === lake.key && row.official_mcft != null);
    const reading = latest(history);
    const forecast = latest(data.forecasts.filter((row) => row.reservoir === lake.key));
    return { lake, reading, forecast };
  });
  const lowCount = summary.filter(({ reading, lake }) => reading?.official_mcft != null && reading.official_mcft / lake.capacity < 0.2).length;
  const newestDate = latest(data.history.filter((row) => row.official_mcft != null))?.date;
  const allCurrent = summary.reduce((total, item) => total + (item.reading?.official_mcft ?? 0), 0);
  const totalCapacity = reservoirs.reduce((total, lake) => total + lake.capacity, 0);
  const records = data.history.filter((row) => row.official_mcft != null).length;
  const chart = [...summary].map(({ lake, reading, forecast }) => ({ reservoir: lake.name.replace(" (Redhills)", ""), storage: reading?.official_mcft ?? null, capacity: lake.capacity, forecast: forecast?.yhat ?? null, fill: lake.color }));
  return <>
    <div className="metric-strip">
      <Metric icon={Droplets} label="Combined storage" value={`${fmt(allCurrent)} mcft`} note={`of ${fmt(totalCapacity)} mcft capacity · ${fmt(allCurrent / totalCapacity * 100, 1)}%`} />
      <Metric icon={Activity} label="Reservoirs below alert" value={`${lowCount} / 5`} note="20% capacity threshold" tone={lowCount ? "warning" : "good"} />
      <Metric icon={CalendarDays} label="Latest official data" value={dateLabel(newestDate, { day: "numeric", month: "short" })} note={dateLabel(newestDate, { year: "numeric" })} />
      <Metric icon={Database} label="Storage observations" value={fmt(records)} note="CMWSSB dates in current snapshot" />
    </div>
    <PipelineOverview data={data} />
    <div className="section-row"><div><h2>Reservoir status</h2><p>Official CMWSSB readings compared with rated capacity.</p></div><button className="text-button" onClick={() => onChoose("poondi")}>View reservoir detail <ArrowUpRight size={15} /></button></div>
    <div className="reservoir-grid">{summary.map(({ lake, reading, forecast }) => {
      const pct = reading?.official_mcft == null ? 0 : reading.official_mcft / lake.capacity * 100;
      const low = pct < 20;
      const forecastPct = forecast?.yhat == null ? null : forecast.yhat / lake.capacity * 100;
      return <button className="reservoir-card" key={lake.key} onClick={() => onChoose(lake.key)}><div className="card-top"><span className="card-code" style={{ color: lake.color, background: `${lake.color}16` }}>{lake.short}</span><span className={`status-pill ${low ? "low" : "normal"}`}><i />{low ? "Low" : "Normal"}</span></div><h3>{lake.name}</h3><div className="capacity-value">{fmt(pct, 0)}<small>%</small></div><div className="capacity-line"><span style={{ width: `${Math.max(2, Math.min(100, pct))}%`, background: lake.color }} /></div><div className="card-foot"><span>{fmt(reading?.official_mcft)} mcft</span><span>of {fmt(lake.capacity)}</span></div><div className="card-bottom"><span>Official · {dateLabel(reading?.date, { day: "numeric", month: "short" })}</span><span>{forecastPct == null ? "No forecast" : `Outlook ${fmt(forecastPct)}%`}</span></div></button>;
    })}</div>
    <div className="content-grid two-col overview-charts"><div className="panel"><div className="panel-heading"><div><h2>Current storage</h2><p>Latest reported official volume by reservoir.</p></div><span className="legend-chip"><i className="legend-dot official" /> CMWSSB</span></div><div className="chart-area short"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chart} margin={{ top: 10, right: 8, bottom: 0, left: -12 }}><CartesianGrid stroke="#e9efee" vertical={false} /><XAxis dataKey="reservoir" tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip formatter={(value) => [`${fmt(Number(value))} mcft`, "Official storage"]} contentStyle={{ borderRadius: 12, border: "1px solid #e3ebe8", boxShadow: "0 12px 32px #244c3e14" }} /><Line dataKey="storage" type="monotone" stroke="#17796f" strokeWidth={2.5} dot={{ r: 4, fill: "#17796f", strokeWidth: 0 }} connectNulls /></ComposedChart></ResponsiveContainer></div></div><div className="panel outlook-panel"><div className="panel-heading"><div><h2>Forecast snapshot</h2><p>Stored 15-day XGBoost endpoint, by lake.</p></div><span className="soft-tag"><Activity size={13} /> Model output</span></div><div className="mini-forecast-list">{summary.map(({ lake, forecast }) => <div className="mini-forecast" key={lake.key}><span className="lake-dot" style={{ background: lake.color }} /><span className="mini-name">{lake.name}</span><strong>{fmt(forecast?.yhat)} <small>mcft</small></strong><span className="mini-range">{forecast?.yhat_lower != null && forecast?.yhat_upper != null ? `${fmt(forecast.yhat_lower)}–${fmt(forecast.yhat_upper)}` : "range n/a"}</span></div>)}</div><div className="note-box"><span>The shaded range shows how forecasts have varied in past comparisons; it is not a probability.</span></div></div></div>
  </>;
}

function PipelineOverview({ data }: { data: DashboardData }) {
  return <section className="pipeline-panel">
    <div className="pipeline-heading"><div><span className="eyebrow">CONNECTED DATA FLOW</span><h2>From reservoir readings to a 15-day outlook</h2></div><span className="api-connected"><i /> API connected · {data.data_status.database}</span></div>
    <div className="pipeline-steps">
      <div className="pipeline-step"><span className="pipeline-icon official-icon"><Droplets size={17} /></span><div><b>Official storage</b><small>CMWSSB observations</small></div></div>
      <span className="pipeline-arrow" aria-hidden="true">→</span>
      <div className="pipeline-step"><span className="pipeline-icon satellite-icon"><Satellite size={17} /></span><div><b>Satellite history</b><small>Sentinel-2 first · Sentinel-1 gap-fill</small></div></div>
      <span className="pipeline-arrow" aria-hidden="true">→</span>
      <div className="pipeline-step"><span className="pipeline-icon forecast-icon"><Activity size={17} /></span><div><b>XGBoost forecast</b><small>Official + S2/S1 + GPM rain</small></div></div>
      <span className="pipeline-arrow" aria-hidden="true">→</span>
      <div className="pipeline-step"><span className="pipeline-icon api-icon"><Database size={17} /></span><div><b>FastAPI + database</b><small>Served to this dashboard</small></div></div>
    </div>
    <div className="pipeline-branch"><CloudSun size={14} /><span>Uses antecedent 7-, 30-, and 90-day GPM rainfall totals available before each forecast origin.</span></div>
  </section>;
}

function Metric({ icon: Icon, label, value, note, tone = "" }: { icon: typeof Waves; label: string; value: string; note: string; tone?: string }) {
  return <div className="metric-card"><div className="metric-icon"><Icon size={16} /></div><div><small>{label}</small><strong className={tone}>{value}</strong><span>{note}</span></div></div>;
}

function ReservoirDetail({ lake, rows, forecast, metric }: { lake: typeof reservoirs[number]; rows: HistoryRow[]; forecast: ForecastRow[]; metric: MetricRow }) {
  const official = rows.filter((row) => row.official_mcft != null);
  const current = latest(official);
  const sat = latest(rows.filter((row) => row.sat_estimate_mcft != null));
  const outlook = latest(forecast);
  const pct = current?.official_mcft == null ? null : current.official_mcft / lake.capacity * 100;
  const chart = rows.filter((row) => row.official_mcft != null || row.sat_estimate_mcft != null).map((row) => ({ date: row.date, official: row.official_mcft, satellite: row.sat_estimate_mcft }));
  const rmse = number(metric.rmse);
  return <>
    <div className="lake-heading"><span className="lake-emblem" style={{ background: `${lake.color}17`, color: lake.color }}><Waves size={20} /></span><div><h2>{lake.name}</h2><p>Rated capacity {fmt(lake.capacity)} mcft · {official.length} official records</p></div><div className={`status-pill ${pct != null && pct < 20 ? "low" : "normal"}`}><i />{pct != null && pct < 20 ? "Below alert" : "Within threshold"}</div></div>
    <div className="metric-strip three-col"><Metric icon={Droplets} label="Official storage" value={`${fmt(current?.official_mcft)} mcft`} note={`${fmt(pct, 1)}% capacity · ${dateLabel(current?.date)}`} /><Metric icon={Satellite} label="Latest satellite estimate" value={`${fmt(sat?.sat_estimate_mcft)} mcft`} note={sat ? `Sentinel-2 · ${dateLabel(sat.date)}` : "No estimate in snapshot"} /><Metric icon={Activity} label="15-day forecast endpoint" value={`${fmt(outlook?.yhat)} mcft`} note={outlook ? `Target ${dateLabel(outlook.ds)}` : "No stored forecast"} /></div>
    <div className="content-grid detail-grid"><div className="panel"><div className="panel-heading"><div><h2>Storage history</h2><p>Official observations and satellite estimate dates.</p></div><div className="legend-group"><span><i className="legend-dot official" /> Official</span><span><i className="legend-dot satellite" /> Satellite</span></div></div><div className="chart-area tall"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chart} margin={{ top: 12, right: 12, bottom: 0, left: -10 }}><CartesianGrid stroke="#e9efee" vertical={false} /><XAxis dataKey="date" tickFormatter={(value) => dateLabel(String(value), { month: "short", year: "2-digit" })} minTickGap={36} tick={{ fill: "#83918f", fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip labelFormatter={(value) => dateLabel(String(value))} formatter={(value, name) => [`${fmt(Number(value))} mcft`, name === "official" ? "CMWSSB official" : "Satellite estimate"]} contentStyle={{ borderRadius: 12, border: "1px solid #e3ebe8" }} /><Line dataKey="official" name="official" type="monotone" stroke="#df8a4b" strokeWidth={1.8} dot={{ r: 2.5, fill: "#df8a4b", strokeWidth: 0 }} connectNulls /><Line dataKey="satellite" name="satellite" type="monotone" stroke={lake.color} strokeWidth={2.2} dot={{ r: 2.6, fill: lake.color, strokeWidth: 0 }} connectNulls /></ComposedChart></ResponsiveContainer></div><div className="chart-note"><span><i className="legend-dot official" /> Official CMWSSB record</span><span><i className="legend-dot satellite" /> Sentinel-2 estimate on usable image dates</span></div></div><div className="panel quality-panel"><div className="panel-heading"><div><h2>Satellite calibration</h2><p>Chronological holdout evaluation.</p></div><Gauge size={18} /></div><div className="quality-value">{rmse == null ? "—" : `${fmt(rmse, 1)} mcft`}<small>RMSE</small></div><div className="quality-rows"><div><span>Model</span><b>{String(metric.model ?? "Not recorded")}</b></div><div><span>Validation</span><b>{String(metric.cv_method ?? "Not recorded")}</b></div><div><span>Holdout pairs</span><b>{fmt(number(metric.n_matched) ?? number(metric.cv_splits))}</b></div><div><span>Bias</span><b>{fmt(number(metric.bias), 1)} mcft</b></div></div><p className="muted-note">This measures satellite storage estimation error, not forecast error. Sparse low-storage holdout observations limit drought conclusions.</p></div></div>
  </>;
}

function ForecastView({ lake, rows, forecast }: { lake: typeof reservoirs[number]; rows: HistoryRow[]; forecast: ForecastRow[] }) {
  const origin = latest(rows.filter((row) => row.official_mcft != null));
  const points = [ ...(origin ? [{ ds: origin.date, actual: origin.official_mcft, yhat: null, low: null, band: null, high: null }] : []), ...forecast.map((row) => ({ ds: row.ds, actual: null, yhat: row.yhat, low: row.yhat_lower, band: row.yhat_lower != null && row.yhat_upper != null ? Math.max(0, row.yhat_upper - row.yhat_lower) : null, high: row.yhat_upper })) ];
  const end = latest(forecast);
  const startVal = origin?.official_mcft;
  const delta = startVal != null && end?.yhat != null ? end.yhat - startVal : null;
  const modelLabel = end?.model?.includes("rainfall") ? "XGBoost · satellite + GPM rainfall" : end?.model ?? "No model metadata";
  const rainfallValues = end?.rainfall_source
    ? `${fmt(end.rainfall_7d_mm, 1)} / ${fmt(end.rainfall_30d_mm, 1)} / ${fmt(end.rainfall_90d_mm, 1)} mm · through ${dateLabel(end.rainfall_feature_through_date)}`
    : "Rainfall feature unavailable";
  return <><div className="panel forecast-panel"><div className="panel-heading"><div><h2>{lake.name} · 15-day outlook</h2><p>Stored forecast from the project’s current XGBoost pipeline.</p></div><span className="soft-tag"><Activity size={13} /> {modelLabel}</span></div><div className="metric-strip three-col embedded"><Metric icon={Gauge} label="Forecast origin" value={`${fmt(startVal)} mcft`} note={dateLabel(origin?.date)} /><Metric icon={CalendarDays} label="Day-15 endpoint" value={`${fmt(end?.day15_prediction_mcft ?? end?.yhat)} mcft`} note={dateLabel(end?.ds)} /><Metric icon={delta != null && delta < 0 ? ArrowDownRight : ArrowUpRight} label="Change from origin" value={`${delta == null ? "—" : `${delta > 0 ? "+" : ""}${fmt(delta)} mcft`}`} note="Model estimate" tone={delta != null && delta < 0 ? "warning" : "good"} /></div><div className="forecast-provenance"><span><b>Satellite input</b>{end?.satellite_source ?? "No prior satellite estimate"}</span><span><b>Image date</b>{dateLabel(end?.satellite_feature_date)}</span><span><b>Image age</b>{end?.satellite_age_days == null ? "—" : String(Math.round(end.satellite_age_days)) + " days"}</span><span><b>GPM rainfall · 7 / 30 / 90 days</b>{rainfallValues}</span><span><b>Generated</b>{end?.generated_at_utc ? dateLabel(end.generated_at_utc) + " UTC" : "Timestamp unavailable"}</span></div><div className="chart-area forecast-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={points} margin={{ top: 14, right: 12, bottom: 0, left: -10 }}><CartesianGrid stroke="#e9efee" vertical={false} /><XAxis dataKey="ds" tickFormatter={(value) => dateLabel(String(value), { day: "numeric", month: "short" })} minTickGap={30} tick={{ fill: "#83918f", fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip labelFormatter={(value) => dateLabel(String(value))} formatter={(value, name) => [`${fmt(Number(value))} mcft`, name === "actual" ? "Official origin" : name === "yhat" ? "Forecast" : name === "low" ? "Approx. lower range" : name === "high" ? "Approx. upper range" : "Range width"]} contentStyle={{ borderRadius: 12, border: "1px solid #e3ebe8" }} /><Area dataKey="low" name="low" stackId="range" type="monotone" stroke="none" fill="transparent" tooltipType="none" /><Area dataKey="band" name="band" stackId="range" type="monotone" stroke="none" fill="#d8e8e4" /><Line dataKey="actual" name="actual" stroke="#df8a4b" strokeWidth={2.5} dot={{ r: 4, fill: "#df8a4b", strokeWidth: 0 }} connectNulls /><Line dataKey="yhat" name="yhat" stroke="#16796f" strokeWidth={2.5} dot={false} connectNulls /></ComposedChart></ResponsiveContainer></div><div className="note-box"><span>{end?.interval_method ?? "The shaded range is an approximate guide based on past forecast errors."}</span></div><p className="muted-note">The model uses point-in-time official storage, the latest prior satellite estimate, and complete GPM 7-, 30-, and 90-day rainfall totals before the forecast origin. Rainfall is sampled at the nearest grid cell to each lake, not averaged over its catchment. Forecasts are advisory and do not replace CMWSSB figures.</p></div></>;
}

function Validation({ data, selected, rainfallCandidate }: { data: DashboardData; selected: string; rainfallCandidate: RainfallCandidateResult | null }) {
  const entries = reservoirs.map((lake) => ({ lake, metric: data.metrics[lake.key] ?? {} }));
  return <>
    <div className="validation-intro"><span className="callout-icon"><Layers3 size={20} /></span><div><h2>Results by stage</h2><p>Satellite storage estimates, 15-day forecasts, and rainfall features are evaluated separately so each result maps to the part of the pipeline it measures.</p></div></div>
    <div className="panel table-panel"><div className="panel-heading"><div><h2>Sentinel-2 storage calibration</h2><p>Later-date evaluation saved with the dashboard metrics.</p></div><span className="soft-tag"><Satellite size={13} /> Sentinel-2</span></div><div className="table-scroll"><table><thead><tr><th>Reservoir</th><th>Model</th><th>Holdout RMSE</th><th>Bias</th><th>Validation</th></tr></thead><tbody>{entries.map(({ lake, metric }) => <tr className={lake.key === selected ? "row-selected" : ""} key={lake.key}><td><span className="table-lake"><i style={{ background: lake.color }} />{lake.name}</span></td><td>{String(metric.model ?? "—")}</td><td>{number(metric.rmse) == null ? "—" : `${fmt(number(metric.rmse), 1)} mcft`}</td><td>{number(metric.bias) == null ? "—" : `${fmt(number(metric.bias), 1)} mcft`}</td><td>{String(metric.cv_method ?? "—")}</td></tr>)}</tbody></table></div></div>
    <div className="content-grid two-col validation-cards">
      <div className="panel"><div className="panel-heading"><div><h2>Sentinel-1 radar gap-fill</h2><p>Secondary satellite history source</p></div><Satellite size={18} /></div><p className="body-copy">Sentinel-2 provides the optical water-area estimate. When a usable Sentinel-2 estimate is missing for a lake and date, the pipeline can use the Sentinel-1 radar estimate as a gap-fill. Both are calibrated against official storage history.</p><div className="pipeline-inline"><span>Sentinel-2</span><b>preferred</b><span>Sentinel-1</span><b>fills gaps</b></div><p className="muted-note">The 15-day XGBoost forecast receives the latest available calibrated satellite estimate as a lag feature.</p></div>
      <div className="panel"><div className="panel-heading"><div><h2>Historical IMD rainfall study</h2><p>Standard IMD grid archive · 2019–2024</p></div><CloudSun size={18} /></div><p className="rainfall-score"><strong>221.5</strong><span>mcft baseline RMSE</span><span className="versus-mark">→</span><strong>225.8</strong><span>mcft with rainfall</span></p><p className="body-copy">This earlier study used the standard IMD archive. The live forecast now uses the separate merged GPM product; their results should not be treated as the same rainfall series.</p></div>
    </div>
    {rainfallCandidate ? <RainfallCandidateCard result={rainfallCandidate} /> : <div className="panel candidate-unavailable"><CloudSun size={17} /><span>The saved GPM rainfall candidate result is not available from the research API.</span></div>}
  </>;
}

function RainfallCandidateCard({ result }: { result: RainfallCandidateResult }) {
  const overallDelta = result.all_pairs.rainfall_rmse_mcft - result.all_pairs.baseline_rmse_mcft;
  const developmentDelta = result.development.rainfall_rmse_mcft - result.development.baseline_rmse_mcft;
  const recentDelta = result.september_october_2026.rainfall_rmse_mcft - result.september_october_2026.baseline_rmse_mcft;
  return <section className="panel rainfall-candidate-panel">
    <div className="panel-heading"><div><span className="eyebrow">POOLED-MODEL RAINFALL COMPARISON</span><h2>{result.title}</h2><p>{result.rainfall_source} · comparison history through {dateLabel(result.rainfall_last_date)}</p></div><span className="soft-tag"><CloudSun size={13} /> {result.rainfall_windows_days.join(" / ")}-day rainfall totals</span></div>
    <div className="candidate-feature-line"><span>{result.model}</span><span>{fmt(result.paired_forecasts)} paired forecasts</span><span>{fmt(result.disputed_source_pairs)} disputed lake/date pairs excluded</span><span>{result.satellite_features_used ? "Satellite features included" : "Satellite features not included in this isolated rainfall comparison"}</span></div>
    <div className="candidate-score-grid">
      <article className="candidate-score"><span className="candidate-period">ALL PAIRED DATES · {fmt(result.all_pairs.pairs)} PAIRS</span><strong>{fmt(result.all_pairs.baseline_rmse_mcft, 2)} <i>→</i> {fmt(result.all_pairs.rainfall_rmse_mcft, 2)}</strong><small>RMSE mcft · change {overallDelta > 0 ? "+" : ""}{fmt(overallDelta, 2)} mcft</small><div className="candidate-normalized">Capacity-normalized RMSE {fmt(result.all_pairs.baseline_rmse_pct_capacity, 2)}% → {fmt(result.all_pairs.rainfall_rmse_pct_capacity, 2)}%</div><div className="candidate-mae">MAE {fmt(result.all_pairs.baseline_mae_mcft, 2)} → {fmt(result.all_pairs.rainfall_mae_mcft, 2)} mcft</div></article>
      <article className="candidate-score"><span className="candidate-period">DEVELOPMENT · {fmt(result.development.pairs)} PAIRS</span><strong>{fmt(result.development.baseline_rmse_mcft, 2)} <i>→</i> {fmt(result.development.rainfall_rmse_mcft, 2)}</strong><small>RMSE mcft · {fmt(Math.abs(developmentDelta), 2)} mcft lower with rainfall</small><div className="candidate-normalized">Capacity-normalized RMSE {fmt(result.development.baseline_rmse_pct_capacity, 2)}% → {fmt(result.development.rainfall_rmse_pct_capacity, 2)}%</div><div className="candidate-mae">MAE {fmt(result.development.baseline_mae_mcft, 2)} → {fmt(result.development.rainfall_mae_mcft, 2)} mcft</div></article>
      <article className="candidate-score recent"><span className="candidate-period">SEP–OCT 2026 · {fmt(result.september_october_2026.pairs)} PAIRS</span><strong>{fmt(result.september_october_2026.baseline_rmse_mcft, 2)} <i>→</i> {fmt(result.september_october_2026.rainfall_rmse_mcft, 2)}</strong><small>RMSE mcft · change {recentDelta > 0 ? "+" : ""}{fmt(recentDelta, 2)} mcft</small><div className="candidate-normalized">Capacity-normalized RMSE {fmt(result.september_october_2026.baseline_rmse_pct_capacity, 2)}% → {fmt(result.september_october_2026.rainfall_rmse_pct_capacity, 2)}%</div><div className="candidate-mae">MAE {fmt(result.september_october_2026.baseline_mae_mcft, 2)} → {fmt(result.september_october_2026.rainfall_mae_mcft, 2)} mcft</div></article>
    </div>
    <div className="candidate-foot"><span>{result.rainfall_sampling}. Only rainfall before each forecast origin is used.</span><span>Live forecast input: {result.live_model ?? (result.forecast_model_includes_rainfall ? "official storage + satellite history + rainfall" : "official storage + available satellite lag")}; latest source date {dateLabel(result.live_rainfall_last_date ?? result.rainfall_last_date)}.</span></div>
  </section>;
}

function Methodology({ data }: { data: DashboardData }) {
  return <><div className="method-banner"><div className="method-banner-icon"><Waves size={22} /></div><div><span className="eyebrow">AQUASAT PIPELINE</span><h2>From official readings to an advisory 15-day outlook</h2><p>Five Chennai reservoirs · calibrated satellite history · rainfall-aware XGBoost · FastAPI-served results.</p></div></div><div className="method-grid"><MethodStep number="01" title="Observe water area" text="Sentinel-2 optical scenes provide the preferred image-based estimate. Sentinel-1 radar is available as a history gap-fill when a usable S2 estimate is missing." icon={Satellite} /><MethodStep number="02" title="Calibrate to storage" text="Water area is converted to storage using reservoir-specific calibration against exact-date CMWSSB readings. Satellite and forecast errors are reported separately." icon={Gauge} /><MethodStep number="03" title="Forecast 15 days" text="XGBoost combines official storage history, the latest prior calibrated S2/S1 satellite estimate, and complete prior 7-, 30-, and 90-day merged GPM rainfall totals." icon={Activity} /><MethodStep number="04" title="Serve the results" text={`FastAPI reads the synchronized ${data.data_status.database} snapshot and returns dashboard history, rainfall-assisted forecast rows, metrics, and the paired rainfall comparison.`} icon={Database} /></div><div className="panel limits-panel"><div className="panel-heading"><div><h2>Reading the dashboard</h2><p>What each result represents.</p></div><Layers3 size={18} /></div><ul><li>Official CMWSSB readings are the reference for current storage.</li><li>Sentinel-2 is the preferred optical estimate; Sentinel-1 fills history gaps when no usable S2 estimate is present.</li><li>Rainfall totals use only days before the forecast origin and come from the nearest GPM grid cell to each reservoir.</li><li>The forecast line ends at a model-generated day-15 value; the daily path between dates is interpolated.</li><li>Shaded forecast ranges use rolling-origin errors from the rainfall-assisted model and are approximate, not calibrated probabilities.</li></ul></div></>;
}

function MethodStep({ number: n, title, text, icon: Icon }: { number: string; title: string; text: string; icon: typeof Waves }) {
  return <article className="method-step"><span className="step-number">{n}</span><span className="step-icon"><Icon size={18} /></span><h3>{title}</h3><p>{text}</p></article>;
}
