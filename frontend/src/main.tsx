import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ArrowUpRight,
  AudioLines,
  Bell,
  Camera,
  Check,
  CheckCheck,
  ChevronRight,
  Command,
  CreditCard,
  ExternalLink,
  Globe,
  Headphones,
  Keyboard,
  LayoutDashboard,
  Mouse,
  Package,
  LoaderCircle,
  Plus,
  Radar,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  TrendingDown,
  Usb,
  Wallet,
  X,
} from 'lucide-react';
import { Button } from './components/ui/button';
import { api, money } from './lib';
import { PriceHistory } from './components/PriceHistory';
import { usePriceHistory } from './usePriceHistory';
import './style.css';

type ApprovedDomain = {
  id: string;
  domain: string;
  display_name: string;
  created_at: string;
};

type AIBudget = {
  budget_limit_usd: number;
  spent_usd: number;
  remaining_usd: number;
  recovery_seconds: number;
  window_seconds: number;
  model: string;
};

type CheckoutDemoRun = {
  id: string;
  permission_id: string;
  owner: string;
  product_id: string;
  product_name: string;
  product_variant: string;
  seller: string;
  card_last4?: string | null;
  trigger_type: 'manual_admin' | 'price_drop' | 'manual_user';
  status: 'queued' | 'running' | 'completed' | 'blocked' | 'failed';
  blocker_reason?: string | null;
  nessie_verified_limit_cents: number;
  fields_completed: string[];
  has_screenshot: boolean;
  has_payment_screenshot: boolean;
  is_submitted: boolean;
  created_at: string;
};

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
  supports_checkout_demo?: boolean;
  domain_approved?: boolean;
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
  product?: Product | null;
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
type Rules = { product_id: string; max_total_cents: number; expires_at: string; quantity: 1 };
type Listing = {handle: string; name: string; seller: string; source_url: string; image_url: string | null; price_from_cents: number};
type CatalogTask = {task_id: string; store: string; status: string; message: string; checked_at?: string; next_check_at?: string; listings: Listing[]; products: Product[]};
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
  const refreshPending = useRef(false);
  const [page, setPage] = useState(window.location.pathname === '/screenshots' ? 'Evidence Gallery' : 'Overview');
  const [checkoutMode, setCheckoutMode] = useState<'merchant_handoff' | 'sandbox'>('merchant_handoff');
  const [sandboxTotal, setSandboxTotal] = useState('');
  const [bankEvidence, setBankEvidence] = useState<{last4: string; credit_limit_cents: number; available_cents: number; verified_at: string} | null>(null);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [grok, setGrok] = useState(false);
  const [payments, setPayments] = useState('simulation');
  const [ledger, setLedger] = useState<Ledger>({ charges: [], nessie: [] });
  const [approvedDomains, setApprovedDomains] = useState<ApprovedDomain[]>([]);
  const [newDomain, setNewDomain] = useState('');
  const [aiBudget, setAiBudget] = useState<AIBudget | null>(null);
  const [checkoutDemos, setCheckoutDemos] = useState<CheckoutDemoRun[]>([]);
  const [selectedDemoWatch, setSelectedDemoWatch] = useState('');
  const [demoStatusMsg, setDemoStatusMsg] = useState('');
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Product | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [watchUrl, setWatchUrl] = useState('');
  const [linkTask, setLinkTask] = useState<CatalogTask | null>(null);
  const [linkError, setLinkError] = useState('');
  const [checkingLink, setCheckingLink] = useState(false);
  const [checkoutRuns, setCheckoutRuns] = useState<CheckoutDemoRun[]>([]);
  const linkRequest = useRef(0);
  const [catalogTasks, setCatalogTasks] = useState<CatalogTask[]>([]);
  const [taskIds, setTaskIds] = useState<string[]>([]);
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
    if (refreshPending.current) return;
    refreshPending.current = true;
    try {
      const [p, w, o, n] = await Promise.all([
        api<Product[]>('/products'),
        api<Permission[]>('/permissions'),
        api<Order[]>('/orders'),
        api<Notice[]>('/notifications'),
      ]);
      setProducts(p);
      setWatches(w.filter((watch) => (watch.product || p.find((product) => product.id === watch.product_id))?.source_kind === 'merchant'));
      setOrders(o);
      setNotices(n);
      const bankReads = Promise.all([
        api<Ledger>('/payments/ledger').then(setLedger).catch(() => setLedger({ charges: [], nessie: [] })),
        api<ApprovedDomain[]>('/demo/approved-domains').then(setApprovedDomains).catch(() => setApprovedDomains([])),
        api<AIBudget>('/ai/budget').then(setAiBudget).catch(() => setAiBudget(null)),
        api<CheckoutDemoRun[]>('/demo/checkout-demos').then(setCheckoutDemos).catch(() => {}),
      ]);
      await bankReads;
    } finally {
      refreshPending.current = false;
    }
  }
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setInterval>;
    api<{ ai_configured?: boolean; grok_configured: boolean; payments: string }>('/session')
      .then(async (s) => {
        if (!active) return;
        setGrok(Boolean(s.ai_configured ?? s.grok_configured));
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

  useEffect(() => {
    if (page !== 'Activity') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function loadRuns() {
      try {
        const runs = await api<CheckoutDemoRun[]>('/activity/checkout-runs');
        if (active) setCheckoutRuns(runs);
      } catch {
        // A transient failure should leave the last known statuses visible.
      } finally {
        if (active) timer = setTimeout(loadRuns, 4000);
      }
    }
    void loadRuns();
    return () => { active = false; clearTimeout(timer); };
  }, [page]);

  function trackTask(task: CatalogTask) {
    setTaskIds((ids) => [...new Set([...ids, task.task_id])].slice(-8));
    setCatalogTasks((tasks) => [...tasks.filter((old) => old.task_id !== task.task_id), task].slice(-8));
  }
  useEffect(() => {
    if (!taskIds.length) return;
    let active = true;
    const update = async () => {
      const tasks = await Promise.all(taskIds.map((id) => api<CatalogTask>(`/catalog/tasks/${id}`)));
      if (!active) return;
      setCatalogTasks(tasks);
      setRecommendations((ids) => [...new Set([...ids, ...tasks.flatMap((task) => task.products.map((product) => product.id))])]);
    };
    update().catch(() => {});
    const timer = setInterval(() => { update().catch(() => {}); }, 4000);
    return () => {active = false; clearInterval(timer);};
  }, [taskIds.join(',')]);

  async function checkWatchLink() {
    const request = ++linkRequest.current;
    setCheckingLink(true);
    setLinkError('');
    setLinkTask(null);
    try {
      const task = await api<CatalogTask>('/catalog/inspect', {
        method: 'POST', body: JSON.stringify({ url: watchUrl.trim() }),
      });
      if (request === linkRequest.current) setLinkTask(task);
    } catch (cause) {
      if (request === linkRequest.current) setLinkError((cause as Error).message);
    } finally {
      if (request === linkRequest.current) setCheckingLink(false);
    }
  }

  useEffect(() => {
    if (!pickerOpen || !linkTask || linkTask.status !== 'queued') return;
    const timer = setTimeout(() => {
      const request = linkRequest.current;
      api<CatalogTask>(`/catalog/tasks/${linkTask.task_id}`).then((task) => {
        if (request === linkRequest.current) setLinkTask(task);
      }).catch(() => {
        if (request === linkRequest.current) setLinkTask({ ...linkTask });
      });
    }, 4000);
    return () => clearTimeout(timer);
  }, [pickerOpen, linkTask]);

  const requestedVariant = (() => {
    try { return new URL(watchUrl).searchParams.get('variant'); }
    catch { return null; }
  })();
  const linkProducts = (linkTask?.status === 'complete' ? linkTask.products : []).filter((product) =>
    !requestedVariant || product.supports_checkout_demo === false || (product.source_url &&
      new URL(product.source_url).searchParams.get('variant') === requestedVariant));

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
  const watchedProducts = watches.flatMap((watch) => {
    const product = watch.product || products.find((item) => item.id === watch.product_id);
    return product ? [product] : [];
  });
  const histories = usePriceHistory([...new Map(watchedProducts.map((item) => [item.id, item])).values()].slice(0, 250), page === 'Watchlist');
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
        <nav>
          {[
            ['Overview', LayoutDashboard],
            ['Watchlist', Radar],
            ['Activity', Bell],
            ['Shopping Assistant', Sparkles],
            ['Evidence Gallery', Camera],
          ].map(([label, Icon]) => {
            const ItemIcon = Icon as typeof Radar;
            return (
              <button
                key={String(label)}
                aria-label={String(label)}
                className={page === label ? 'nav-item active' : 'nav-item'}
                onClick={() => setPage(String(label))}
              >
                <ItemIcon size={18} />
                <span className="nav-text">{String(label)}</span>
                {label === 'Watchlist' && <span className="nav-count">{activeWatches}</span>}
                {label === 'Evidence Gallery' && checkoutDemos.length > 0 && <span className="nav-count">{checkoutDemos.length}</span>}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <button className="nav-item" onClick={() => setAdminOpen(true)}>
            <SlidersHorizontal size={18} />
            Demo controls
          </button>
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
                      : page === 'Evidence Gallery'
                        ? 'Autonomous Checkout Visual Evidence'
                        : 'DealOne Shopping Assistant powered by Gemini'}
              </h1>
              <p>
                {page === 'Overview'
                  ? 'Watch an item, set a maximum, and decide whether we ask before buying.'
                  : page === 'Watchlist'
                    ? 'Review active cards, budgets, and purchase modes in one place.'
                    : page === 'Activity'
                      ? 'Follow checkout runs, alerts, and sandbox transactions.'
                      : page === 'Evidence Gallery'
                        ? 'Visual proof of Playwright browser checkout runs with masked credentials and strict non-submission safeguards.'
                        : 'Describe what you want. Gemini can check product links from approved merchants.'}
              </p>
            </div>
            <Button onClick={() => { setWatchUrl(''); setLinkTask(null); setLinkError(''); setError(''); setPickerOpen(true); }}>
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

              {page === 'Watchlist' && (
                <>
                  <div className="section-heading"><h2>Your watches <span>{watches.length}</span></h2></div>
                  <div className="watch-list">
                    {watches.length === 0 ? (
                      <div className="empty">
                        <Radar />
                        <h3>No watches yet</h3>
                        <p>Paste a product link to set a price alert.</p>
                      </div>
                    ) : (
                      watches.map((w) => {
                        const product = w.product || products.find((p) => p.id === w.product_id);
                        const canBuy =
                          w.status === 'active' &&
                          product != null && product.can_request && product.domain_approved !== false && w.checkout_mode === 'sandbox' &&
                          (w.sandbox_total_cents || product.total_cents) <= w.max_total_cents;
                        return (
                          <div className="watch-row" key={w.id}>
                            <div>
                              <strong>{product?.name}</strong>
                              <small>{w.variant}</small>
                              {product?.domain_approved === false && <small>Checks paused · domain approval removed</small>}
                              {product && <PriceHistory observations={histories[product.id]} name={product.name} />}
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
                              {w.status === 'draft' && product?.domain_approved !== false && (
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
                </>
              )}

              {page === 'Activity' && (
                <div className="activity-grid">
                  <section className="panel">
                    <h2>Agent activity</h2>
                    {checkoutRuns.map((demo) => (
                      <div className="notice" key={demo.id}>
                        <Camera size={18} />
                        <div>
                          <strong>{demo.product_name}</strong>
                          <p><span className={`status ${demo.status}`}>{demo.status}</span></p>
                          {demo.blocker_reason && <p>{demo.blocker_reason}</p>}
                          {(demo.has_screenshot || demo.has_payment_screenshot) && <Button variant="outline" onClick={() => {
                            setCheckoutDemos((runs) => [demo, ...runs.filter((run) => run.id !== demo.id)]);
                            setPage('Evidence Gallery');
                            requestAnimationFrame(() => document.getElementById(`evidence-${demo.id}`)?.scrollIntoView());
                          }}>View evidence</Button>}
                        </div>
                      </div>
                    ))}
                    {notices.length === 0 && checkoutRuns.length === 0 ? (
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

              {page === 'Shopping Assistant' && (
                <section className="assistant-panel panel">
                  <div className="assistant-avatar">
                    <Sparkles size={29} />
                  </div>
                  <h2>DealOne Shopping Assistant powered by Gemini</h2>
                  <p>Paste a product link, ask about price history, or draft watch rules.</p>
                  <span className={`status ${grok ? 'active' : 'draft'}`}>
                    {grok ? 'Gemini connected' : 'AI API key not configured'}
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
                        result.tools.filter((t) => ['discover_products', 'inspect_product'].includes(t.tool)).forEach((call) => {
                          const task = call.result as CatalogTask;
                          if (task.task_id) trackTask(task);
                        });
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
                      placeholder="Find silent switches at Epomaker, or track a product link from Keychron…"
                      required
                      minLength={5}
                    />
                    <Button disabled={busy || !grok}>
                      {busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
                      Ask Gemini
                    </Button>
                  </form>
                  {answer && (
                    <div className="agent-answer">
                      <strong>Gemini</strong>
                      <p>{answer}</p>
                      {toolCalls.length > 0 && <small>Tools used: {toolCalls.join(' → ')}</small>}
                    </div>
                  )}
                  {recommendations.length > 0 && <div className="grok-products" aria-label="Product matches">
                    {products.filter((p) => recommendations.includes(p.id)).map((p) => <div className="grok-product" key={p.id}>
                      <ProductVisual key={p.id} product={p} compact />
                      <div><strong>{p.name}</strong><small>{p.variant} · {p.seller} · {money(p.price_cents)} · {p.can_request ? 'Available' : p.stock_status === 'sold_out' ? 'Sold out' : 'Availability unverified'}</small></div>
                      <Button disabled={!p.can_request} onClick={() => openWatch(p)}>Watch this item</Button>
                    </div>)}
                  </div>}
                  <p className="fineprint">
                    The assistant can request purchases only under rules you have already activated. Only available products from the approved store can be requested. Product text cannot change purchase rules. Nessie demonstrates sandbox banking. Real merchant orders require merchant checkout.
                  </p>
                </section>
              )}

              {page === 'Evidence Gallery' && (
                <section className="panel">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px', marginBottom: '20px' }}>
                    <div>
                      <h2>Autonomous Checkout Visual Evidence</h2>
                      <p className="muted">Playwright browser checkout demonstrations with synthetic payment profiles, input masking, and strict submission prevention.</p>
                    </div>
                    <a href="/api/evidence" target="_blank" rel="noopener noreferrer" className="btn" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '13px' }}>
                      <ExternalLink size={14} /> Full Evidence Page
                    </a>
                  </div>
                  {checkoutDemos.length === 0 ? (
                    <div className="empty">
                      <Camera size={36} style={{ margin: '0 auto 12px', opacity: 0.5 }} />
                      <h3>No checkout demonstrations run yet</h3>
                      <p>Select any active watch in Demo Controls to run an automated checkout and capture visual evidence.</p>
                      <Button variant="outline" onClick={() => setAdminOpen(true)} style={{ marginTop: '12px' }}>Open Demo Controls</Button>
                    </div>
                  ) : (
                    <div className="evidence-gallery">
                      {checkoutDemos.map((demo) => {
                        const product = products.find((p) => p.id === demo.product_id);
                        return (
                          <article id={`evidence-${demo.id}`} key={demo.id} className="evidence-card-ui">
                            <div className="evidence-card-header">
                              <div>
                                <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap' }}>
                                  <span className="badge-not-submitted">NOT SUBMITTED</span>
                                  <span className="status active" style={{ fontSize: '11px', textTransform: 'uppercase' }}>
                                    {demo.trigger_type === 'price_drop' ? 'Price Drop Auto-Trigger' : 'Admin Manual Trigger'}
                                  </span>
                                  <span className={`status ${demo.status}`} style={{ fontSize: '11px', textTransform: 'uppercase' }}>
                                    {demo.status}
                                  </span>
                                </div>
                                <h3 style={{ fontSize: '18px', fontWeight: 700 }}>{demo.product_name || product?.name || 'Item'}</h3>
                                <p className="muted" style={{ fontSize: '13px' }}>{demo.product_variant || product?.variant} · {demo.seller || product?.seller}</p>
                              </div>
                              <div style={{ textAlign: 'right' }}>
                                <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Nessie Verified Limit</div>
                                <div style={{ fontSize: '18px', fontWeight: 800, color: '#0f766e' }}>{money(demo.nessie_verified_limit_cents || 0)}</div>
                                <div style={{ fontSize: '12px', color: 'var(--muted)', marginTop: '2px' }}>Card: •••• {demo.card_last4 || '4242'}</div>
                                <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '4px' }}>{new Date(demo.created_at).toLocaleString()}</div>
                              </div>
                            </div>
                            <div style={{ margin: '10px 0', fontSize: '13px' }}>
                              <span style={{ fontWeight: 600, color: 'var(--muted)', marginRight: '8px' }}>Completed Fields:</span>
                              {demo.fields_completed && demo.fields_completed.length > 0 ? (
                                demo.fields_completed.map((f) => (
                                  <span key={f} style={{ display: 'inline-block', background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: '4px', padding: '2px 8px', fontSize: '11px', marginRight: '6px', marginBottom: '4px' }}>
                                    {f}
                                  </span>
                                ))
                              ) : (
                                <span className="muted">None recorded</span>
                              )}
                            </div>
                            {demo.blocker_reason && (
                              <div className="trap-note" style={{ margin: '10px 0', padding: '8px 12px', borderRadius: '6px' }}>
                                <strong>Blocker / Outcome:</strong> {demo.blocker_reason}
                              </div>
                            )}
                            <h4>Checkout overview</h4>
                            <div className="evidence-img-container">
                              {demo.has_screenshot ? (
                                <a href={`/api/screenshots/${demo.id}`} target="_blank" rel="noopener noreferrer">
                                  <img src={`/api/screenshots/${demo.id}`} alt="Masked checkout demonstration evidence screenshot" loading="lazy" />
                                </a>
                              ) : (
                                <div style={{ padding: '40px', textAlign: 'center', color: '#94a3b8' }}>
                                  Screenshot not available for this run
                                </div>
                              )}
                            </div>
                            <h4>Payment close-up</h4>
                            {demo.has_payment_screenshot ? (
                              <div className="evidence-img-container payment-evidence">
                                <a href={`/api/screenshots/${demo.id}/payment`} target="_blank" rel="noopener noreferrer">
                                  <img src={`/api/screenshots/${demo.id}/payment`} alt="Masked payment close-up" loading="lazy" />
                                </a>
                              </div>
                            ) : <p className="muted">Payment close-up unavailable for this run.</p>}
                          </article>
                        );
                      })}
                    </div>
                  )}
                </section>
              )}
            </>
          )}
          <footer>
            <span>DealOne © {new Date().getFullYear()}</span>
            <span>Independent hackathon project · Not affiliated with Capital One</span>
            <span>Gemini × TimescaleDB × Vultr</span>
          </footer>
          <p className="legal-disclaimer">DealOne is an independent student/hackathon project. It is not a Capital One product. Nessie is used only as a public sandbox API.</p>
        </main>
      </div>

      {pickerOpen && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="picker-title">
        <button className="modal-close" aria-label="Close product link" onClick={() => {
          linkRequest.current++;
          setCheckingLink(false);
          setPickerOpen(false);
        }}><X size={21} /></button>
        <h2 id="picker-title">Add a product link</h2>
        <p>{approvedDomains.length ? `Approved domains · ${approvedDomains.map((domain) => domain.domain).join(', ')}` : 'No approved domains are available.'}</p>
        <form onSubmit={(event) => { event.preventDefault(); void checkWatchLink(); }}>
          <label htmlFor="watch-url">Product URL</label>
          <input id="watch-url" type="url" value={watchUrl} onChange={(event) => {
            setWatchUrl(event.target.value);
            setLinkTask(null);
            setLinkError('');
          }} required maxLength={500} disabled={checkingLink} />
          <Button disabled={checkingLink || approvedDomains.length === 0}>{checkingLink ? 'Checking…' : 'Check product link'}</Button>
        </form>
        {linkError && <p role="alert">{linkError}</p>}
        {linkTask && <p role="status">{linkTask.message}</p>}
        {linkTask?.status === 'complete' && linkProducts.length === 0 && <p>No verified variant matched this link.</p>}
        {linkProducts.length > 1 && <h3>Choose a variant</h3>}
        {linkProducts.map((product) => <div className="selected-product" key={product.id}>
          <div><strong>{product.name}</strong><small>{product.variant} · {money(product.price_cents)} · {product.can_request ? 'Available' : product.stock_status === 'sold_out' ? 'Sold out' : 'Availability unverified'}</small></div>
          <Button disabled={!product.can_request} onClick={() => openWatch(product)}>Watch this item</Button>
        </div>)}
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
            {selected.supports_checkout_demo !== false && <><label htmlFor="checkout-mode">Experience</label>
            <select id="checkout-mode" value={checkoutMode} onChange={(e) => setCheckoutMode(e.target.value as 'merchant_handoff' | 'sandbox')}>
              <option value="merchant_handoff">Real price watch and merchant checkout</option>
              <option value="sandbox">Nessie banking demonstration · same real item</option>
            </select></>}
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
                    <Sparkles size={14} /> Describe it to Gemini{' '}
                    <small>{grok ? 'Connected' : 'API key needed'}</small>
                  </label>
                  <textarea
                    id="rules-prompt"
                    value={prompt}
                    onChange={(e) => setPrompt(e.target.value)}
                    placeholder={`Watch ${selected.name} under $${budget} for ${deadline} days…`}
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
                        if (r.product_id !== selected.id) throw new Error('Gemini matched a different product. Set the rules for this link below.');
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
                      Gemini deadline: {new Date(parsedExpiration).toLocaleString()}
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

            {adminToken && (
              <>
                {/* AI Spending Controls & Telemetry */}
                <div className="ai-budget-box">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <h3 style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>AI Assistant Spending Controls</h3>
                    <span style={{ fontSize: '11px', color: 'var(--muted)', background: 'var(--surface)', padding: '2px 8px', borderRadius: '4px', border: '1px solid var(--line)' }}>
                      Rolling 3-Hour Window
                    </span>
                  </div>
                  <p className="fineprint" style={{ margin: '6px 0 10px' }}>
                    Shared atomic spending ceiling ($10 / 3hr) enforced across all API replicas and workers in PostgreSQL.
                  </p>
                  {aiBudget ? (
                    <div>
                      <div className="budget-stats-row">
                        <span>Spent: <strong>${aiBudget.spent_usd.toFixed(4)}</strong> / ${aiBudget.budget_limit_usd.toFixed(2)}</span>
                        <span>Remaining: <strong style={{ color: aiBudget.remaining_usd > 1 ? '#0f766e' : '#b91c1c' }}>${aiBudget.remaining_usd.toFixed(4)}</strong></span>
                      </div>
                      <div className="budget-meter">
                        <div
                          className={`budget-meter-fill ${aiBudget.spent_usd / aiBudget.budget_limit_usd > 0.85 ? 'danger' : aiBudget.spent_usd / aiBudget.budget_limit_usd > 0.6 ? 'warn' : ''}`}
                          style={{ width: `${Math.min(100, (aiBudget.spent_usd / aiBudget.budget_limit_usd) * 100)}%` }}
                        />
                      </div>
                      <div className="budget-stats-row" style={{ fontSize: '11px', color: 'var(--muted)' }}>
                        <span>Allowance recovery: {aiBudget.recovery_seconds > 0 ? `${Math.floor(aiBudget.recovery_seconds / 60)}m ${aiBudget.recovery_seconds % 60}s` : 'Full budget available'}</span>
                        <span>Model: {aiBudget.model}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="muted" style={{ fontSize: '12px' }}>Loading budget telemetry…</div>
                  )}
                </div>

                {/* Approved Domains Whitelist */}
                <div className="domains-manager">
                  <h3 style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>Approved Domains Whitelist</h3>
                  <p className="fineprint" style={{ margin: '6px 0 10px' }}>
                    The Playwright worker strictly verifies that target merchant URLs match an approved domain before navigating.
                  </p>
                  <div className="domains-list">
                    {approvedDomains.map((d) => (
                      <div key={d.id} className="domain-row">
                        <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 600 }}>
                          <Globe size={14} style={{ color: 'var(--brand-blue)' }} />
                          {d.domain}
                          <small style={{ color: 'var(--muted)', fontWeight: 'normal' }}>({d.display_name || d.domain})</small>
                        </span>
                        <Button
                          variant="ghost"
                          style={{ padding: '4px 8px', height: 'auto', color: '#dc2626' }}
                          disabled={busy}
                          onClick={() =>
                            run(async () => {
                              await api(`/demo/approved-domains/${d.id}`, {
                                method: 'DELETE',
                                headers: { 'x-demo-admin-token': adminToken },
                              });
                            })
                          }
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <div className="domain-add-row">
                    <input
                      placeholder="e.g. www.keychron.com"
                      value={newDomain}
                      onChange={(e) => setNewDomain(e.target.value)}
                    />
                    <Button
                      variant="outline"
                      disabled={busy || !newDomain.trim()}
                      onClick={() =>
                        run(async () => {
                          await api('/demo/approved-domains', {
                            method: 'POST',
                            headers: { 'x-demo-admin-token': adminToken },
                            body: JSON.stringify({ domain: newDomain.trim() }),
                          });
                          setNewDomain('');
                        })
                      }
                    >
                      + Add Domain
                    </Button>
                  </div>
                </div>

                {/* Checkout Demo Trigger */}
                <div className="checkout-demo-box">
                  <h3 style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>Checkout Demonstration Trigger</h3>
                  <p className="fineprint" style={{ margin: '6px 0 10px' }}>
                    Select an active watch to launch Playwright, fill synthetic customer details + quadrupled Nessie card, and capture visual proof with strict non-submission guarantees.
                  </p>
                  <label htmlFor="demo-watch-select" style={{ fontSize: '12px', fontWeight: 600, color: 'var(--muted)' }}>Select active watch</label>
                  <select
                    id="demo-watch-select"
                    value={selectedDemoWatch}
                    onChange={(e) => setSelectedDemoWatch(e.target.value)}
                  >
                    <option value="">Choose an active watch to demonstrate</option>
                    {watches.filter((w) => w.status === 'active' && w.product?.supports_checkout_demo !== false && w.product?.domain_approved !== false).map((w) => {
                      const prod = w.product || products.find((p) => p.id === w.product_id);
                      return (
                        <option key={w.id} value={w.id}>
                          {prod?.name || w.product_id} ({w.variant}) · Limit {money(w.max_total_cents)} · {w.card ? `•••• ${w.card.last4}` : 'no card'}
                        </option>
                      );
                    })}
                  </select>
                  <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                    <Button
                      disabled={busy || !selectedDemoWatch}
                      onClick={() =>
                        run(async () => {
                          setDemoStatusMsg('Launching Playwright browser automation…');
                          const runResult = await api<CheckoutDemoRun>('/demo/checkout-demo', {
                            method: 'POST',
                            headers: { 'x-demo-admin-token': adminToken },
                            body: JSON.stringify({ permission_id: selectedDemoWatch }),
                          });
                          setDemoStatusMsg(`Demo run queued (${runResult.id.slice(0, 8)}). Worker capturing evidence…`);
                        })
                      }
                    >
                      <Camera size={15} />
                      Run Checkout Demo &amp; Capture Screenshot
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        setPage('Evidence Gallery');
                        setAdminOpen(false);
                      }}
                    >
                      View Evidence Gallery
                    </Button>
                  </div>
                  {demoStatusMsg && (
                    <p style={{ fontSize: '12px', color: 'var(--brand-blue)', marginTop: '8px', fontWeight: 600 }}>
                      {demoStatusMsg}
                    </p>
                  )}
                </div>
              </>
            )}
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
