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
  Mouse,
  Package,
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
  source_kind: 'demo' | 'merchant';
  can_request: boolean;
  stock_status: 'in_stock' | 'sold_out' | 'unknown';
  source_url: string | null;
  image_url: string | null;
  icon: string;
  price_checked_at: string | null;
  contains_hidden_instructions?: boolean;
};
type PermissionCard = {
  last4: string;
  limit_cents: number;
  nessie_account_id?: string | null;
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
  checkout_mode: 'merchant_handoff' | 'sandbox' | 'legacy';
  sandbox_total_cents?: number | null;
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
  bank_status?: string | null;
  cancellable_until?: string | null;
};
type CardSummary = {
  provider: string;
  card_count?: number;
  issued_count?: number;
  committed_limit_cents?: number;
};
type LedgerEntry = {
  source: string;
  transaction_id?: string;
  status?: string;
  amount_cents?: number;
  description?: string;
  account_id?: string;
  created_at?: string;
};
type Ledger = { charges: LedgerEntry[]; nessie: LedgerEntry[] };
type Notice = { id: string; message: string; created_at: string };
type Observation = { price_cents: number; observed_at: string };
type Rules = { product_id: string; max_total_cents: number; expires_at: string; quantity: 1 };
const icons = { headphones: Headphones, keyboard: Keyboard, speaker: AudioLines, mouse: Mouse, package: Package };
function ProductVisual({ product, compact = false }: { product: Product; compact?: boolean }) {
  const [failed, setFailed] = useState(false);
  const Icon = icons[product.icon as keyof typeof icons] || icons[product.id as keyof typeof icons] || Package;
  return product.image_url && !failed ? (
    <img className={compact ? 'product-photo compact' : 'product-photo'} src={product.image_url}
      alt={`${product.name} · ${product.variant}`} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
  ) : <Icon className={compact ? 'product-icon-small' : 'product-object'} size={compact ? 42 : 116} strokeWidth={1.3} />;
}

function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [watches, setWatches] = useState<Permission[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [histories, setHistories] = useState<Record<string, Observation[]>>({});
  const [page, setPage] = useState('Overview');
  const [checkoutMode, setCheckoutMode] = useState<'merchant_handoff' | 'sandbox'>('merchant_handoff');
  const [sandboxTotal, setSandboxTotal] = useState('');
  const [bankEvidence, setBankEvidence] = useState<{last4: string; credit_limit_cents: number; available_cents: number; verified_at: string} | null>(null);
  const [catalogQuery, setCatalogQuery] = useState('');
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [grok, setGrok] = useState(false);
  const [payments, setPayments] = useState('simulation');
  const [cardSummary, setCardSummary] = useState<CardSummary | null>(null);
  const [ledger, setLedger] = useState<Ledger>({ charges: [], nessie: [] });
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Product | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [inventoryStatus, setInventoryStatus] = useState('');
  const [budget, setBudget] = useState('180');
  const [deadline, setDeadline] = useState('7');
  const [parsedExpiration, setParsedExpiration] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [draft, setDraft] = useState<Permission | null>(null);
  const [purchaseMode, setPurchaseMode] = useState<'ask_first' | 'auto_buy'>('ask_first');
  const [busy, setBusy] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [adminToken, setAdminToken] = useState('');
  const [demoCredit, setDemoCredit] = useState('1');
  const [demoCardPermission, setDemoCardPermission] = useState('');
  const [chat, setChat] = useState('');
  const [answer, setAnswer] = useState('');
  const [toolCalls, setToolCalls] = useState<string[]>([]);
  const nessieCards = watches.filter((w) => w.card?.nessie_account_id);

  async function refresh() {
    const [p, w, o, n] = await Promise.all([
      api<Product[]>('/products'),
      api<Permission[]>('/permissions'),
      api<Order[]>('/orders'),
      api<Notice[]>('/notifications'),
    ]);
    setProducts(p);
    setWatches(w.filter((watch) => p.some((product) => product.source_kind === 'merchant' && product.id === watch.product_id)));
    setOrders(o);
    setNotices(n);
    // Card summary / ledger are extra; a bank hiccup must never blank the rest of the page.
    const bankReads = Promise.all([api<CardSummary>('/payments/card')
      .then(setCardSummary)
      .catch(() => setCardSummary(null)),
    api<Ledger>('/payments/ledger')
      .then(setLedger)
      .catch(() => setLedger({ charges: [], nessie: [] }))]);
    const history = await Promise.all(
      p.map(
        async (product) =>
          [product.id, await api<Observation[]>(`/products/${product.id}/prices`)] as const,
      ),
    );
    setHistories(Object.fromEntries(history));
    await bankReads;
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
      await refresh().catch(() => {});
    } finally {
      setBusy(false);
    }
  }
  function openWatch(p: Product, mode: 'merchant_handoff' | 'sandbox' = 'merchant_handoff') {
    if (!p.can_request) { setError('This variant is sold out or its availability is unverified.'); return; }
    setCheckoutMode(mode);
    setPickerOpen(false);
    setError('');
    setSandboxTotal((p.price_cents * 0.9 / 100).toFixed(2));
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
  const visibleProducts = products.filter((p) => p.source_kind === 'merchant'
    && `${p.name} ${p.category} ${p.seller} ${p.variant}`.toLowerCase().includes(catalogQuery.toLowerCase()));
  const SelectedIcon = selected ? icons[selected.icon as keyof typeof icons] || icons[selected.id as keyof typeof icons] || Package : Radar;
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
            Personal account<small>Price watches &amp; limits</small>
          </div>
          <ChevronRight size={15} />
        </div>
        <span className="nav-label">ACCOUNT</span>
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
            <strong>You set the limit</strong>
            <p>Every sandbox permission issues its own card. Ask-first stays on unless you choose auto-run.</p>
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
              Signed in<small>Your price watches</small>
            </div>
            <CircleHelp size={17} />
          </div>
        </div>
      </aside>

      <div className="main-shell">
        <header>
          <div className="breadcrumb">
            Account <ChevronRight size={13} /> <strong>{page}</strong>
          </div>
          <div className="header-right">
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
              <div className="eyebrow">DealOne price protection</div>
              <h1>
                {page === 'Overview'
                  ? 'Set your price. Stay in control.'
                  : page === 'Watchlist'
                    ? 'Your watches and spending limits'
                    : page === 'Activity'
                      ? 'Transactions and alerts'
                      : 'Ask Grok to find a deal'}
              </h1>
              <p>
                {page === 'Overview'
                  ? 'Watch an item, set a maximum, and decide whether we ask before buying.'
                  : page === 'Watchlist'
                    ? 'Review active cards, budgets, and purchase modes in one place.'
                    : page === 'Activity'
                      ? 'See charges, settlements, and declines as they happen.'
                      : 'Describe what you want. We’ll match it to a catalog item you can watch.'}
              </p>
            </div>
            <Button onClick={() => { const p = products.find((p) => p.can_request); if (p) openWatch(p); else setPickerOpen(true); }}>
              <Plus size={17} />
              Start a watch
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
                        <span /> VIRTUAL CARD CONTROLS
                      </span>
                      <h2>
                        Wait for your price.
                        <br />
                        Never overpay.
                      </h2>
                      <p>
                        DealOne tracks the total, checks your limit, and only
                        <br className="desktop" /> moves when you say so — or when auto-buy is on.
                      </p>
                      <button onClick={() => setPage('Grok assistant')}>
                        Find an item with Grok <ArrowUpRight size={17} />
                      </button>
                    </div>
                    <div className="hero-art" aria-hidden="true">
                      <div className="orbit orbit-one" />
                      <div className="orbit orbit-two" />
                      <div className="floating-card">
                        <div className="card-top">
                          <ShieldCheck size={23} />
                          <span>DealOne virtual</span>
                        </div>
                        <div className="card-chip" />
                        <div className="card-number">•••• &nbsp; •••• &nbsp; •••• &nbsp; DEMO</div>
                        <div className="card-bottom">
                          <span>ONE CHARGE · YOUR LIMIT</span>
                          <Command size={22} />
                        </div>
                      </div>
                    </div>
                  </section>
                  <section className="stats">
                    <div>
                      <button className="stat-icon" aria-label="View active watches" onClick={() => setPage('Watchlist')}><Radar size={20} /></button>
                      <span>
                        Active watches
                        <strong>
                          {activeWatches}
                          <small> real items being watched</small>
                        </strong>
                      </span>
                    </div>
                    <div>
                      <button className="stat-icon" aria-label="Review authorized budgets" onClick={() => setPage('Watchlist')}><Wallet size={20} /></button>
                      <span>
                        Authorized budget
                        <strong>
                          {money(committed)}
                          <small> combined spending caps</small>
                        </strong>
                      </span>
                    </div>
                    <div>
                      <button className="stat-icon" aria-label="View purchase receipts" onClick={() => setPage('Activity')}><CheckCheck size={20} /></button>
                      <span>
                        Sandbox transactions
                        <strong>
                          {completed.length}
                          <small> banking demonstrations</small>
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
                        {page === 'Watchlist' ? 'Your watches' : 'Catalog'}{' '}
                        <span>{page === 'Watchlist' ? watches.length : visibleProducts.length}</span>
                      </h2>
                      <p>
                        {page === 'Watchlist'
                          ? 'Permissions, card limits, and purchase status.'
                          : 'Pick an item, set your max, and start watching.'}
                      </p>
                    </div>
                    <span className="live-label">
                      <i /> Merchant snapshots · refreshes paced by store
                    </span>
                  </div>
                  {page === 'Watchlist' && (
                    <div className="watch-list">
                      {watches.length === 0 ? (
                        <div className="empty">
                          <Radar />
                          <h3>No watches yet</h3>
                          <p>Start a watch to issue a virtual card and set your price limit.</p>
                        </div>
                      ) : (
                        watches.map((w) => {
                          const product = products.find((p) => p.id === w.product_id);
                          const canBuy =
                            w.status === 'active' &&
                            product != null && product.can_request && w.checkout_mode === 'sandbox' &&
                            (w.sandbox_total_cents || product.total_cents) <= w.max_total_cents;
                          return (
                            <div className="watch-row" key={w.id}>
                              <div>
                                <strong>{product?.name}</strong>
                                <small>{w.variant}</small>
                                <small className="watch-meta">
                                  {w.checkout_mode === 'sandbox' ? 'Sandbox banking demo' : 'Merchant price alert'}
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
                                {product?.source_url && <a className="button button-outline" href={product.source_url} target="_blank" rel="noopener noreferrer">Review at store <ArrowUpRight size={14} /></a>}
                                {w.card?.nessie_account_id && <Button variant="outline" onClick={() => run(async () => { setBankEvidence(await api(`/payments/cards/${w.id}/verify`)); setPage('Activity'); })}>Verify Nessie card</Button>}
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
                                {['draft', 'active', 'paused', 'declined'].includes(w.status) && (
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
                  <div className="catalog-controls">
                    <div className="catalog-tabs"><strong>Keychron official store</strong>
                      <Button variant="outline" disabled={!products.some((p) => p.can_request)} onClick={() => { const p = products.find((p) => p.can_request); if (p) openWatch(p, 'sandbox'); }}>{payments === 'nessie' ? 'Try Nessie sandbox' : 'Try banking sandbox'}</Button>
                    </div>
                    <label className="catalog-search">Find an item
                      <input aria-label="Find an item" placeholder="Keyboard, mouse, Keychron…" value={catalogQuery} onChange={(e) => setCatalogQuery(e.target.value)} />
                    </label>
                  </div>
                  <p className="catalog-disclosure">Real products from one approved store. The merchant confirms stock for each variant. Sold-out or unverified items cannot be requested. Nessie sandbox mode demonstrates banking without placing a merchant order. Banking provider: {payments === 'nessie' ? 'Nessie v2 API' : 'local simulator (Nessie not connected)'}.</p>
                  <Button variant="outline" disabled={busy} onClick={() => run(async () => {
                    const result = await api<{message: string}>('/catalog/refresh', {method: 'POST'});
                    setInventoryStatus(result.message);
                  })}>Refresh merchant inventory</Button>
                  {inventoryStatus && <p role="status" className="fineprint">{inventoryStatus}</p>}
                  {visibleProducts.length === 0 && <div className="empty"><Package /><h3>{catalogQuery ? 'No matching items yet.' : 'Waiting for merchant prices.'}</h3><p>{catalogQuery ? 'Try another product name or ask Grok about the supported catalog.' : 'Merchant data may be temporarily unavailable. No products will be invented while the merchant is unavailable.'}</p></div>}
                  <section className="product-grid">
                    {visibleProducts.map((p) => {
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
                            <ProductVisual key={p.id} product={p} />
                            {p.original_cents > p.price_cents && <span className="discount">
                              <ArrowDownRight size={12} />
                              {Math.round((1 - p.price_cents / p.original_cents) * 100)}%
                            </span>}
                          </div>
                          <div className="product-info">
                            <span className="seller">
                              {p.seller} <ShieldCheck size={11} />
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
                              {p.original_cents > p.price_cents && <del>{money(p.original_cents)}</del>}
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
                                  stroke="#0070a8"
                                  strokeWidth="2"
                                  vectorEffect="non-scaling-stroke"
                                />
                              </svg>
                              <div>
                                <span>Observed price history</span>
                                <span>{p.source_kind === 'merchant' ? 'Before tax & shipping' : `All in ${money(p.total_cents)}`}</span>
                              </div>
                            </div>
                            {p.source_kind === 'merchant' && <div className="merchant-provenance">
                              <span>{p.price_checked_at ? `Checked ${new Date(p.price_checked_at).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})}${Date.now() - new Date(p.price_checked_at).getTime() > 180000 ? ' · refresh delayed' : ''}` : 'Price not checked'}</span>
                              <a href={p.source_url || undefined} target="_blank" rel="noopener noreferrer">View at {p.seller} <ArrowUpRight size={13} /></a>
                            </div>}
                            <p className={`availability ${p.stock_status}`}>{p.can_request ? 'Available to order · merchant verified' : p.stock_status === 'sold_out' ? 'Sold out · cannot request' : 'Availability unverified · cannot request'}</p>
                            <Button variant="outline" disabled={!p.can_request} onClick={() => openWatch(p)}>
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
                    <h2>Sandbox receipts</h2>
                    <p className="fineprint">These are banking demonstrations for real products. No retailer order has been placed.</p>
                    {bankEvidence && <div className="review"><strong>Verified directly with Nessie · •••• {bankEvidence.last4}</strong><p>Bank limit {money(bankEvidence.credit_limit_cents)} · Available credit {money(bankEvidence.available_cents)}</p><small>{new Date(bankEvidence.verified_at).toLocaleString()}</small></div>}
                    {orders.length === 0 && (
                      <p className="muted">
                        No purchases yet. Every completed order will leave a trail here.
                      </p>
                    )}
                    {orders.map((o) => (
                      <div className="receipt" key={o.id}>
                        <span className={`status ${o.status}`}>{o.status}</span>
                        <strong>{money(o.total_cents)}</strong>
                        <p>{o.confirmation || (o.status === 'declined' ? 'Bank declined' : 'Awaiting confirmation')}</p>
                        <small>
                          {o.error ||
                            (o.payment_reference && !o.payment_reference.startsWith('sim_')
                              ? `Nessie · ${o.payment_reference}${o.bank_status ? ` · ${o.bank_status}` : ''}`
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
                  <section className="panel ledger-panel">
                    <h2>Bank ledger</h2>
                    <p className="muted">
                      {payments === 'nessie'
                        ? 'DealOne charge map plus live Nessie sandbox transactions.'
                        : 'Nessie ledger appears when PAYMENTS_MODE=nessie.'}
                    </p>
                    {ledger.charges.length === 0 && ledger.nessie.length === 0 ? (
                      <p className="muted">No bank charges yet.</p>
                    ) : (
                      <>
                        {ledger.charges.map((c) => (
                          <div className="ledger-row" key={`local-${c.transaction_id}`}>
                            <span className={`status ${c.status}`}>{c.status}</span>
                            <strong>{money(c.amount_cents || 0)}</strong>
                            <small>
                              DealOne map · {c.transaction_id}
                              {c.account_id ? ` · ${c.account_id}` : ''}
                            </small>
                          </div>
                        ))}
                        {ledger.nessie.map((c) => (
                          <div className="ledger-row" key={`nessie-${c.transaction_id}`}>
                            <span className={`status ${c.status}`}>{c.status}</span>
                            <strong>{money(c.amount_cents || 0)}</strong>
                            <small>
                              Nessie · {c.description || c.transaction_id}
                              {c.account_id ? ` · ${c.account_id}` : ''}
                            </small>
                          </div>
                        ))}
                      </>
                    )}
                  </section>
                </div>
              )}

              {page === 'Grok assistant' && (
                <section className="assistant-panel panel">
                  <div className="assistant-avatar">
                    <Sparkles size={29} />
                  </div>
                  <h2>Tell Grok what you’re shopping for</h2>
                  <p>Ask about products, price history, or your activated watches.</p>
                  <span className={`status ${grok ? 'active' : 'draft'}`}>
                    {grok ? 'Grok connected' : 'Grok API key not configured'}
                  </span>
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      run(async () => {
                        const result = await api<{ message: string; inventory?: {message: string}; tools: { tool: string; result: unknown }[] }>(
                          '/assistant',
                          { method: 'POST', body: JSON.stringify({ text: chat }) },
                        );
                        setAnswer(result.message);
                        setToolCalls(result.tools.map((t) => t.tool));
                        setInventoryStatus(result.inventory?.message || '');
                        setRecommendations([...new Set(result.tools.filter((t) => t.tool === 'find_product' && Array.isArray(t.result))
                          .flatMap((t) => (t.result as Product[]).map((p) => p.id)))]);
                        const created = result.tools.find((t) => t.tool === 'create_watch_draft' && (t.result as Permission)?.status === 'draft');
                        if (created) {
                          const permission = created.result as Permission;
                          const catalog = await api<Product[]>('/products');
                          const product = catalog.find((p) => p.id === permission.product_id);
                          if (product?.can_request) { openWatch(product); setBudget(String(permission.max_total_cents / 100)); setDraft(permission); }
                        }
                      });
                    }}
                  >
                    <label htmlFor="chat">Your message</label>
                    <textarea
                      id="chat"
                      value={chat}
                      onChange={(e) => setChat(e.target.value)}
                      placeholder="Find a Keychron keyboard and show me its price history…"
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
                  {recommendations.length > 0 && <div className="grok-products" aria-label="Grok product matches">
                    {products.filter((p) => recommendations.includes(p.id)).map((p) => <div className="grok-product" key={p.id}>
                      <ProductVisual key={p.id} product={p} compact />
                      <div><strong>{p.name}</strong><small>{p.variant} · {p.seller} · {money(p.price_cents)} · {p.can_request ? 'Available' : p.stock_status === 'sold_out' ? 'Sold out' : 'Availability unverified'}</small></div>
                      <Button disabled={!p.can_request} onClick={() => openWatch(p)}>Watch this item</Button>
                    </div>)}
                  </div>}
                  <p className="fineprint">
                    Grok can request purchases only under rules you have already activated. Only available products from the approved store can be requested. Product text cannot change purchase rules. Nessie demonstrates sandbox banking. Real merchant orders require merchant checkout.
                  </p>
                </section>
              )}
            </>
          )}
          <footer>
            <span>DealOne © {new Date().getFullYear()}</span>
            <span>Independent hackathon project · Not affiliated with Capital One</span>
            <span>Grok × Tiger Data × Vultr</span>
          </footer>
          <p className="legal-disclaimer">DealOne is an independent student/hackathon project. It is not a Capital One product. Nessie is used only as a public sandbox API.</p>
        </main>
      </div>

      {pickerOpen && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="picker-title">
        <button className="modal-close" aria-label="Close item picker" onClick={() => setPickerOpen(false)}><X size={21} /></button>
        <h2 id="picker-title">Choose an available item</h2>
        <p>No recently verified, available variants can be requested right now. Sold-out items remain visible so you can check their status.</p>
        {products.map((p) => <div className="selected-product" key={p.id}><ProductVisual product={p} compact /><div><strong>{p.name}</strong><small>{p.variant} · {p.can_request ? 'Available' : p.stock_status === 'sold_out' ? 'Sold out' : 'Availability unverified'}</small></div><Button disabled={!p.can_request} onClick={() => openWatch(p)}>Watch this item</Button></div>)}
        <Button disabled={busy} onClick={() => run(async () => { const result = await api<{message: string}>('/catalog/refresh', {method:'POST'}); setInventoryStatus(result.message); })}>Check merchant availability</Button>
        <p role="status">{inventoryStatus || 'The merchant may limit refresh frequency. We never substitute invented stock.'}</p>
      </section></div>}
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
              <SelectedIcon size={24} />
            </span>
            <h2 id="watch-title">
              {draft ? 'Confirm your purchase rules' : 'Set your price and limit'}
            </h2>
            {error && <p role="alert" className="error">{error}</p>}
            <p>
              {selected.name} · {selected.variant}
            </p>
            <div className="selected-product">
              <ProductVisual key={selected.id} product={selected} compact />
              <div><strong>{selected.seller}</strong><small>{selected.source_kind === 'merchant' ? 'Real listing · merchant checkout' : 'Practice listing · simulated checkout'}</small></div>
              {selected.source_url && <a href={selected.source_url} target="_blank" rel="noopener noreferrer">View item <ArrowUpRight size={14} /></a>}
            </div>
            <p className="catalog-disclosure">{checkoutMode === 'sandbox' ? `${payments === 'nessie' ? 'Nessie v2 API' : 'Local simulator (Nessie API not connected)'} demonstration using this real item. The checkout total below is a test scenario, not a merchant offer. No real money moves and no merchant order is placed.` : 'Watch this real item and review its price at the store. Automatic retailer checkout is not connected.'}</p>
            <label htmlFor="checkout-mode">Experience</label>
            <select id="checkout-mode" value={checkoutMode} onChange={(e) => setCheckoutMode(e.target.value as 'merchant_handoff' | 'sandbox')}>
              <option value="merchant_handoff">Real price watch and merchant checkout</option>
              <option value="sandbox">Nessie banking demonstration · same real item</option>
            </select>
            {checkoutMode === 'sandbox' && <><label htmlFor="sandbox-total">Sandbox final total (USD, test scenario)</label><input id="sandbox-total" type="number" min="0.01" step="0.01" value={sandboxTotal} onChange={(e) => setSandboxTotal(e.target.value)} required /></>}
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
                {checkoutMode === 'sandbox' && <fieldset className="mode-picker">
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
                      <small>Run this sandbox bank transaction when its rules match.</small>
                    </span>
                  </label>
                </fieldset>}
                <p className="fineprint">
                  {checkoutMode === 'merchant_handoff' ? 'Activate price alerts only. The store confirms the final total before you purchase.' : `This authorizes one sandbox bank transaction for ${money(Math.round(Number(sandboxTotal) * 100))}, within your approved limit. ${purchaseMode === 'ask_first' ? 'You must approve it in the watchlist.' : 'The demonstration worker may submit it automatically.'}`}
                </p>
                <Button
                  className="full-width"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api(`/permissions/${draft.id}/activate`, {
                        method: 'POST',
                        body: JSON.stringify({ purchase_mode: checkoutMode === 'sandbox' ? purchaseMode : 'ask_first', checkout_mode: checkoutMode, sandbox_total_cents: checkoutMode === 'sandbox' ? Math.round(Number(sandboxTotal) * 100) : null }),
                      });
                      setSelected(null);
                      setPage('Watchlist');
                    })
                  }
                >
                  <ShieldCheck size={17} />
                  {checkoutMode === 'merchant_handoff' ? 'Activate price watch' : 'Activate sandbox permission'}
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
                    placeholder={`Watch ${selected.name} under $${budget} for ${deadline} days, or describe a different item…`}
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
                        if (p && p.can_request) { setSelected(p); setSandboxTotal((Math.min(p.price_cents * 0.9, r.max_total_cents) / 100).toFixed(2)); }
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
                <label htmlFor="watch-product">Item to watch</label>
                <select id="watch-product" value={selected.id} onChange={(e) => { const p = products.find((p) => p.id === e.target.value); if (p) openWatch(p); }}>
                  {products.filter((p) => p.source_kind === 'merchant' && p.can_request).map((p) => <option key={p.id} value={p.id}>{p.name} · {p.seller}</option>)}
                </select>
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
            <h2 id="demo-title">Demo controls</h2>
            <p>Real prices and stock come only from Keychron. Use the sandbox experience on an available real item to demonstrate card limits and transactions.</p>
            <label htmlFor="admin-token">Demo administrator token</label>
            <input id="admin-token" type="password" value={adminToken} onChange={(e) => setAdminToken(e.target.value)} autoComplete="off" />
            {payments === 'nessie' && (
              <div className="demo-nessie">
                <h3>Nessie sandbox</h3>
                <p className="fineprint">
                  Seed merchant/customer, force a bank decline, or reset a permission card’s holds.
                </p>
                <label htmlFor="demo-card">Permission card</label>
                <select
                  id="demo-card"
                  value={demoCardPermission}
                  onChange={(e) => setDemoCardPermission(e.target.value)}
                >
                  <option value="">Select a watch with a Nessie card</option>
                  {nessieCards.map((w) => (
                    <option key={w.id} value={w.id}>
                      {products.find((p) => p.id === w.product_id)?.name || w.product_id} · ••••{' '}
                      {w.card?.last4}
                    </option>
                  ))}
                </select>
                <label htmlFor="demo-credit">Available credit for decline demo (USD)</label>
                <input
                  id="demo-credit"
                  type="number"
                  min="0"
                  step="0.01"
                  value={demoCredit}
                  onChange={(e) => setDemoCredit(e.target.value)}
                />
                <div className="demo-nessie-actions">
                  <Button
                    variant="outline"
                    disabled={busy || !adminToken}
                    onClick={() =>
                      run(async () => {
                        await api('/demo/nessie/seed', {
                          method: 'POST',
                          headers: { 'x-demo-admin-token': adminToken },
                        });
                      })
                    }
                  >
                    Seed sandbox
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy || !adminToken || !demoCardPermission}
                    onClick={() =>
                      run(async () => {
                        await api('/demo/nessie/credit', {
                          method: 'POST',
                          headers: { 'x-demo-admin-token': adminToken },
                          body: JSON.stringify({
                            permission_id: demoCardPermission,
                            available_cents: Math.round(Number(demoCredit) * 100),
                          }),
                        });
                      })
                    }
                  >
                    Force low credit
                  </Button>
                  <Button
                    variant="outline"
                    disabled={busy || !adminToken || !demoCardPermission}
                    onClick={() =>
                      run(async () => {
                        await api('/demo/nessie/reset', {
                          method: 'POST',
                          headers: { 'x-demo-admin-token': adminToken },
                          body: JSON.stringify({ permission_id: demoCardPermission }),
                        });
                      })
                    }
                  >
                    Reset card holds
                  </Button>
                </div>
              </div>
            )}
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
