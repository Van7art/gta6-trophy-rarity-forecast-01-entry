# FINAL — "logit-logtime trend, damped"

Winner picked from 3 variants by local testing (V2 base + two changes below).
Python 3.13, standard library only, deterministic, offline — no network,
no model calls.

## Final model formula

Per trophy, over the case history's **distinct** reads (consecutive reads
whose rate for that trophy is unchanged collapse to one point — the
launch-night page was re-read identically many times, so without dedupe a
flat stretch would count as many independent observations):

```
x = ln(1 + hours since release 2026-11-19T00:00:00Z)   (per read's own `at`)
logit(rate) = a + b * x                                  (OLS over distinct reads)
b_shrunk = b * n / (n + 3)                               (n = # distinct reads)
```

Median at target:

```
median_logit = (a + b * x_last)
               + clamp(0.5 * b_shrunk * (x_target - x_last), -3, +3)
```

i.e. the fitted line's value at the last read, extrapolated to the target
with the **shrunk slope, halved**. Shrinkage damps the slope, never the
level: shrinking `b` while keeping the intercept `a` would pivot the line
at x = 0 (release) and drag the median below the last observed rate even
for a rising trophy. The 3-logit cap is applied to the extrapolation term.

Spread:

```
sigma = sqrt((0.5*sqrt(d))^2 + 1.15^2 + resid_var * (1 + leverage))
  d          = horizon in days
  1.15       = refresh-jump allowance, in logit units
  resid_var  = residual variance of the unshrunk fit, SSR / (n - 2)
               (0 when n < 3)
  leverage   = (x_target - x_mean)^2 / Sxx + 1/n
q_tau = 100 / (1 + exp(-(median_logit + z_tau * sigma)))
  z = [-1.645, -0.674, 0, 0.674, 1.645], rounded to 4 decimals half-up
  (same rounding as the starter)
```

Fallback: fewer than 3 distinct reads → persistence (b = 0, median =
last rate), sigma = sqrt((0.5*sqrt(d))^2 + 1.15^2). Special cases: last
rate 100 → all quantiles 100; rates clamped to [0.01, 99.99] before the
logit; null rates skipped per trophy; a trophy with no valid rate anywhere
forecasts from 0.01. Median rate capped at 99.5.

## Two changes vs V2, and why

1. **`JUMP_LOGIT` 0.7 → 1.15.** The launch-night refresh shifted every
   rate ~−1.81 logits by pure dilution (players 638 → 3868, earners
   unchanged). A 0.7-logit jump term prices in only part of such a step;
   1.15 widens the 90% band (q05 sits 1.89 logits below the median
   instead of 1.17) so the band brackets a refresh jump of that size.
   On the practice proxy this moves loss 1.269 → 0.988 and coverage
   0.018 → 1.00.
2. **Damping on the trend extrapolation:
   `delta = 0.5 * b_shrunk * (x_target - x_last)`** (was undamped).
   Logit-linear extrapolation overshoots saturating progression curves:
   trophy earn rates rise fast early then flatten as the easy earners
   finish. Halving the extrapolated move hedges saturation while still
   following the trend upward — the median still rises above the last
   rate for a rising trophy, just by half the fitted move.

## Test results

Scored with `../local/score.py` (pinball loss on the logit scale).

### Practice cases (pure-dilution proxy answers: earners unchanged, players 638 → 3868)

| Case | Loss | Typical miss | Coverage 90% |
|---|---|---|---|
| p00 | **0.987669** | 0.725 pp | 1.00 (55/55) |
| p01 | **0.987669** | 0.725 pp | 1.00 (55/55) |

For comparison: V2 on the same proxy scored 1.269157 with coverage
0.018; the starter (house persistence baseline) scored 1.687705 with
coverage 0.0. Example row, trophy 1 (last rate 0.63%):
`[0.0945, 0.2899, 0.63, 1.3637, 4.0777]`; trophy 2 (100%):
`[100, 100, 100, 100, 100]`.

### Synthetic finals (f00 vs f00.answer.json)

| Scenario | Loss | Typical miss | Coverage 90% |
|---|---|---|---|
| steady | **0.339072** | 1.204 pp | 1.00 |
| fast-dilution | **0.339072** | 1.204 pp | 1.00 |
| slow | **0.339072** | 1.204 pp | 1.00 |

(All three synthetic cases share the same history/answer shape, so the
identical scores are expected — the same forecast against the same
answer.)

### Determinism, runtime, shape

- Determinism: two runs on the practice input into separate dirs,
  `diff -r` clean (byte-identical).
- Runtime: 0.49 s wall for both practice cases in one invocation
  (~0.05 s user time), far under the 8 s cap.
- Output shape: exactly `{"trophies": [...]}`, 55 rows, 5 finite
  non-decreasing numbers in [0, 100] per row; 2,679 bytes per case
  (limit 65,536).

### Kit check

`node cli/makefun.mjs check --repo <entry> --worktree` (run 2026-11-19
~11:50 UTC): **4 passed, 1 failed, 14 skipped, 5 at submission** —
passed: `competition_open`, `snapshot` (7 files, 22,135 bytes incl.
test outputs, since removed), `license_osi` (MIT), `entrypoint`
(python3.13). The single failure is `transcripts_present` (no bundle
supplied — the transcript bundle is made at submit time by the parent
agent). The 5 at-submission checks (repo public, owner, created after
open, commit pushed, duplicate snapshot) can only pass once the repo is
created on GitHub and the entry submitted.

## Files

- `main.py` — the entry (drop-in at a repo root as `main.py`).
- `README.md` — make.fun badges, model summary, run command.
- `LICENSE` — MIT (starter text, SPDX-License-Identifier: MIT).
- `NOTES.md` — this file.
