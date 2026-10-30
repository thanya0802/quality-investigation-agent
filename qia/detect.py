"""Emerging-issue detection, shared by the agent tool and the real-data backtest.

Real NHTSA volumes are low (often 0-3 complaints/week per model+component), so a single week is
too noisy. Instead: the sum of the last WINDOW_WEEKS weeks is compared with what the previous
BASELINE_WEEKS weeks predict, using the MEDIAN and MAD (robust: an earlier spike in the baseline
doesn't hide a new one), with a Poisson floor so near-zero baselines don't produce huge z-scores.
An engineer can recompute every number by hand."""
import math
import statistics
from . import config

def _robust(base):
    med = statistics.median(base)
    mad = statistics.median([abs(x - med) for x in base]) * 1.4826
    return med, max(mad, math.sqrt(max(med, 1.0)))

def window_flags(counts, window=None, baseline=None, z_thr=None, min_count=None, min_ratio=None):
    """Return [(end_index, window_sum, expected, z)] for every week where the window is flagged."""
    W = window or config.WINDOW_WEEKS
    B = baseline or config.BASELINE_WEEKS
    zt, mc, mr = z_thr or config.Z_THRESHOLD, min_count or config.MIN_WINDOW_COUNT, min_ratio or config.MIN_RATIO
    out = []
    for i in range(W + B - 1, len(counts)):
        base = counts[i - W - B + 1: i - W + 1]
        med, sd = _robust(base)
        s = sum(counts[i - W + 1: i + 1])
        expected = W * med
        z = (s - expected) / (math.sqrt(W) * sd)
        if s >= mc and z >= zt and s >= mr * max(expected, 1):
            out.append((i, s, round(expected, 1), round(z, 1)))
    return out

def onset_index(counts, end_index, window=None, baseline=None):
    """First week inside the first flagged window whose count clearly exceeds the baseline."""
    W, B = window or config.WINDOW_WEEKS, baseline or config.BASELINE_WEEKS
    med, sd = _robust(counts[max(0, end_index - W - B + 1): end_index - W + 1])
    for j in range(end_index - W + 1, end_index + 1):
        if counts[j] > med + 2 * sd:
            return j
    return end_index - W + 1
