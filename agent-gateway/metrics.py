"""Lightweight in-process metrics registry.

Provides simple counter and gauge primitives and a Prometheus-compatible
text-format exporter.  Does NOT require the prometheus_client library —
the exporter writes the standard text exposition format directly.

Usage:
  from metrics import inc, gauge, export_prometheus

  inc('tool_calls_total', labels={'tool': 'browser.summarize'})
  gauge('worker_pool_alive', 2)
  text = export_prometheus()
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

_lock = threading.Lock()

# counters[name][label_tuple] = float
_counters: Dict[str, Dict[Tuple, float]] = defaultdict(lambda: defaultdict(float))
# gauges[name][label_tuple] = float
_gauges: Dict[str, Dict[Tuple, float]] = defaultdict(lambda: defaultdict(float))
# histograms[name][label_tuple] = [sum, count, list[float]]
_histograms: Dict[str, Dict[Tuple, list]] = defaultdict(lambda: defaultdict(lambda: [0.0, 0, []]))

_start_time = time.time()


def _labels_to_tuple(labels: Optional[Dict[str, str]]) -> Tuple:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def inc(name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None):
    """Increment a counter."""
    with _lock:
        _counters[name][_labels_to_tuple(labels)] += value


def gauge(name: str, value: float, labels: Optional[Dict[str, str]] = None):
    """Set a gauge value."""
    with _lock:
        _gauges[name][_labels_to_tuple(labels)] = value


def observe(name: str, value: float, labels: Optional[Dict[str, str]] = None):
    """Record a histogram observation."""
    with _lock:
        bucket = _histograms[name][_labels_to_tuple(labels)]
        bucket[0] += value   # sum
        bucket[1] += 1       # count
        bucket[2].append(value)


def get_counter(name: str, labels: Optional[Dict[str, str]] = None) -> float:
    with _lock:
        return _counters[name][_labels_to_tuple(labels)]


def get_gauge(name: str, labels: Optional[Dict[str, str]] = None) -> float:
    with _lock:
        return _gauges[name][_labels_to_tuple(labels)]


def _fmt_labels(label_tuple: Tuple) -> str:
    if not label_tuple:
        return ''
    parts = [f'{k}="{v}"' for k, v in label_tuple]
    return '{' + ','.join(parts) + '}'


def export_prometheus() -> str:
    """Return Prometheus text exposition format string."""
    lines = []
    with _lock:
        # uptime
        lines.append('# HELP process_uptime_seconds Seconds since gateway started')
        lines.append('# TYPE process_uptime_seconds gauge')
        lines.append(f'process_uptime_seconds {time.time() - _start_time:.3f}')

        for name, buckets in _counters.items():
            lines.append(f'# HELP {name} Counter')
            lines.append(f'# TYPE {name} counter')
            for lbl, val in buckets.items():
                lines.append(f'{name}{_fmt_labels(lbl)} {val}')

        for name, buckets in _gauges.items():
            lines.append(f'# HELP {name} Gauge')
            lines.append(f'# TYPE {name} gauge')
            for lbl, val in buckets.items():
                lines.append(f'{name}{_fmt_labels(lbl)} {val}')

        for name, buckets in _histograms.items():
            lines.append(f'# HELP {name} Histogram')
            lines.append(f'# TYPE {name} histogram')
            for lbl, (s, c, _) in buckets.items():
                lines.append(f'{name}_sum{_fmt_labels(lbl)} {s}')
                lines.append(f'{name}_count{_fmt_labels(lbl)} {c}')
    return '\n'.join(lines) + '\n'


def reset():
    """Clear all metrics (useful in tests)."""
    with _lock:
        _counters.clear()
        _gauges.clear()
        _histograms.clear()
