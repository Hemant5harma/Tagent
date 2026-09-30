import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Row = Record<string, any>;
type Tab = "Overview" | "Trades" | "Agent" | "System";
const tabs: Tab[] = ["Overview", "Trades", "Agent", "System"];
const marks = ["◫", "⇄", "✳", "⌘"];
const money = (v: unknown, currency = "USD") =>
  v === null || v === undefined
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(Number(v));
const time = (v?: string) =>
  v
    ? new Intl.DateTimeFormat("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(v))
    : "Not recorded";
const label = (s: string) => s.replaceAll("_", " ");
function Badge({
  children,
  warning = false,
}: {
  children: ReactNode;
  warning?: boolean;
}) {
  return (
    <span className={`badge ${warning ? "warning" : ""}`}>{children}</span>
  );
}
function Empty({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty-mark">◌</span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
function Stat({
  title,
  value,
  note,
  tone = "",
}: {
  title: string;
  value: string;
  note?: string;
  tone?: string;
}) {
  return (
    <article className="stat">
      <p className="eyebrow">{title}</p>
      <div className={`stat-value ${tone}`}>{value}</div>
      {note && <small>{note}</small>}
    </article>
  );
}
function Chart({
  points,
  field,
  title,
}: {
  points: Row[];
  field: string;
  title: string;
}) {
  if (points.length < 2)
    return (
      <Empty title="A clear picture takes time">
        Your chart starts with verified observations. We never manufacture a
        historical equity curve.
      </Empty>
    );
  const vals = points.map((p) => Number(p[field]));
  const low = Math.min(...vals),
    high = Math.max(...vals);
  const span = high - low || 1;
  const path = vals
    .map(
      (v, i) =>
        `${i ? "L" : "M"} ${20 + (i / (vals.length - 1)) * 760} ${170 - ((v - low) / span) * 140}`,
    )
    .join(" ");
  return (
    <div className="chart">
      <div className="chart-range">
        <span>{money(high)}</span>
        <span>{title}</span>
      </div>
      <svg
        viewBox="0 0 800 200"
        role="img"
        aria-label={`${title}. First ${vals[0].toFixed(2)}, latest ${vals.at(-1)?.toFixed(2)}. Minimum ${low.toFixed(2)}, maximum ${high.toFixed(2)}.`}
      >
        <path
          d="M20 30H780 M20 100H780 M20 170H780"
          stroke="#e7e3d9"
          fill="none"
        />
        <path
          d={path}
          stroke="#aa644a"
          strokeWidth="2.5"
          fill="none"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="chart-range">
        <span>{time(points[0].time)}</span>
        <span>{time(points.at(-1)?.time)}</span>
      </div>
    </div>
  );
}
function Login({ onLogin }: { onLogin: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/dashboard-api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.trim() }),
      });
      if (!response.ok) {
        const body = await response.json();
        throw new Error(body.detail || "Unable to sign in.");
      }
      onLogin();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection unavailable");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login">
      <div className="login-story">
        <div className="brand">
          <span>✳</span> Trade Agent
        </div>
        <p className="eyebrow">YOUR PRIVATE OBSERVATORY</p>
        <h1>
          A quieter view
          <br />
          of every decision.
        </h1>
        <p>
          See your trades, understand the numbers, and know what your agent is
          doing. One honest picture, wherever you are.
        </p>
        <div className="login-rule">
          Vantage MT5 demo · Human-confirmed trading
        </div>
      </div>
      <form onSubmit={submit} className="login-card">
        <Badge>PRIVATE ACCESS</Badge>
        <h2>Welcome back.</h2>
        <p>
          Send <code>/dashboard</code> to your Telegram bot, then paste your
          one-use code below.
        </p>
        <label htmlFor="code">Dashboard access code</label>
        <input
          id="code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoComplete="one-time-code"
          autoCapitalize="characters"
          maxLength={64}
          required
          placeholder="Your code from Telegram"
        />
        <button className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Open my dashboard →"}
        </button>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <small>
          Code expires after 5 minutes. This dashboard cannot place trades.
        </small>
      </form>
    </main>
  );
}
function App() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [data, setData] = useState<Row | null>(null);
  const [signedIn, setSignedIn] = useState(true);
  const [period, setPeriod] = useState("7d");
  const [scope, setScope] = useState("agent");
  const [error, setError] = useState("");
  const [clock, setClock] = useState(Date.now());
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (!signedIn) return;
    let alive = true,
      timer: ReturnType<typeof setTimeout>,
      controller: AbortController | undefined,
      delay = 5000;
    async function poll() {
      if (document.hidden) {
        timer = setTimeout(poll, 5000);
        return;
      }
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 10000);
      try {
        const response = await fetch(
          `/dashboard-api/snapshot?period=${period}&scope=${scope}`,
          { signal: controller.signal },
        );
        if (response.status === 401) {
          if (alive) {
            setData(null);
            setSignedIn(false);
          }
          return;
        }
        if (!response.ok)
          throw new Error("Dashboard is temporarily unavailable.");
        const next = await response.json();
        if (alive) {
          setData(next);
          setError("");
        }
        delay = 5000;
      } catch {
        if (alive)
          setError(
            "Connection interrupted. Displaying the last verified snapshot.",
          );
        delay = Math.min(delay * 2, 30000);
      } finally {
        clearTimeout(timeout);
        if (alive) timer = setTimeout(poll, delay);
      }
    }
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [signedIn, period, scope, refresh]);
  if (!signedIn)
    return (
      <Login
        onLogin={() => {
          setSignedIn(true);
          setRefresh((v) => v + 1);
        }}
      />
    );
  const broker = data?.broker,
    account = broker?.account,
    state = broker?.state,
    m = data?.metrics,
    currency = account?.currency || "USD";
  const stale =
    !broker?.observed_at ||
    clock - new Date(broker.observed_at).getTime() > 15000;
  const fmt = (v: unknown) => money(v, currency);
  async function logout() {
    await fetch("/dashboard-api/logout", { method: "POST" });
    setData(null);
    setSignedIn(false);
  }
  const nav = (
    <nav aria-label="Main navigation">
      {tabs.map((name, i) => (
        <button
          key={name}
          className={tab === name ? "selected" : ""}
          aria-current={tab === name ? "page" : undefined}
          onClick={() => setTab(name)}
        >
          <span aria-hidden="true">{marks[i]}</span>
          {name}
        </button>
      ))}
    </nav>
  );
  const pnlPoints = [...(data?.closed_trades || [])]
    .reverse()
    .reduce((points: Row[], trade: Row) => {
      points.push({
        time: trade.closed_at,
        pnl: Number(trade.net_pnl) + (points.at(-1)?.pnl || 0),
      });
      return points;
    }, []);
  return (
    <div className="shell">
      <aside>
        <a className="brand" href="/" aria-label="Trade Agent home">
          <span>✳</span> Trade Agent
        </a>
        <p className="sidebar-sub">THE OBSERVATORY</p>
        {nav}
        <div className="sidebar-foot">
          <Badge>READ-ONLY</Badge>
          <p>
            Decisions in Telegram.
            <br />
            Clarity here.
          </p>
          <button className="text-button" onClick={logout}>
            Sign out ↗
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header>
          <span>
            Personal workspace <span className="slash">/</span> {tab}
          </span>
          <Badge warning={stale}>
            {stale ? "○ Data delayed" : "● Connected"}
          </Badge>
          <button className="mobile-logout text-button" onClick={logout}>Sign out</button>
        </header>
        <main className="content">
          <div className="page-heading">
            <div>
              <p className="eyebrow">YOUR TRADING, IN PERSPECTIVE</p>
              <h1>
                {tab === "Overview"
                  ? "The bigger picture."
                  : tab === "Trades"
                    ? "Every trade, accounted for."
                    : tab === "Agent"
                      ? "Understand your agent."
                      : "Nothing behind the curtain."}
              </h1>
              <p className="subtitle">
                {tab === "Overview"
                  ? "Real numbers. Clear decisions. A little more peace of mind."
                  : "Verified activity from your trading system. All timestamps shown in IST."}
              </p>
            </div>
          </div>
          <div className="account-strip">
            <div>
              <span className="account-dot">V</span>
              <strong>
                {broker?.broker || "Broker pending"}{" "}
                <span className="muted">/ MT5</span>
              </strong>
              <Badge warning={!account?.is_demo}>
                {account
                  ? account.is_demo
                    ? "DEMO"
                    : "LIVE — CHECK CONFIG"
                  : "UNVERIFIED"}
              </Badge>
            </div>
            <span className="muted">
              Account{" "}
              {account ? `••${String(account.account_id).slice(-4)}` : "—"} ·{" "}
              {broker?.paused || !broker?.assisted_demo_accepted
                ? "Entries paused"
                : "Human-confirmed entries"}
            </span>
          </div>
          {(error || stale) && (
            <div className="notice" role="status">
              {error ||
                "Waiting for a fresh broker snapshot. Values below may be outdated."}{" "}
              <button
                className="text-button"
                onClick={() => setRefresh((v) => v + 1)}
              >
                Refresh
              </button>
            </div>
          )}
          {!data ? (
            <Empty title="Connecting to your observatory">
              Loading verified account data…
            </Empty>
          ) : (
            <>
              {tab === "Overview" && (
                <>
                  <div className="filters">
                    <div className="segmented" aria-label="Reporting period">
                      {[
                        ["today", "Today"],
                        ["7d", "7 days"],
                        ["30d", "30 days"],
                        ["all", "All recorded"],
                      ].map(([value, text]) => (
                        <button
                          key={value}
                          aria-pressed={period === value}
                          className={period === value ? "active" : ""}
                          onClick={() => setPeriod(value)}
                        >
                          {text}
                        </button>
                      ))}
                    </div>
                    <label className="scope-label">
                      Performance{" "}
                      <select
                        value={scope}
                        onChange={(e) => setScope(e.target.value)}
                      >
                        <option value="agent">Agent-managed</option>
                        <option value="account">Account-wide</option>
                      </select>
                    </label>
                  </div>
                  <div className="stats">
                    <Stat
                      title="ACCOUNT EQUITY"
                      value={fmt(account?.equity)}
                      note={`Balance ${fmt(account?.balance)}`}
                    />
                    <Stat
                      title="NET REALIZED P&L"
                      value={fmt(m?.net_realized_pnl)}
                      note="Closed lifecycles · after attributable costs"
                      tone={
                        Number(m?.net_realized_pnl) < 0
                          ? "negative"
                          : "positive"
                      }
                    />
                    <Stat
                      title="OPEN P&L · ACCOUNT"
                      value={fmt(
                        state?.positions.reduce(
                          (sum: number, p: Row) => sum + Number(p.profit),
                          0,
                        ),
                      )}
                      note={`Free margin ${fmt(account?.margin_free)}`}
                    />
                    <Stat
                      title="TRADE WIN RATE"
                      value={
                        m?.win_rate == null
                          ? "Not enough data"
                          : `${m.win_rate.toFixed(1)}%`
                      }
                      note={`${m?.closed_trades || 0} closed · ${m?.breakevens || 0} breakeven`}
                    />
                  </div>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <p className="eyebrow">THE LONG VIEW</p>
                        <h2>Account equity</h2>
                      </div>
                      <Badge>RECORDED, NOT SIMULATED</Badge>
                    </div>
                    <Chart
                      points={data.equity}
                      field="equity"
                      title="Observed account equity"
                    />
                    <p className="footnote">
                      Account-wide, sampled observations. Coverage starts{" "}
                      {time(data.coverage.equity_since)}. Reporting periods use
                      UTC.
                    </p>
                  </section>
                  <div className="two-columns">
                    <section className="panel">
                      <div className="panel-heading">
                        <h2>Performance at a glance</h2>
                        <span className="muted">
                          {scope === "agent" ? "Agent-managed" : "Account-wide"}
                        </span>
                      </div>
                      <dl className="metrics">
                        <div>
                          <dt>Total winning P&L</dt>
                          <dd className="positive">{fmt(m.winning_pnl)}</dd>
                        </div>
                        <div>
                          <dt>Total losing P&L</dt>
                          <dd className="negative">{fmt(m.losing_pnl)}</dd>
                        </div>
                        <div>
                          <dt>Profit factor</dt>
                          <dd>
                            {m.profit_factor == null
                              ? "— No losing trades"
                              : m.profit_factor.toFixed(2)}
                          </dd>
                        </div>
                        <div>
                          <dt>Sampled account drawdown</dt>
                          <dd>{fmt(data.drawdown)}</dd>
                        </div>
                      </dl>
                      <p className="footnote">
                        {data.drawdown_scope}. Unattributed broker adjustments:{" "}
                        {fmt(data.coverage.unattributed_adjustments)}.
                      </p>
                    </section>
                    <section className="panel insight">
                      <p className="eyebrow">A NOTE ON CONFIDENCE</p>
                      <h2>
                        Good execution isn’t
                        <br />
                        the same as a good prediction.
                      </h2>
                      <p>
                        You choose the trades in assisted mode. Win rate
                        measures those outcomes—not the AI’s ability to predict
                        markets.
                      </p>
                      <button
                        className="text-button"
                        onClick={() => setTab("Agent")}
                      >
                        See what we actually measure →
                      </button>
                    </section>
                  </div>
                  <section className="panel">
                    <h2>Closed-trade P&L</h2>
                    <Chart
                      points={pnlPoints}
                      field="pnl"
                      title="Cumulative selected closed-trade P&L"
                    />
                    <p className="footnote">
                      Closed lifecycle sample shown; deposits and withdrawals
                      excluded.
                    </p>
                  </section>
                </>
              )}
              {tab === "Trades" && (
                <>
                  {[
                    ["Open positions", state?.positions || [], "position_id"],
                    ["Pending orders", state?.orders || [], "order_id"],
                    ["Closed trades", data.closed_trades, "position_id"],
                    ["Trade proposals", data.proposals, "id"],
                  ].map(([title, list, key]: any) => (
                    <section className="panel" key={title}>
                      <div className="panel-heading">
                        <h2>{title}</h2>
                        <Badge>{list.length} recorded</Badge>
                      </div>
                      {!list.length ? (
                        <Empty title={`No ${title.toLowerCase()}`}>
                          Nothing to show yet. Trade instructions and
                          confirmations stay in Telegram.
                        </Empty>
                      ) : (
                        <div className="trade-list">
                          {list.map((raw: Row) => {
                            const trade = raw.instruction || raw;
                            return (
                              <article className="trade" key={raw[key]}>
                                <div className="trade-title">
                                  <strong>{trade.symbol}</strong>
                                  <Badge>
                                    {label(raw.status || "open position")}
                                  </Badge>
                                </div>
                                <p>
                                  {String(trade.side || "").toUpperCase()} ·{" "}
                                  {trade.volume} lots ·{" "}
                                  {raw.source || "broker account"}
                                </p>
                                <dl>
                                  <div>
                                    <dt>Entry</dt>
                                    <dd>{trade.entry_price ?? "Market"}</dd>
                                  </div>
                                  <div>
                                    <dt>SL / TP</dt>
                                    <dd>
                                      {trade.stop_loss ?? "—"} /{" "}
                                      {trade.take_profit ?? "—"}
                                    </dd>
                                  </div>
                                  <div>
                                    <dt>P&L</dt>
                                    <dd>{fmt(raw.net_pnl ?? raw.profit)}</dd>
                                  </div>
                                  <div>
                                    <dt>Costs / swaps</dt>
                                    <dd>{fmt(raw.costs)}</dd>
                                  </div>
                                </dl>
                                <small>
                                  #{raw[key]} ·{" "}
                                  {time(
                                    raw.closed_at ||
                                      raw.opened_at ||
                                      raw.created_at,
                                  )}
                                </small>
                              </article>
                            );
                          })}
                        </div>
                      )}
                    </section>
                  ))}
                  <section className="panel">
                    <h2>Execution timeline</h2>
                    <div className="timeline">
                      {data.events.length ? (
                        data.events.map((e: Row) => (
                          <div key={e.id}>
                            <span className="timeline-dot" />
                            <strong>{label(e.kind)}</strong>
                            <small>
                              {time(e.time)}
                              {e.proposal_id
                                ? ` · ${e.proposal_id.slice(0, 8)}`
                                : ""}
                            </small>
                          </div>
                        ))
                      ) : (
                        <p className="muted">
                          Events will appear here as instructions move through
                          the system.
                        </p>
                      )}
                    </div>
                  </section>
                </>
              )}
              {tab === "Agent" && (
                <>
                  <div className="stats">
                    <Stat
                      title="INSTRUCTION ACCURACY"
                      value="Not evaluated"
                      note="Requires independently reviewed examples"
                    />
                    <Stat title="CLARIFICATION RESPONSES" value={String(data.agent_counts?.clarifying || 0)} note="Recorded since this release" />
                    <Stat title="REJECTED PROPOSALS" value={String(data.agent_counts?.rejected_proposal || 0)} note="Blocked during proposal creation" />
                    <Stat
                      title="PREDICTION ACCURACY"
                      value="Not applicable"
                      note="Not available in assisted mode"
                    />
                    <Stat
                      title="UNRESOLVED SUBMISSIONS"
                      value={String(
                        data.execution.unknown + data.execution.prepared,
                      )}
                      note="Never treated as failed and blindly retried"
                    />
                    <Stat
                      title="LAST MODEL RESPONSE"
                      value={
                        data.services.model?.latency_ms != null
                          ? `${(data.services.model.latency_ms / 1000).toFixed(1)}s`
                          : "Not recorded"
                      }
                      note={
                        data.services.model?.status ||
                        "No model request observed"
                      }
                    />
                  </div>
                  <section className="panel">
                    <h2>Execution outcomes</h2>
                    <div className="outcomes">
                      {Object.entries(data.execution).map(([key, value]) => (
                        <div key={key}>
                          <strong>{String(value)}</strong>
                          <span>{label(key)}</span>
                        </div>
                      ))}
                    </div>
                    <p className="footnote">
                      Ledger outcomes, not a measure of AI prediction skill.
                      User-directed trades only; autonomous entries are
                      disabled.
                    </p>
                  </section>
                  <section className="panel">
                    <div className="panel-heading">
                      <h2>Agent journal</h2>
                      <Badge>SANITIZED ACTIVITY</Badge>
                    </div>
                    {data.activity.length ? (
                      data.activity.map((a: Row) => (
                        <article className="journal" key={a.id}>
                          <div>
                            <Badge>{label(a.kind)}</Badge>
                            <small>{time(a.time)}</small>
                          </div>
                          <p>{a.summary}</p>
                        </article>
                      ))
                    ) : (
                      <Empty title="The journal is ready">
                        Your agent’s explanations, clarification requests, and
                        processing outcomes will appear here. No hidden
                        reasoning is collected.
                      </Empty>
                    )}
                  </section>
                </>
              )}
              {tab === "System" && (
                <>
                  <section className="panel">
                    <h2>Service health</h2>
                    <div className="health-grid">
                      {[
                        [
                          "Broker / MT5",
                          stale ? "delayed" : "ok",
                          broker?.observed_at,
                        ],
                        ["Dashboard / database", "ok", data.generated_at],
                        ...["telegram", "protection", "model"].map((name) => [
                          label(name),
                          data.services[name]?.status || "not observed",
                          data.services[name]?.observed_at,
                        ]),
                      ].map(([name, status, stamp]) => {
                        const aged =
                          stamp &&
                          name !== "model" &&
                          clock - new Date(stamp).getTime() >
                            (name === "telegram" ? 65000 : 15000);
                        return (
                          <article className="health" key={name}>
                            <strong>{name}</strong>
                            <Badge warning={status !== "ok" || Boolean(aged)}>
                              {aged ? "delayed" : status}
                            </Badge>
                            <small>{time(stamp)}</small>
                          </article>
                        );
                      })}
                    </div>
                  </section>
                  <section className="panel">
                    <h2>Safety & data coverage</h2>
                    <dl className="metrics">
                      <div>
                        <dt>Trading mode</dt>
                        <dd>Human-confirmed demo</dd>
                      </div>
                      <div>
                        <dt>Entries</dt>
                        <dd>
                          {broker?.paused || !broker?.assisted_demo_accepted
                            ? "Paused — operator review required"
                            : "Enabled with confirmation"}
                        </dd>
                      </div>
                      <div>
                        <dt>Risk circuit breaker</dt>
                        <dd>{data.services.limits?.reason || data.services.limits?.status || "Not observed"}</dd>
                      </div>
                      <div>
                        <dt>Unprotected open positions</dt>
                        <dd>
                          {state?.positions.filter(
                            (p: Row) =>
                              !p.stop_loss || Number(p.stop_loss) === 0,
                          ).length ?? "Unknown"}
                        </dd>
                      </div>
                      <div>
                        <dt>History synchronized through</dt>
                        <dd>{time(data.coverage.until)}</dd>
                      </div>
                      <div>
                        <dt>Imported history starts</dt>
                        <dd>{time(data.coverage.since)}</dd>
                      </div>
                    </dl>
                    <p className="footnote">
                      {data.coverage.note ||
                        "History has not yet synchronized."}{" "}
                      Temporary tunnel availability does not control protective
                      orders.
                    </p>
                  </section>
                </>
              )}
            </>
          )}
          <footer>
            Read-only observatory · No real-money authorization · Last broker
            update {time(broker?.observed_at)}
          </footer>
        </main>
      </div>
      <div className="mobile-nav">{nav}</div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
