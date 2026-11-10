// Metrics — Counter, Gauge, Histogram + Registry.
// Mirrors server/shared/observability/metrics.py.

const DEFAULT_BUCKETS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000];

function labelsKey(labels) {
  if (!labels) return '';
  return Object.keys(labels).sort().map(k => `${k}=${labels[k]}`).join(',');
}

class Counter {
  constructor(name, labels) {
    this.type = 'counter';
    this.name = name;
    this.labels = labels || {};
    this.value = 0;
  }
  inc(amount = 1) { this.value += amount; }
  snapshot() { return { type: 'counter', name: this.name, labels: this.labels, value: this.value }; }
}

class Gauge {
  constructor(name, labels) {
    this.type = 'gauge';
    this.name = name;
    this.labels = labels || {};
    this.value = 0;
  }
  set(v) { this.value = v; }
  inc(amount = 1) { this.value += amount; }
  dec(amount = 1) { this.value -= amount; }
  snapshot() { return { type: 'gauge', name: this.name, labels: this.labels, value: this.value }; }
}

class Histogram {
  constructor(name, labels, buckets = DEFAULT_BUCKETS) {
    this.type = 'histogram';
    this.name = name;
    this.labels = labels || {};
    this.buckets = [...buckets, Infinity];
    this.counts = new Array(this.buckets.length).fill(0);
    this.sum = 0;
    this.count = 0;
  }
  observe(value) {
    this.sum += value;
    this.count += 1;
    for (let i = 0; i < this.buckets.length; i++) {
      if (value <= this.buckets[i]) {
        this.counts[i] += 1;
        return;
      }
    }
  }
  snapshot() {
    // Cumulative counts (Prometheus convention).
    const cumulative = [];
    let running = 0;
    for (const c of this.counts) {
      running += c;
      cumulative.push(running);
    }
    return {
      type: 'histogram',
      name: this.name,
      labels: this.labels,
      buckets: this.buckets,
      counts: cumulative,
      sum: this.sum,
      count: this.count,
    };
  }
}

class MetricRegistry {
  constructor() {
    this._counters = new Map();
    this._gauges = new Map();
    this._histograms = new Map();
  }
  _key(name, labels) { return name + '|' + labelsKey(labels); }

  counter(name, labels) {
    const k = this._key(name, labels);
    let m = this._counters.get(k);
    if (!m) { m = new Counter(name, labels); this._counters.set(k, m); }
    return m;
  }
  gauge(name, labels) {
    const k = this._key(name, labels);
    let m = this._gauges.get(k);
    if (!m) { m = new Gauge(name, labels); this._gauges.set(k, m); }
    return m;
  }
  histogram(name, labels, buckets) {
    const k = this._key(name, labels);
    let m = this._histograms.get(k);
    if (!m) { m = new Histogram(name, labels, buckets); this._histograms.set(k, m); }
    return m;
  }

  snapshot() {
    return [
      ...[...this._counters.values()].map(c => c.snapshot()),
      ...[...this._gauges.values()].map(g => g.snapshot()),
      ...[...this._histograms.values()].map(h => h.snapshot()),
    ];
  }

  reset() {
    this._counters.clear();
    this._gauges.clear();
    this._histograms.clear();
  }
}

let _registry = null;
export function getRegistry() {
  if (!_registry) _registry = new MetricRegistry();
  return _registry;
}
export function setRegistry(r) { _registry = r; }

export { Counter, Gauge, Histogram, MetricRegistry, DEFAULT_BUCKETS };
