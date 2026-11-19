"""gta6-trophy-rarity-forecast-01 -- FINAL ("logit-logtime trend, damped").

Per trophy, fit logit(rate) = a + b * ln(1 + hours since release
2026-11-19T00:00:00Z) by ordinary least squares over the case history's
DISTINCT reads (consecutive reads with an unchanged rate for that trophy
collapse to one point; each point uses its own read's `at`).

  * Slope shrinkage: b_shrunk = b * n / (n + 3), n = # distinct reads.
    With fewer than 3 distinct reads fall back to persistence
    (b = 0, a = logit(last rate)).
  * Median at target: fitted value at x_target = ln(1 + target hours),
    anchored at the fitted value at the last read and extrapolated with
    the shrunk slope, 0.5-DAMPED and capped at 3.0 logits:
    median_logit = (a + b * x_last)
                   + clamp(0.5 * b_shrunk * (x_target - x_last), -3, 3).
    Median rate clamped to [0.01, 99.5]. The 0.5 damping hedges
    saturation: logit-linear extrapolation overshoots saturating
    progression curves, so halving the extrapolated move follows the
    trend upward while guarding against runaway.
  * Spread: sigma = sqrt((0.5*sqrt(d))^2 + 1.15^2
                         + resid_var * (1 + leverage)),
    d = horizon in days, 1.15 logit = refresh-jump allowance
    (the launch-night refresh shifted every rate ~-1.81 logits by
    pure dilution, so the band must be wide enough to bracket such
    jumps), resid_var = residual variance of the (unshrunk) logit fit
    (0 when < 3 distinct reads),
    leverage = (x_target - x_mean)^2 / Sxx + 1/n.
  * Quantiles: q_tau = 100 / (1 + exp(-(median_logit + z_tau * sigma))),
    z = [-1.645, -0.674, 0, 0.674, 1.645], rounded to 4 decimals half-up
    (same rounding as the starter).
  * Special case: trophy whose last rate is 100 -> all quantiles 100.
  * Rates clamped to [0.01, 99.99] before the logit. Null rates are
    skipped for that trophy; a trophy with no valid rate anywhere
    forecasts from 0.01.

Standard library only, deterministic, no network, no randomness.
Run: python3 -P -s /bot/main.py (reads $MAKEFUN_INPUT_DIR / $MAKEFUN_OUTPUT_DIR,
else /input / /output).
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]

RELEASE = datetime(2026, 11, 19, 0, 0, 0, tzinfo=timezone.utc)

# --- FINAL constants (see NOTES.md for the reasoning) ---
SHRINK_PRIOR = 3.0      # b_shrunk = b * n / (n + 3)
JUMP_LOGIT = 1.15        # refresh-jump allowance, in logit units
RW_SIGMA = 0.5           # random-walk spread per sqrt(day), as the starter
MAX_EXTRAP_LOGIT = 3.0   # cap on |0.5 * b_shrunk * (x_target - x_last)|
TREND_DAMPING = 0.5      # halved trend extrapolation (saturation hedge)
MEDIAN_CAP = 99.5        # median may not exceed this (percent)
MIN_DISTINCT_FOR_FIT = 3


def clamp(x: float, lo: float = 0.01, hi: float = 99.99) -> float:
    return min(hi, max(lo, x))


def logit(pct: float) -> float:
    p = clamp(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(lg: float) -> float:
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x: float):
    """Round to 4 decimals, half up, as JavaScript Math.round does."""
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value == int(value) else value


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def x_of(at: datetime) -> float:
    """ln(1 + hours since release); guarded so the log argument stays > 0."""
    hours = (at - RELEASE).total_seconds() / 3600.0
    return math.log(max(1.0 + hours, 0.05))


def forecast_trophy(rates, times, x_target, horizon_days):
    """rates/times: per-read (rate or None, read time), oldest first.

    Returns the five quantiles for one trophy.
    """
    last_rate = None
    for r in rates:
        if r is not None:
            last_rate = r
    if last_rate is not None and last_rate >= 100:
        return [100, 100, 100, 100, 100]
    if last_rate is None:
        last_rate = 0.01

    # Distinct reads: collapse consecutive reads whose rate is unchanged
    # for this trophy (identical page re-reads carry no new information).
    pts = []  # (x, logit(rate))
    prev_rate = None
    for r, t in zip(rates, times):
        if r is None:
            continue
        if prev_rate is not None and r == prev_rate:
            continue
        prev_rate = r
        pts.append((x_of(t), logit(r)))
    n = len(pts)

    if n < MIN_DISTINCT_FOR_FIT:
        # Persistence fallback: flat median at the last rate.
        med_logit = logit(last_rate)
        sigma = math.sqrt((RW_SIGMA * math.sqrt(horizon_days)) ** 2
                          + JUMP_LOGIT ** 2)
    else:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        sxx = sum((x - x_mean) ** 2 for x in xs)
        if sxx <= 0:
            med_logit = logit(last_rate)
            sigma = math.sqrt((RW_SIGMA * math.sqrt(horizon_days)) ** 2
                              + JUMP_LOGIT ** 2)
        else:
            sxy = sum((x - x_mean) * (y - y_mean) for x, y in pts)
            b = sxy / sxx
            a = y_mean - b * x_mean
            b_shrunk = b * n / (n + SHRINK_PRIOR)
            # Residual variance of the unshrunk fit (n - 2 dof).
            ssr = sum((y - (a + b * x)) ** 2 for x, y in pts)
            resid_var = ssr / (n - 2) if n > 2 else 0.0
            x_last = xs[-1]
            # Median: the fitted line's value at the last read, extrapolated
            # to the target with the SHRUNK slope, 0.5-DAMPED (saturation
            # hedge: logit-linear extrapolation overshoots saturating
            # progression curves). Shrinkage damps the slope only; it must
            # not drag the level down by pivoting the line at x = 0.
            # The extrapolation is capped at 3 logits.
            delta = TREND_DAMPING * b_shrunk * (x_target - x_last)
            delta = max(-MAX_EXTRAP_LOGIT, min(MAX_EXTRAP_LOGIT, delta))
            med_logit = (a + b * x_last) + delta
            leverage = (x_target - x_mean) ** 2 / sxx + 1.0 / n
            sigma = math.sqrt(
                (RW_SIGMA * math.sqrt(horizon_days)) ** 2
                + JUMP_LOGIT ** 2
                + resid_var * (1.0 + leverage)
            )

    med_rate = min(MEDIAN_CAP, max(0.01, inv_logit(med_logit)))
    med_logit = logit(med_rate)
    qs = [round4(clamp(inv_logit(med_logit + z * sigma), 0.0, 100.0))
          for z in Z]
    # Rounding can only preserve order (z increasing, sigma >= 0), but
    # enforce non-decreasing defensively.
    for i in range(1, 5):
        if qs[i] < qs[i - 1]:
            qs[i] = qs[i - 1]
    return qs


def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError(f"{name} has no history")
    target = parse_time(case["target"])
    x_target = x_of(target)
    horizon_days = case["horizon_minutes"] / 1440.0
    times = [parse_time(h["at"]) for h in case["history"]]
    rows = []
    for i, trophy in enumerate(case["trophies"]):
        rates = [h["rates"][i] for h in case["history"]]
        rows.append({"id": trophy["id"],
                     "q": forecast_trophy(rates, times, x_target,
                                          horizon_days)})
    return {"trophies": rows}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n",
            encoding="utf-8")


if __name__ == "__main__":
    main()
