"""The examples study's sampler, vendored verbatim for sampled steering-diff runs: the serve's own _pick (host.py) at
Glimmer's vendor settings, seeded. Source: glimmer-emotions examples/sampler.py at 8037a03 (the run that recorded the
known lies), minus its host.py source loader. A test pins these draws to the original's.

_pick draws with the legacy global generator, np.random.choice. A RandomState seeded with the same value draws the same
uniforms, so for the same logits and the same probability vector this sampler picks exactly what a seeded _pick picks.
The vector is built as _pick builds it. The fast path sorts only the top-k candidates. When two candidates tie exactly
it rebuilds the vector with _pick's own full-array steps, because only those reproduce its tie order at the top-p cut."""
import hashlib

import numpy as np


def seed_for(family, item, k):
    """One seed per (family, item, sample), shared by every arm."""
    return int.from_bytes(hashlib.sha256(f"examples:{family}:{item}:{k}".encode()).digest()[:4], "big")


class Sampler:
    def __init__(self, seed, temperature=1.0, top_p=0.95, top_k=64):
        if temperature <= 0:
            raise ValueError("the examples sample; greedy is not a setting here")
        self.seed, self.temperature, self.top_p, self.top_k = int(seed), float(temperature), float(top_p), int(top_k)
        self.rng = np.random.RandomState(self.seed)

    def _exact(self, p):
        """_pick's own top-k and top-p steps over the full vector (its source, minus the draw)."""
        if self.top_k and 0 < self.top_k < p.size:
            thresh = np.partition(p, -self.top_k)[-self.top_k]
            p[p < thresh] = 0.0
        if 0.0 < self.top_p < 1.0:
            order = np.argsort(p)[::-1]
            cum = np.cumsum(p[order])
            cut = int(np.searchsorted(cum, self.top_p)) + 1
            keep = np.zeros(p.size, dtype=bool)
            keep[order[:cut]] = True
            p[~keep] = 0.0
        return p

    def probs(self, logits):
        """The vector _pick draws from: float64 over the whole vocabulary, normalized as _pick normalizes it. None when
        _pick would fall back to argmax (nothing kept)."""
        z = np.asarray(logits).astype(np.float64) / self.temperature
        z -= z.max()
        p = np.exp(z)
        p /= p.sum()
        if self.top_k and 0 < self.top_k < p.size:
            cand = np.flatnonzero(p >= np.partition(p, -self.top_k)[-self.top_k])
        else:
            cand = np.arange(p.size)
        if np.unique(p[cand]).size != cand.size:          # an exact tie: only _pick's own order is _pick's order
            p = self._exact(p)
        else:
            if 0.0 < self.top_p < 1.0:
                order = cand[np.argsort(p[cand])[::-1]]
                cut = int(np.searchsorted(np.cumsum(p[order]), self.top_p)) + 1
                cand = order[:cut]
            q = np.zeros_like(p)
            q[cand] = p[cand]
            p = q
        s = p.sum()                                        # over the full vector, as _pick sums it
        if s <= 0.0:
            return None
        p /= s
        return p

    def __call__(self, logits):
        p = self.probs(logits)
        if p is None:
            return int(np.argmax(logits))
        return int(self.rng.choice(p.size, p=p))
