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
  Headphones,
  Keyboard,
  LayoutDashboard,
  Mouse,
  Package,
  LoaderCircle,
  LockKeyhole,
  Plus,
  Radar,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TrendingDown,
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
  source_url: string | null;
  image_url: string | null;
  icon: string;
  price_checked_at: string | null;
};
type Permission = {
  id: string;
  product_id: string;
  max_total_cents: number;
  expires_at: string;
  status: string;
  variant: string;
  seller: string;
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
type Card = { provider: string; credit_limit_cents?: number | null; available_cents?: number | null };
type Notice = { id: string; message: string; created_at: string };
type Observation = { price_cents: number; observed_at: string };
type Rules = { product_id: string; max_total_cents: number; expires_at: string; quantity: 1 };
const icons = { headphones: Headphones, keyboard: Keyboard, speaker: AudioLines, mouse: Mouse, package: Package };
function ProductVisual({ product, compact = false }: { product: Product; compact?: boolean }) {
  const [failed, setFailed] = useState(false);
  const Icon = icons[product.id as keyof typeof icons] || icons[product.icon as keyof typeof icons] || Package;
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
  const [catalogMode, setCatalogMode] = useState<'merchant' | 'demo'>('merchant');
  const [catalogQuery, setCatalogQuery] = useState('');
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [grok, setGrok] = useState(false);
  const [payments, setPayments] = useState('simulation');
  const [card, setCard] = useState<Card | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Product | null>(null);
  const [budget, setBudget] = useState('180');
  const [deadline, setDeadline] = useState('7');
  const [parsedExpiration, setParsedExpiration] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [draft, setDraft] = useState<Permission | null>(null);
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
    // The card view is extra; a bank hiccup must never blank the rest of the page.
    api<Card>('/payments/card')
      .then(setCard)
      .catch(() => setCard(null));
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
  const visibleProducts = products.filter((p) => (p.source_kind || 'demo') === catalogMode
    && `${p.name} ${p.category} ${p.seller} ${p.variant}`.toLowerCase().includes(catalogQuery.toLowerCase()));
  const SelectedIcon = selected ? icons[selected.id as keyof typeof icons] || icons[selected.icon as keyof typeof icons] || Package : Radar;
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
            <p>Every purchase stays within the limits you approve.</p>
            <span>
              <i /> {payments === 'nessie' ? 'Nessie sandbox card' : 'Simulated payments'}
            </span>
            {card?.provider === 'nessie' && card.available_cents != null && (
              <p>
                {money(card.available_cents)} available
                {card.credit_limit_cents != null && ` of ${money(card.credit_limit_cents)}`}
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
            <span className="preview-badge">HACKATHON PREVIEW</span>
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
            <Button onClick={() => (visibleProducts[0] || products[0]) && openWatch(visibleProducts[0] || products[0])}>
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
                      <button className="stat-icon" aria-label="View active watches" onClick={() => setPage('Watchlist')}><Radar size={20} /></button>
                      <span>
                        Active watches
                        <strong>
                          {activeWatches}
                          <small> items on our radar</small>
                        </strong>
                      </span>
                    </div>
                    <div>
                      <button className="stat-icon" aria-label="Review authorized budgets" onClick={() => setPage('Watchlist')}><Wallet size={20} /></button>
                      <span>
                        Authorized budget
                        <strong>
                          {money(committed)}
                          <small> total maximum</small>
                        </strong>
                      </span>
                    </div>
                    <div>
                      <button className="stat-icon" aria-label="View purchase receipts" onClick={() => setPage('Activity')}><CheckCheck size={20} /></button>
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
                        <span>{page === 'Watchlist' ? watches.length : visibleProducts.length}</span>
                      </h2>
                      <p>
                        {page === 'Watchlist'
                          ? 'Your permissions, budgets, and progress.'
                          : 'Find something you love. Let the price come to you.'}
                      </p>
                    </div>
                    <span className="live-label">
                      <i /> {catalogMode === 'merchant' ? 'Merchant feeds · every 5 min' : 'Practice checks · every 10 sec'}
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
                        watches.map((w) => (
                          <div className="watch-row" key={w.id}>
                            <div>
                              <strong>{products.find((p) => p.id === w.product_id)?.name}</strong>
                              <small>{w.variant}</small>
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
                                      });
                                    })
                                  }
                                >
                                  Activate
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
                        ))
                      )}
                    </div>
                  )}
                  <div className="catalog-controls">
                    <div className="catalog-tabs" aria-label="Product source">
                      <button aria-pressed={catalogMode === 'merchant'} onClick={() => setCatalogMode('merchant')}>Real stores</button>
                      <button aria-pressed={catalogMode === 'demo'} onClick={() => setCatalogMode('demo')}>Practice checkout</button>
                    </div>
                    <label className="catalog-search">Find an item
                      <input aria-label="Find an item" placeholder="Keyboard, mouse, Keychron…" value={catalogQuery} onChange={(e) => setCatalogQuery(e.target.value)} />
                    </label>
                  </div>
                  <p className="catalog-disclosure">{catalogMode === 'merchant'
                    ? 'Real Keychron listings. Prices refresh every 5 minutes. Tax, shipping and stock need confirmation at the merchant. Checkout opens at the store.'
                    : 'Practice products and payments are simulated. Use this catalog to try the complete automatic checkout flow.'}</p>
                  {visibleProducts.length === 0 && <div className="empty"><Package /><h3>{catalogQuery ? 'No matching items yet.' : 'Waiting for merchant prices.'}</h3><p>{catalogQuery ? 'Try another product name or ask Grok about the supported catalog.' : 'Merchant data may be temporarily unavailable. You can still try Practice checkout.'}</p></div>}
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
                            <span className="discount" hidden={p.original_cents <= p.price_cents}>
                              <ArrowDownRight size={12} />
                              {Math.round((1 - p.price_cents / p.original_cents) * 100)}%
                            </span>
                          </div>
                          <div className="product-info">
                            <span className="seller">
                              {p.seller} <ShieldCheck size={11} />
                            </span>
                            <h3>{p.name}</h3>
                            <p>{p.variant}</p>
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
                              <span>{p.price_checked_at ? `Checked ${new Date(p.price_checked_at).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})}${Date.now() - new Date(p.price_checked_at).getTime() > 900000 ? ' · refresh delayed' : ''}` : 'Price not checked'}</span>
                              <a href={p.source_url || undefined} target="_blank" rel="noopener noreferrer">View at {p.seller} <ArrowUpRight size={13} /></a>
                            </div>}
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
                        const result = await api<{ message: string; tools: { tool: string; result: unknown }[] }>(
                          '/assistant',
                          { method: 'POST', body: JSON.stringify({ text: chat }) },
                        );
                        setAnswer(result.message);
                        setToolCalls(result.tools.map((t) => t.tool));
                        setRecommendations([...new Set(result.tools.filter((t) => t.tool === 'find_product' && Array.isArray(t.result))
                          .flatMap((t) => (t.result as Product[]).map((p) => p.id)).filter((id) => products.some((p) => p.id === id)))]);
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
                      <div><strong>{p.name}</strong><small>{p.seller} · {money(p.price_cents)}</small></div>
                      <Button onClick={() => openWatch(p)}>Watch this item</Button>
                    </div>)}
                  </div>}
                  <p className="fineprint">
                    Grok can request purchases only under rules you have already activated. Payments
                    in this preview are simulated.
                  </p>
                </section>
              )}
            </>
          )}
          <footer>
            <span>DealOne © {new Date().getFullYear()}</span>
            <span>Independent hackathon project · Capital One–inspired design</span>
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
              <SelectedIcon size={24} />
            </span>
            <h2 id="watch-title">
              {draft ? 'Your rules. Your call.' : 'Put a good deal in motion.'}
            </h2>
            <p>
              {selected.name} · {selected.variant}
            </p>
            <div className="selected-product">
              <ProductVisual key={selected.id} product={selected} compact />
              <div><strong>{selected.seller}</strong><small>{selected.source_kind === 'merchant' ? 'Real listing · merchant checkout' : 'Practice listing · simulated checkout'}</small></div>
              {selected.source_url && <a href={selected.source_url} target="_blank" rel="noopener noreferrer">View item <ArrowUpRight size={14} /></a>}
            </div>
            {selected.source_kind === 'merchant' && <p className="catalog-disclosure">DealOne monitors this real item and sends price alerts. Complete your purchase at the store after confirming the final total. Automatic merchant checkout is not connected.</p>}
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
                <p className="fineprint">
                  {selected.source_kind === 'merchant' ? 'Activate to monitor this item and receive price alerts. The merchant confirms taxes, shipping and availability before you purchase.' : 'Activating lets DealOne automatically complete one simulated purchase when every rule matches. Tax and shipping must fit your maximum.'}
                </p>
                <Button
                  className="full-width"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api(`/permissions/${draft.id}/activate`, { method: 'POST' });
                      setSelected(null);
                      setPage('Watchlist');
                    })
                  }
                >
                  <ShieldCheck size={17} />
                  {selected.source_kind === 'merchant' ? 'Activate price watch' : 'Activate automatic purchase'}
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
                <label htmlFor="watch-product">Item to watch</label>
                <select id="watch-product" value={selected.id} onChange={(e) => { const p = products.find((p) => p.id === e.target.value); if (p) openWatch(p); }}>
                  {products.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.seller}</option>)}
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
                {products.filter((p) => p.source_kind !== 'merchant').map((p) => (
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
