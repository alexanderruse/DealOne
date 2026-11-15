import { useEffect, useState } from 'react';
import { api } from './lib';
import type { Observation } from './components/PriceHistory';

type ProductVersion = { id: string; price_checked_at: string | null };

export function usePriceHistory(products: ProductVersion[], enabled: boolean) {
  const [history, setHistory] = useState<Record<string, Observation[]>>({});
  // New merchant observations invalidate the cache; routine dashboard polls do not.
  const signature = JSON.stringify(products.map((product) => [product.id, product.price_checked_at]));

  useEffect(() => {
    if (!enabled || !products.length) return;
    let active = true;
    let controller: AbortController | undefined;
    const ids = products.map((product) => product.id);
    async function load() {
      controller?.abort();
      const requestController = new AbortController();
      controller = requestController;
      const timeout = setTimeout(() => requestController.abort(), 3000);
      try {
        const result = await api<Record<string, Observation[]>>('/price-history', {
          method: 'POST', body: JSON.stringify({ product_ids: ids }), signal: requestController.signal,
        });
        if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('Invalid history');
        if (active) setHistory(result);
      } catch {
        // Optional analytics must not prevent product discovery or watch creation.
        if (active) setHistory({});
      } finally {
        clearTimeout(timeout);
      }
    }
    load();
    const timer = setInterval(load, 30_000);
    return () => { active = false; clearInterval(timer); controller?.abort(); };
  }, [signature, enabled]);

  return history;
}
