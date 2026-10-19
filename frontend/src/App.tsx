import { useEffect, useMemo, useState } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, BarChart3,
  CalendarDays, ChevronDown, CircleHelp, CloudSun, Database,
  Droplets, Gauge, Layers3, RefreshCw, Satellite, Waves,
} from "lucide-react";
import {
  Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";
import { getDashboard } from "./api";
import type { DashboardData, ForecastRow, HistoryRow, MetricRow } from "./types";

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
  { id: "validation", label: "Validation", icon: Satellite },
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

function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState<Page>("overview");
  const [selected, setSelected] = useState("poondi");

  const refresh = async () => {
    setLoading(true);
    setError("");
    try { setData(await getDashboard()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load AquaSat data."); }
    finally { setLoading(false); }
  };

  useEffect(() => { void refresh(); }, []);

  const selectedReservoir = reservoirs.find((lake) => lake.key === selected) ?? reservoirs[0];
  const selectedHistory = useMemo(() => (data?.history ?? []).filter((row) => row.reservoir === selected), [data, selected]);
  const selectedForecast = useMemo(() => (data?.forecasts ?? []).filter((row) => row.reservoir === selected), [data, selected]);
  const metric = (data?.metrics?.[selected] ?? {}) as MetricRow;

  const overview = page === "overview";
  const title = overview ? "Chennai reservoir storage" : page === "reservoir" ? "Reservoir detail" : page === "forecast" ? "15-day outlook" : page === "validation" ? "Model validation" : "How AquaSat works";

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
        <div className="sidebar-foot">ADVISORY RESEARCH DASHBOARD<br />CMWSSB · Sentinel-2 · IMD</div>
      </aside>

      <main className="main-content">
        <header className="topbar"><div className="breadcrumbs">AquaSat <span>/</span> Chennai <span>/</span> <b>{title}</b></div><div className="top-actions"><span className="live-badge"><span /> Research prototype</span><button className="icon-button" title="Refresh data" onClick={() => void refresh()} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /></button><button className="avatar" aria-label="AquaSat">A</button></div></header>

        <div className="content-wrap">
          {error && <div className="error-banner"><AlertTriangle size={18} /><div><strong>Dashboard data could not load</strong><span>{error}. Start the FastAPI service and refresh. Your database and source CSV files are unchanged.</span></div><button onClick={() => void refresh()}>Retry</button></div>}
          <section className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> CHENNAI · TAMIL NADU</div><h1>{title}</h1><p>Satellite-derived storage estimates and short-range outlooks for five reservoirs.</p></div><div className="heading-meta"><span className="heading-meta-icon"><CalendarDays size={17} /></span><span><small>OFFICIAL READING THROUGH</small><b>{dateLabel(latest((data?.history ?? []).filter((row) => row.official_mcft != null))?.date)}</b></span></div></section>

          <div className="page-tabs">{nav.map(({ id, label }) => <button key={id} className={page === id ? "tab-active" : ""} onClick={() => setPage(id)}>{label}</button>)}</div>

          {loading && !data ? <div className="loading-panel"><span className="loader" /><strong>Connecting to AquaSat data service…</strong><small>Fetching current dashboard history and forecast.</small></div> : data && <>
            {overview && <Overview data={data} onChoose={(key) => { setSelected(key); setPage("reservoir"); }} />}
            {page === "reservoir" && <ReservoirDetail lake={selectedReservoir} rows={selectedHistory} forecast={selectedForecast} metric={metric} />}
            {page === "forecast" && <ForecastView lake={selectedReservoir} rows={selectedHistory} forecast={selectedForecast} />}
            {page === "validation" && <Validation data={data} selected={selected} />}
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
    <div className="section-row"><div><h2>Reservoir status</h2><p>Official CMWSSB readings compared with rated capacity.</p></div><button className="text-button" onClick={() => onChoose("poondi")}>View reservoir detail <ArrowUpRight size={15} /></button></div>
    <div className="reservoir-grid">{summary.map(({ lake, reading, forecast }) => {
      const pct = reading?.official_mcft == null ? 0 : reading.official_mcft / lake.capacity * 100;
      const low = pct < 20;
      const forecastPct = forecast?.yhat == null ? null : forecast.yhat / lake.capacity * 100;
      return <button className="reservoir-card" key={lake.key} onClick={() => onChoose(lake.key)}><div className="card-top"><span className="card-code" style={{ color: lake.color, background: `${lake.color}16` }}>{lake.short}</span><span className={`status-pill ${low ? "low" : "normal"}`}><i />{low ? "Low" : "Normal"}</span></div><h3>{lake.name}</h3><div className="capacity-value">{fmt(pct, 0)}<small>%</small></div><div className="capacity-line"><span style={{ width: `${Math.max(2, Math.min(100, pct))}%`, background: lake.color }} /></div><div className="card-foot"><span>{fmt(reading?.official_mcft)} mcft</span><span>of {fmt(lake.capacity)}</span></div><div className="card-bottom"><span>Official · {dateLabel(reading?.date, { day: "numeric", month: "short" })}</span><span>{forecastPct == null ? "No forecast" : `Outlook ${fmt(forecastPct)}%`}</span></div></button>;
    })}</div>
    <div className="content-grid two-col overview-charts"><div className="panel"><div className="panel-heading"><div><h2>Current storage</h2><p>Latest reported official volume by reservoir.</p></div><span className="legend-chip"><i className="legend-dot official" /> CMWSSB</span></div><div className="chart-area short"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={chart} margin={{ top: 10, right: 8, bottom: 0, left: -12 }}><CartesianGrid stroke="#e9efee" vertical={false} /><XAxis dataKey="reservoir" tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip formatter={(value) => [`${fmt(Number(value))} mcft`, "Official storage"]} contentStyle={{ borderRadius: 12, border: "1px solid #e3ebe8", boxShadow: "0 12px 32px #244c3e14" }} /><Line dataKey="storage" type="monotone" stroke="#17796f" strokeWidth={2.5} dot={{ r: 4, fill: "#17796f", strokeWidth: 0 }} connectNulls /></ComposedChart></ResponsiveContainer></div></div><div className="panel outlook-panel"><div className="panel-heading"><div><h2>Forecast snapshot</h2><p>Stored 15-day XGBoost endpoint, by lake.</p></div><span className="soft-tag"><Activity size={13} /> Model output</span></div><div className="mini-forecast-list">{summary.map(({ lake, forecast }) => <div className="mini-forecast" key={lake.key}><span className="lake-dot" style={{ background: lake.color }} /><span className="mini-name">{lake.name}</span><strong>{fmt(forecast?.yhat)} <small>mcft</small></strong><span className="mini-range">{forecast?.yhat_lower != null && forecast?.yhat_upper != null ? `${fmt(forecast.yhat_lower)}–${fmt(forecast.yhat_upper)}` : "range n/a"}</span></div>)}</div><div className="note-box"><AlertTriangle size={15} /><span>Forecast bands are approximate backtest-error ranges, not probabilities.</span></div></div></div>
  </>;
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
  return <><div className="panel forecast-panel"><div className="panel-heading"><div><h2>{lake.name} · 15-day outlook</h2><p>Stored forecast from the project’s current XGBoost pipeline.</p></div><span className="soft-tag"><Activity size={13} /> {end?.model ?? "No model metadata"}</span></div><div className="metric-strip three-col embedded"><Metric icon={Gauge} label="Forecast origin" value={`${fmt(startVal)} mcft`} note={dateLabel(origin?.date)} /><Metric icon={CalendarDays} label="Day-15 endpoint" value={`${fmt(end?.day15_prediction_mcft ?? end?.yhat)} mcft`} note={dateLabel(end?.ds)} /><Metric icon={delta != null && delta < 0 ? ArrowDownRight : ArrowUpRight} label="Change from origin" value={`${delta == null ? "—" : `${delta > 0 ? "+" : ""}${fmt(delta)} mcft`}`} note="Model estimate" tone={delta != null && delta < 0 ? "warning" : "good"} /></div><div className="chart-area forecast-chart"><ResponsiveContainer width="100%" height="100%"><ComposedChart data={points} margin={{ top: 14, right: 12, bottom: 0, left: -10 }}><CartesianGrid stroke="#e9efee" vertical={false} /><XAxis dataKey="ds" tickFormatter={(value) => dateLabel(String(value), { day: "numeric", month: "short" })} minTickGap={30} tick={{ fill: "#83918f", fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#83918f", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip labelFormatter={(value) => dateLabel(String(value))} formatter={(value, name) => [`${fmt(Number(value))} mcft`, name === "actual" ? "Official origin" : name === "yhat" ? "Forecast" : name === "low" ? "Approx. lower range" : name === "high" ? "Approx. upper range" : "Range width"]} contentStyle={{ borderRadius: 12, border: "1px solid #e3ebe8" }} /><Area dataKey="low" name="low" stackId="range" type="monotone" stroke="none" fill="transparent" tooltipType="none" /><Area dataKey="band" name="band" stackId="range" type="monotone" stroke="none" fill="#d8e8e4" /><Line dataKey="actual" name="actual" stroke="#df8a4b" strokeWidth={2.5} dot={{ r: 4, fill: "#df8a4b", strokeWidth: 0 }} connectNulls /><Line dataKey="yhat" name="yhat" stroke="#16796f" strokeWidth={2.5} dot={false} connectNulls /></ComposedChart></ResponsiveContainer></div><div className="note-box"><AlertTriangle size={15} /><span>{end?.interval_method ?? "The forecast range is approximate and should not be interpreted as a calibrated probability interval."}</span></div><p className="muted-note">The model uses point-in-time official storage history and the available prior satellite estimate as a lag feature. Historical backtests are mixed by reservoir; the forecast is advisory and does not replace CMWSSB figures.</p></div></>;
}

function Validation({ data, selected }: { data: DashboardData; selected: string }) {
  const entries = reservoirs.map((lake) => ({ lake, metric: data.metrics[lake.key] ?? {} }));
  return <><div className="validation-intro"><span className="callout-icon"><Layers3 size={20} /></span><div><h2>Read accuracy in context</h2><p>Satellite calibration and forecast backtests measure different steps. Scores below are descriptive holdout evidence, not a claim of operational accuracy.</p></div></div><div className="panel table-panel"><div className="panel-heading"><div><h2>Satellite storage calibration</h2><p>Chronological evaluation metrics currently saved in the dashboard snapshot.</p></div><span className="soft-tag"><Satellite size={13} /> Sentinel-2</span></div><div className="table-scroll"><table><thead><tr><th>Reservoir</th><th>Model</th><th>Holdout RMSE</th><th>Bias</th><th>Validation</th></tr></thead><tbody>{entries.map(({ lake, metric }) => <tr className={lake.key === selected ? "row-selected" : ""} key={lake.key}><td><span className="table-lake"><i style={{ background: lake.color }} />{lake.name}</span></td><td>{String(metric.model ?? "—")}</td><td>{number(metric.rmse) == null ? "—" : `${fmt(number(metric.rmse), 1)} mcft`}</td><td>{number(metric.bias) == null ? "—" : `${fmt(number(metric.bias), 1)} mcft`}</td><td>{String(metric.cv_method ?? "—")}</td></tr>)}</tbody></table></div></div><div className="content-grid two-col validation-cards"><div className="panel"><div className="panel-heading"><div><h2>Sentinel-1 radar fallback</h2><p>Experimental gap-fill candidate.</p></div><Satellite size={18} /></div><p className="body-copy">Sentinel-1 uses VV/VH radar backscatter thresholds, not NDWI or MNDWI. Its estimates were selected on earlier dates and assessed on later exact-date CMWSSB matches. The candidate remains visibly separate from the satellite storage calibration table.</p><span className="warning-tag">Large holdout errors for most reservoirs</span><p className="muted-note">The project’s combined S2-first/S1-gap-fill series is used only as a lag feature in the stored XGBoost forecast. It does not replace the active Sentinel-2 storage estimates.</p></div><div className="panel"><div className="panel-heading"><div><h2>Rainfall validation</h2><p>IMD nearest-grid-cell proxy · 2016–2024.</p></div><CloudSun size={18} /></div><p className="rainfall-score"><strong>221.5</strong><span>mcft baseline RMSE</span><ArrowDownRight size={16} /><strong>225.8</strong><span>mcft with rainfall</span></p><p className="body-copy">On 344 comparable 2019–2024 pairs, adding antecedent rainfall did not improve pooled RMSE. Rainfall stays outside the live forecast. The nearest grid cell is not a catchment-average measurement.</p></div></div></>;
}

function Methodology({ data }: { data: DashboardData }) {
  return <><div className="method-banner"><div className="method-banner-icon"><Waves size={22} /></div><div><span className="eyebrow">TRANSPARENT BY DESIGN</span><h2>From satellite scenes to an advisory outlook</h2><p>AquaSat keeps the observation source, calibration step, and forecast step distinct so users can see what each score means.</p></div></div><div className="method-grid"><MethodStep number="01" title="Observe water area" text="Sentinel-2 optical scenes use per-reservoir NDWI/MNDWI water-area rules. Sentinel-1 radar uses VV/VH backscatter thresholds and is a separate fallback experiment." icon={Satellite} /><MethodStep number="02" title="Calibrate to storage" text="Water area is converted to storage using reservoir-specific calibration against exact-date CMWSSB readings. Later-date holdout errors are shown separately from forecast errors." icon={Gauge} /><MethodStep number="03" title="Forecast 15 days" text="The live forecast is generated by XGBoost from official storage history and may use the latest prior S2-first/S1-gap-fill estimate as a lag feature. Rainfall is excluded." icon={Activity} /><MethodStep number="04" title="Keep provenance" text={`The current ${data.data_status.database} snapshot is synchronized from dashboard_data.csv, reservoir_forecasts.csv, and metrics.csv. Each import records source-file hashes and row counts.`} icon={Database} /></div><div className="panel limits-panel"><div className="panel-heading"><div><h2>Known limits</h2><p>Use these notes when interpreting charts or sharing results.</p></div><AlertTriangle size={18} /></div><ul><li>Satellite estimates are advisory and do not replace official CMWSSB data.</li><li>Some reservoirs have limited low-storage holdout coverage; drought-period accuracy is not established for all five.</li><li>Forecast bands use historical errors and are approximate, not calibrated probabilities.</li><li>Rainfall is a nearest-cell local proxy, not basin or catchment-average precipitation.</li><li>Database synchronization is currently from reviewed CSV outputs; automatic source collection is not enabled.</li></ul></div></>;
}

function MethodStep({ number: n, title, text, icon: Icon }: { number: string; title: string; text: string; icon: typeof Waves }) {
  return <article className="method-step"><span className="step-number">{n}</span><span className="step-icon"><Icon size={18} /></span><h3>{title}</h3><p>{text}</p></article>;
}

export default App;
