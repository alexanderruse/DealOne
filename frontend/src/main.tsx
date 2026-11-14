import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowDownRight,
  ArrowUpRight,
  AudioLines,
  Bell,
  Check,
  CheckCheck,
  ChevronRight,
  CircleHelp,
  Command,
  CreditCard,
  Headphones,
  Keyboard,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  Plus,
  Radar,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TrendingDown,
  Usb,
  Wallet,
  X,
} from 'lucide-react';
import { Button } from './components/ui/button';
import { api, money } from './lib';
import './style.css';

type Product = {
  id: string;
  name: string;
  variant: string;
  seller: string;
  price_cents: number;
  original_cents: number;
  total_cents: number;
  category: string;
  color: string;
  description?: string;
  contains_hidden_instructions?: boolean;
};
type PermissionCard = {
  last4: string;
  limit_cents: number;
  status: string;
  available_cents: number;
};
type Permission = {
  id: string;
  product_id: string;
  max_total_cents: number;
  expires_at: string;
  status: string;
  variant: string;
  seller: string;
  purchase_mode: 'ask_first' | 'auto_buy';
  card?: PermissionCard | null;
};
type Order = {
  id: string;
  permission_id: string;
  status: string;
  total_cents: number;
  confirmation: string | null;
  error: string | null;
  created_at: string;
  payment_reference?: string | null;
  cancellable_until?: string | null;
};
type CardSummary = {
  provider: string;
  card_count?: number;
  issued_count?: number;
  committed_limit_cents?: number;
};
type Notice = { id: string; message: string; created_at: string };
type Observation = { price_cents: number; observed_at: string };
type Rules = { product_id: string; max_total_cents: number; expires_at: string; quantity: 1 };
const icons = { headphones: Headphones, keyboard: Keyboard, speaker: AudioLines, 'usb-hub': Usb };

function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [watches, setWatches] = useState<Permission[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [histories, setHistories] = useState<Record<string, Observation[]>>({});
  const [page, setPage] = useState('Overview');
  const [error, setError] = useState('');
  const [grok, setGrok] = useState(false);
  const [payments, setPayments] = useState('simulation');
  const [cardSummary, setCardSummary] = useState<CardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Product | null>(null);
  const [budget, setBudget] = useState('180');
  const [deadline, setDeadline] = useState('7');
  const [parsedExpiration, setParsedExpiration] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [draft, setDraft] = useState<Permission | null>(null);
  const [purchaseMode, setPurchaseMode] = useState<'ask_first' | 'auto_buy'>('ask_first');
  const [busy, setBusy] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminToken, setAdminToken] = useState('');
  const [demoPrice, setDemoPrice] = useState('159');
  const [demoProduct, setDemoProduct] = useState('headphones');
  const [chat, setChat] = useState('');
  const [answer, setAnswer] = useState('');
  const [toolCalls, setToolCalls] = useState<string[]>([]);

  async function refresh() {
    const [p, w, o, n] = await Promise.all([
      api<Product[]>('/products'),
      api<Permission[]>('/permissions'),
      api<Order[]>('/orders'),
      api<Notice[]>('/notifications'),
    ]);
    setProducts(p);
    setWatches(w);
    setOrders(o);
    setNotices(n);
    // Card summary is extra; a bank hiccup must never blank the rest of the page.
    api<CardSummary>('/payments/card')
      .then(setCardSummary)
      .catch(() => setCardSummary(null));
    const history = await Promise.all(
      p.map(
        async (product) =>
          [product.id, await api<Observation[]>(`/products/${product.id}/prices`)] as const,
      ),
    );
    setHistories(Object.fromEntries(history));
  }
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setInterval>;
    api<{ grok_configured: boolean; payments: string }>('/session')
      .then(async (s) => {
        if (!active) return;
        setGrok(s.grok_configured);
        setPayments(s.payments);
        await refresh();
        timer = setInterval(() => {
          refresh().catch(() => {});
        }, 4000);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function openWatch(p: Product) {
    setSelected(p);
    setBudget(String(Math.round((p.price_cents * 0.9) / 100)));
    setDraft(null);
    setPrompt('');
    setParsedExpiration(null);
    setPurchaseMode('ask_first');
  }
  async function saveDraft() {
    if (!selected) return;
    const rules: Rules = {
      product_id: selected.id,
      max_total_cents: Math.round(Number(budget) * 100),
      expires_at:
        parsedExpiration || new Date(Date.now() + Number(deadline) * 86400000).toISOString(),
      quantity: 1,
    };
    setDraft(
      await api<Permission>('/permissions', { method: 'POST', body: JSON.stringify(rules) }),
    );
  }
  const activeWatches = watches.filter((w) => w.status === 'active').length;
  const completed = orders.filter((o) => o.status === 'completed');
  const committed = watches
    .filter((w) => w.status === 'active')
    .reduce((sum, w) => sum + w.max_total_cents, 0);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/">
          <span className="brand-icon">
            <TrendingDown size={23} />
          </span>
          DealOne<span className="brand-dot">.</span>
        </a>
        <div className="workspace">
          <span className="workspace-avatar">D</span>
          <div>
            Personal workspace<small>Your next good deal</small>
          </div>
          <ChevronRight size={15} />
        </div>
        <span className="nav-label">YOUR SPACE</span>
        <nav>
          {[
            ['Overview', LayoutDashboard],
            ['Watchlist', Radar],
            ['Activity', Bell],
            ['Grok assistant', Sparkles],
          ].map(([label, Icon]) => {
            const ItemIcon = Icon as typeof Radar;
            return (
              <button
                key={String(label)}
                className={page === label ? 'nav-item active' : 'nav-item'}
                onClick={() => setPage(String(label))}
              >
                <ItemIcon size={18} />
                {String(label)}
                {label === 'Watchlist' && <span className="nav-count">{activeWatches}</span>}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="autopilot">
            <ShieldCheck size={22} />
            <strong>Your rules. Always.</strong>
            <p>Each watch gets its own virtual card and limit. Ask-first is the default.</p>
            <span>
              <i /> {payments === 'nessie' ? 'Nessie settlement' : 'Simulated payments'}
            </span>
            {cardSummary?.issued_count != null && cardSummary.issued_count > 0 && (
              <p>
                {cardSummary.issued_count} active card
                {cardSummary.issued_count === 1 ? '' : 's'} ·{' '}
                {money(cardSummary.committed_limit_cents || 0)} committed
              </p>
            )}
          </div>
          <button className="nav-item" onClick={() => setAdminOpen(true)}>
            <SlidersHorizontal size={18} />
            Demo controls
          </button>
          <div className="profile">
            <span className="profile-avatar">Y</span>
            <div>
              Your workspace<small>Hackathon preview</small>
            </div>
            <CircleHelp size={17} />
          </div>
        </div>
      </aside>

      <div className="main-shell">
        <header>
          <div className="breadcrumb">
            Your space <ChevronRight size={13} /> <strong>{page}</strong>
          </div>
          <div className="header-right">
            <span className="preview-badge">DEMO PREVIEW</span>
            <button
              aria-label="View notifications"
              onClick={() => setPage('Activity')}
              className="icon-button"
            >
              <Bell size={19} />
              {notices.length > 0 && <i />}
            </button>
            <span className="profile-avatar small">Y</span>
          </div>
        </header>
        <main>
          <div className="page-title">
            <div>
              <div className="eyebrow">A LITTLE PATIENCE. A BETTER PRICE.</div>
              <h1>
                {page === 'Overview'
                  ? 'Good things come to those who watch.'
                  : page === 'Watchlist'
                    ? 'Your wishlist. Working for you.'
                    : page === 'Activity'
                      ? 'Every move, in the open.'
                      : 'Meet your shopping copilot.'}
              </h1>
              <p>
                {page === 'Overview'
                  ? 'Set your price. We’ll watch the market and catch your moment.'
                  : 'Thoughtful shopping, with you in control.'}
              </p>
            </div>
            <Button onClick={() => products[0] && openWatch(products[0])}>
              <Plus size={17} />
              New watch
            </Button>
          </div>
          {error && (
            <div role="alert" className="error">
              <span>{error}</span>
              <button aria-label="Dismiss error" onClick={() => setError('')}>
                <X size={16} />
              </button>
            </div>
          )}
          {loading ? (
            <div className="loading">
              <LoaderCircle className="spin" />
              Connecting to your workspace…
            </div>
          ) : (
            <>
              {page === 'Overview' && (
                <>
                  <section className="hero">
                    <div className="hero-copy">
                      <span className="hero-label">
                        <span /> YOUR NEXT DEAL IS OUT THERE
                      </span>
                      <h2>
                        You have the wishlist.
                        <br />
                        We have the watch.
                      </h2>
                      <p>
                        Your agent follows the price, checks your rules, and
                        <br className="desktop" /> makes the move when everything lines up.
                      </p>
                      <button onClick={() => setPage('Grok assistant')}>
                        Meet your Grok agent <ArrowUpRight size={17} />
                      </button>
                    </div>
                    <div className="hero-art" aria-hidden="true">
                      <div className="orbit orbit-one" />
                      <div className="orbit orbit-two" />
                      <div className="float-tag">
                        <TrendingDown size={15} /> The right price. Finally.
                      </div>
                      <div className="floating-card">
                        <div className="card-top">
                          <ShieldCheck size={23} />
                          <span>DealOne virtual</span>
                        </div>
                        <div className="card-chip" />
                        <div className="card-number">•••• &nbsp; •••• &nbsp; •••• &nbsp; DEMO</div>
                        <div className="card-bottom">
                          <span>ONE PURCHASE. YOUR LIMIT.</span>
                          <Command size={22} />
                        </div>
                      </div>
                      <div className="secure-chip">
                        <Check size={13} /> Governed by your rules
                      </div>
                    </div>
                  </section>
                  <section className="stats">
                    <div>
                      <span className="stat-icon">
                        <Radar size={20} />
                      </span>
                      <span>
                        Active watches
                        <strong>
                          {activeWatches}
                          <small> items on our radar</small>
                        </strong>
                      </span>
                    </div>
                    <div>
                      <span className="stat-icon">
                        <Wallet size={20} />
                      </span>
                      <span>
                        Authorized budget
                        <strong>
                          {money(committed)}
                          <small> total maximum</small>
                        </strong>
                      </span>
                    </div>
                    <div>
                      <span className="stat-icon">
                        <CheckCheck size={20} />
                      </span>
                      <span>
                        Completed purchases
                        <strong>
                          {completed.length}
                          <small> simulated orders</small>
                        </strong>
                      </span>
                    </div>
                  </section>
                </>
              )}

              {(page === 'Overview' || page === 'Watchlist') && (
                <>
                  <div className="section-heading">
                    <div>
                      <h2>
                        {page === 'Watchlist' ? 'Your watches' : 'On the radar'}{' '}
                        <span>{page === 'Watchlist' ? watches.length : products.length}</span>
                      </h2>
                      <p>
                        {page === 'Watchlist'
                          ? 'Your permissions, budgets, and progress.'
                          : 'Find something you love. Let the price come to you.'}
                      </p>
                    </div>
                    <span className="live-label">
                      <i /> Checked every 10 seconds
                    </span>
                  </div>
                  {page === 'Watchlist' && (
                    <div className="watch-list">
                      {watches.length === 0 ? (
                        <div className="empty">
                          <Radar />
                          <h3>A little wishful thinking goes a long way.</h3>
                          <p>Create your first watch to get started.</p>
                        </div>
                      ) : (
                        watches.map((w) => {
                          const product = products.find((p) => p.id === w.product_id);
                          const canBuy =
                            w.status === 'active' &&
                            product != null &&
                            product.total_cents <= w.max_total_cents;
                          return (
                            <div className="watch-row" key={w.id}>
                              <div>
                                <strong>{product?.name}</strong>
                                <small>{w.variant}</small>
                                <small className="watch-meta">
                                  {w.purchase_mode === 'auto_buy' ? 'Auto-buy' : 'Ask me first'}
                                  {w.card
                                    ? ` · Card •••• ${w.card.last4} · limit ${money(w.card.limit_cents)}`
                                    : ''}
                                </small>
                              </div>
                              <div>
                                <small>All-in maximum</small>
                                <strong>{money(w.max_total_cents)}</strong>
                              </div>
                              <span className={`status ${w.status}`}>{w.status}</span>
                              <div className="row-actions">
                                {w.status === 'draft' && (
                                  <Button
                                    disabled={busy}
                                    onClick={() =>
                                      run(async () => {
                                        await api(`/permissions/${w.id}/activate`, {
                                          method: 'POST',
                                          body: JSON.stringify({ purchase_mode: 'ask_first' }),
                                        });
                                      })
                                    }
                                  >
                                    Activate
                                  </Button>
                                )}
                                {canBuy && (
                                  <Button
                                    disabled={busy}
                                    onClick={() =>
                                      run(async () => {
                                        await api('/purchases', {
                                          method: 'POST',
                                          body: JSON.stringify({ permission_id: w.id }),
                                        });
                                      })
                                    }
                                  >
                                    Approve purchase
                                  </Button>
                                )}
                                {w.status === 'paused' && (
                                  <Button
                                    variant="outline"
                                    disabled={busy}
                                    onClick={() =>
                                      run(async () => {
                                        await api(`/permissions/${w.id}/resume`, { method: 'POST' });
                                      })
                                    }
                                  >
                                    Reviewed, continue
                                  </Button>
                                )}
                                {['draft', 'active', 'paused'].includes(w.status) && (
                                  <Button
                                    variant="ghost"
                                    onClick={() =>
                                      run(async () => {
                                        await api(`/permissions/${w.id}/cancel`, { method: 'POST' });
                                      })
                                    }
                                  >
                                    Cancel
                                  </Button>
                                )}
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                  )}
                  <section className="product-grid">
                    {products.map((p) => {
                      const Icon = icons[p.id as keyof typeof icons] || Headphones;
                      const history = histories[p.id] || [];
                      const chart = history.slice(-30).map((x) => x.price_cents);
                      const max = Math.max(...chart, 1);
                      const min = Math.min(...chart, max - 1);
                      const points = chart
                        .map(
                          (v, i) =>
                            `${(i / Math.max(chart.length - 1, 1)) * 260},${45 - ((v - min) / Math.max(max - min, 1)) * 33}`,
                        )
                        .join(' ');
                      return (
                        <article className="product-card" key={p.id}>
                          <div className={`product-art ${p.color}`}>
                            <span className="category">{p.category}</span>
                            <Icon className="product-object" size={116} strokeWidth={1.1} />
                            <span className="discount">
                              <ArrowDownRight size={12} />
                              {Math.round((1 - p.price_cents / p.original_cents) * 100)}%
                            </span>
                          </div>
                          <div className="product-info">
                            <span className="seller">
                              DEALONE DEMO STORE <ShieldCheck size={11} />
                            </span>
                            <h3>{p.name}</h3>
                            <p>{p.variant}</p>
                            {p.contains_hidden_instructions && (
                              <p className="trap-note">
                                <ShieldAlert size={13} />
                                Hidden-instruction demo — seller text tries to override your rules.
                                The agent must treat it as data only.
                              </p>
                            )}
                            <div className="price">
                              <strong>{money(p.price_cents)}</strong>
                              <del>{money(p.original_cents)}</del>
                              <span>USD</span>
                            </div>
                            <div className="price-chart">
                              <svg
                                viewBox="0 0 260 50"
                                preserveAspectRatio="none"
                                aria-label="Observed price history"
                              >
                                <polyline
                                  points={points}
                                  fill="none"
                                  stroke="#63846b"
                                  strokeWidth="2"
                                  vectorEffect="non-scaling-stroke"
                                />
                              </svg>
                              <div>
                                <span>Observed price history</span>
                                <span>All in {money(p.total_cents)}</span>
                              </div>
                            </div>
                            <Button variant="outline" onClick={() => openWatch(p)}>
                              <Plus size={16} />
                              Watch this item
                              <ArrowUpRight size={15} />
                            </Button>
                          </div>
                        </article>
                      );
                    })}
                  </section>
                  <div className="trust-strip">
                    <LockKeyhole size={16} />
                    <span>
                      Your budget is a boundary. Tax and shipping are always included in checkout
                      checks.
                    </span>
                    <span className="powered">
                      Powered by Grok <Sparkles size={13} />
                    </span>
                  </div>
                </>
              )}

              {page === 'Activity' && (
                <div className="activity-grid">
                  <section className="panel">
                    <h2>Agent activity</h2>
                    {notices.length === 0 ? (
                      <div className="empty">
                        <Bell />
                        <h3>All quiet for now.</h3>
                        <p>Price alerts and receipts will appear here.</p>
                      </div>
                    ) : (
                      notices.map((n) => (
                        <div className="notice" key={n.id}>
                          <span className="stat-icon">
                            <Bell size={17} />
                          </span>
                          <div>
                            <p>{n.message}</p>
                            <small>{new Date(n.created_at).toLocaleString()}</small>
                          </div>
                        </div>
                      ))
                    )}
                  </section>
                  <section className="panel">
                    <h2>Purchase receipts</h2>
                    {orders.length === 0 && (
                      <p className="muted">
                        No purchases yet. Every completed order will leave a trail here.
                      </p>
                    )}
                    {orders.map((o) => (
                      <div className="receipt" key={o.id}>
                        <span className={`status ${o.status}`}>{o.status}</span>
                        <strong>{money(o.total_cents)}</strong>
                        <p>{o.confirmation || 'Awaiting confirmation'}</p>
                        <small>
                          {o.error ||
                            (o.payment_reference && !o.payment_reference.startsWith('sim_')
                              ? `Nessie sandbox card · ${o.payment_reference}`
                              : 'Simulated virtual card · one purchase')}
                        </small>
                        {o.cancellable_until && new Date(o.cancellable_until) > new Date() && (
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              run(async () => {
                                await api(`/orders/${o.id}/cancel`, { method: 'POST' });
                              })
                            }
                          >
                            Cancel purchase (
                            {Math.max(
                              0,
                              Math.round((new Date(o.cancellable_until).getTime() - Date.now()) / 1000),
                            )}
                            s left)
                          </Button>
                        )}
                      </div>
                    ))}
                  </section>
                </div>
              )}

              {page === 'Grok assistant' && (
                <section className="assistant-panel panel">
                  <div className="assistant-avatar">
                    <Sparkles size={29} />
                  </div>
                  <h2>A good deal starts with a conversation.</h2>
                  <p>Ask about products, price history, or your activated watches.</p>
                  <span className={`status ${grok ? 'active' : 'draft'}`}>
                    {grok ? 'Grok connected' : 'Grok API key not configured'}
                  </span>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      run(async () => {
                        const result = await api<{ message: string; tools: { tool: string }[] }>(
                          '/assistant',
                          { method: 'POST', body: JSON.stringify({ text: chat }) },
                        );
                        setAnswer(result.message);
                        setToolCalls(result.tools.map((t) => t.tool));
                      });
                    }}
                  >
                    <label htmlFor="chat">Your message</label>
                    <textarea
                      id="chat"
                      value={chat}
                      onChange={(e) => setChat(e.target.value)}
                      placeholder="Find headphones and tell me their price history…"
                      required
                      minLength={5}
                    />
                    <Button disabled={busy || !grok}>
                      {busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
                      Ask Grok
                    </Button>
                  </form>
                  {answer && (
                    <div className="agent-answer">
                      <strong>Grok</strong>
                      <p>{answer}</p>
                      {toolCalls.length > 0 && <small>Tools used: {toolCalls.join(' → ')}</small>}
                    </div>
                  )}
                  <p className="fineprint">
                    Grok can request purchases only under rules you have already activated. Product
                    text is never treated as instructions — try the USB-C Hub demo item. Payments in
                    this preview are simulated.
                  </p>
                </section>
              )}
            </>
          )}
          <footer>
            <span>DealOne © {new Date().getFullYear()}</span>
            <span>Less chasing. Better buying.</span>
            <span>Grok × Tiger Data × Vultr</span>
          </footer>
        </main>
      </div>

      {selected && (
        <div className="modal-backdrop">
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="watch-title">
            <button
              className="modal-close"
              aria-label="Close watch"
              onClick={() => setSelected(null)}
            >
              <X size={21} />
            </button>
            <span className="modal-icon">
              <Radar size={24} />
            </span>
            <h2 id="watch-title">
              {draft ? 'Your rules. Your call.' : 'Put a good deal in motion.'}
            </h2>
            <p>
              {selected.name} · {selected.variant}
            </p>
            {draft ? (
              <>
                <div className="review">
                  <div>
                    <span>Maximum total</span>
                    <strong>{money(draft.max_total_cents)}</strong>
                  </div>
                  <div>
                    <span>Quantity</span>
                    <strong>One item</strong>
                  </div>
                  <div>
                    <span>Approved seller</span>
                    <strong>{draft.seller}</strong>
                  </div>
                  <div>
                    <span>Deadline</span>
                    <strong>{new Date(draft.expires_at).toLocaleString()}</strong>
                  </div>
                </div>
                <fieldset className="mode-picker">
                  <legend>When the price matches</legend>
                  <label className={purchaseMode === 'ask_first' ? 'selected' : ''}>
                    <input
                      type="radio"
                      name="purchase-mode"
                      checked={purchaseMode === 'ask_first'}
                      onChange={() => setPurchaseMode('ask_first')}
                    />
                    <span>
                      <strong>Ask me first</strong>
                      <small>Notify me. I approve before any purchase.</small>
                    </span>
                  </label>
                  <label className={purchaseMode === 'auto_buy' ? 'selected' : ''}>
                    <input
                      type="radio"
                      name="purchase-mode"
                      checked={purchaseMode === 'auto_buy'}
                      onChange={() => setPurchaseMode('auto_buy')}
                    />
                    <span>
                      <strong>Buy automatically</strong>
                      <small>One purchase when every rule matches.</small>
                    </span>
                  </label>
                </fieldset>
                <p className="fineprint">
                  Activating issues a single-use virtual card limited to {money(draft.max_total_cents)}.
                  Tax and shipping must fit that maximum.
                </p>
                <Button
                  className="full-width"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api(`/permissions/${draft.id}/activate`, {
                        method: 'POST',
                        body: JSON.stringify({ purchase_mode: purchaseMode }),
                      });
                      setSelected(null);
                      setPage('Watchlist');
                    })
                  }
                >
                  {purchaseMode === 'auto_buy' ? (
                    <ShieldCheck size={17} />
                  ) : (
                    <CreditCard size={17} />
                  )}
                  {purchaseMode === 'auto_buy'
                    ? 'Activate automatic purchase'
                    : 'Activate watch · ask me first'}
                </Button>
              </>
            ) : (
              <>
                <div className="grok-extract">
                  <label htmlFor="rules-prompt">
                    <Sparkles size={14} /> Describe it to Grok{' '}
                    <small>{grok ? 'Connected' : 'API key needed'}</small>
                  </label>
                  <textarea
                    id="rules-prompt"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder="Buy the black headphones if the total drops below $180, within a week."
                  />
                  <Button
                    variant="outline"
                    disabled={busy || !grok || prompt.length < 5}
                    onClick={() =>
                      run(async () => {
                        const r = await api<Rules>('/rules/parse', {
                          method: 'POST',
                          body: JSON.stringify({ text: prompt }),
                        });
                        const p = products.find((p) => p.id === r.product_id);
                        if (p) setSelected(p);
                        setBudget(String(r.max_total_cents / 100));
                        setParsedExpiration(r.expires_at);
                        setDeadline(
                          String(
                            Math.max(
                              1,
                              Math.ceil((new Date(r.expires_at).getTime() - Date.now()) / 86400000),
                            ),
                          ),
                        );
                      })
                    }
                  >
                    <Sparkles size={15} />
                    Fill my rules
                  </Button>
                </div>
                <div className="divider">or set your rules</div>
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    run(saveDraft);
                  }}
                >
                  <label htmlFor="budget">Maximum total, including tax & shipping (USD)</label>
                  <div className="currency-input">
                    <span>$</span>
                    <input
                      id="budget"
                      type="number"
                      min="0.01"
                      max="10000"
                      step="0.01"
                      value={budget}
                      onChange={(e) => setBudget(e.target.value)}
                      required
                    />
                  </div>
                  <label htmlFor="deadline">Watch for</label>
                  <select
                    id="deadline"
                    value={deadline}
                    onChange={(e) => {
                      setDeadline(e.target.value);
                      setParsedExpiration(null);
                    }}
                  >
                    {Array.from({ length: 30 }, (_, i) => i + 1).map((d) => (
                      <option key={d} value={d}>
                        {d} {d === 1 ? 'day' : 'days'}
                      </option>
                    ))}
                  </select>
                  {parsedExpiration && (
                    <p className="fineprint">
                      Grok deadline: {new Date(parsedExpiration).toLocaleString()}
                    </p>
                  )}
                  <div className="rule-note">
                    <ShieldCheck size={17} />
                    Exact variant. Approved seller. One purchase.
                  </div>
                  <Button className="full-width" disabled={busy}>
                    Review purchase rules
                    <ChevronRight size={16} />
                  </Button>
                </form>
              </>
            )}
            {error && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
          </section>
        </div>
      )}

      {adminOpen && (
        <div className="modal-backdrop">
          <section className="modal" role="dialog" aria-modal="true" aria-labelledby="demo-title">
            <button
              className="modal-close"
              aria-label="Close demo controls"
              onClick={() => setAdminOpen(false)}
            >
              <X size={21} />
            </button>
            <h2 id="demo-title">Make the moment happen.</h2>
            <p>Change a demo price to exercise the real monitoring and checkout flow.</p>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                run(async () => {
                  await api(`/demo/products/${demoProduct}/price`, {
                    method: 'POST',
                    headers: { 'x-demo-admin-token': adminToken },
                    body: JSON.stringify({ price_cents: Math.round(Number(demoPrice) * 100) }),
                  });
                  setAdminOpen(false);
                });
              }}
            >
              <label htmlFor="admin-token">Demo administrator token</label>
              <input
                id="admin-token"
                type="password"
                value={adminToken}
                onChange={(e) => setAdminToken(e.target.value)}
                required
                autoComplete="off"
              />
              <label htmlFor="demo-product">Product</label>
              <select
                id="demo-product"
                value={demoProduct}
                onChange={(e) => setDemoProduct(e.target.value)}
              >
                {products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <label htmlFor="demo-price">New item price (USD)</label>
              <input
                id="demo-price"
                type="number"
                min="0.01"
                step="0.01"
                value={demoPrice}
                onChange={(e) => setDemoPrice(e.target.value)}
                required
              />
              <Button className="full-width" disabled={busy}>
                <TrendingDown size={17} />
                Update demo price
              </Button>
            </form>
            {error && (
              <p role="alert" className="form-error">
                {error}
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
