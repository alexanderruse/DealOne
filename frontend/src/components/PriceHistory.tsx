import { money } from '../lib';

export type Observation = { price_cents: number; observed_at: string };
const MAX_GAP_MS = 30 * 60 * 1000;

export function PriceHistory({ observations, name }: { observations?: Observation[]; name: string }) {
  const byTime = new Map<number, Observation>();
  for (const row of Array.isArray(observations) ? observations : []) {
    if (!row || !Number.isSafeInteger(row.price_cents) || row.price_cents <= 0) continue;
    const time = Date.parse(row.observed_at);
    if (Number.isFinite(time)) byTime.set(time, row);
  }
  const samples = [...byTime.entries()].sort(([a], [b]) => a - b).slice(-30);
  if (samples.length < 2) return null;

  const low = Math.min(...samples.map(([, row]) => row.price_cents));
  const high = Math.max(...samples.map(([, row]) => row.price_cents));
  if (low === high) return <p className="price-history-unchanged">No observed price change</p>;

  const start = samples[0][0];
  const end = samples[samples.length - 1][0];
  const points = samples.map(([time, row]) => ({
    time, row,
    x: 12 + ((time - start) / (end - start)) * 296,
    y: 94 - ((row.price_cents - low) / (high - low)) * 78,
  }));
  const segments: typeof points[] = [];
  for (const point of points) {
    const segment = segments[segments.length - 1];
    if (!segment || point.time - segment[segment.length - 1].time > MAX_GAP_MS) segments.push([point]);
    else segment.push(point);
  }
  const label = (time: number) => new Date(time).toLocaleString([], {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  });

  return <figure className="price-history" aria-label={`Price history for ${name}`}>
    <figcaption>Observed price history <span>{samples.length} observations</span></figcaption>
    <div className="price-history-range"><span>Low {money(low)}</span><span>High {money(high)}</span></div>
    <svg viewBox="0 0 320 110" role="img" aria-label={`Observed prices for ${name}`}>
      <path d="M12 16H308 M12 94H308" fill="none" stroke="#dedede" />
      {segments.filter((segment) => segment.length > 1).map((segment) =>
        <polyline key={segment[0].time} points={segment.map((point) => `${point.x},${point.y}`).join(' ')}
          fill="none" stroke="#b91c2b" strokeWidth="2" vectorEffect="non-scaling-stroke" />)}
      {points.map((point) => <circle key={point.time} cx={point.x} cy={point.y} r="3.5" fill="#b91c2b">
        <title>{money(point.row.price_cents)} · {label(point.time)}</title>
      </circle>)}
    </svg>
    <div className="price-history-dates"><time dateTime={new Date(start).toISOString()}>{label(start)}</time><time dateTime={new Date(end).toISOString()}>{label(end)}</time></div>
    <p>USD item prices · before tax &amp; shipping</p>
    {segments.length > 1 && <p>Gaps over 30 minutes are left unconnected.</p>}
    <details><summary>View price observations</summary><ul>{samples.map(([time, row]) =>
      <li key={time}><time dateTime={new Date(time).toISOString()}>{label(time)}</time> — {money(row.price_cents)}</li>)}</ul></details>
  </figure>;
}
