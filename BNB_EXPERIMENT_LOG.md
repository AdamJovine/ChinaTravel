# BnB Experiment Log — Session 2026-06-17

## Objective

Maximize pass rate on 95 originally-failing queries while keeping 39-passing sample at 100%.
All runs used `--rank-cache rank_cache` (no LLM calls, pure cache replay).

---

## Run Index

### Failing-set runs (95 queries originally failing at baseline)

| Commit | Run dir | Pass | Total | Rate | Notes |
|--------|---------|------|-------|------|-------|
| `6dfbe0a` | `results/failures_at_6dfbe0a/run_20260617_192902_bnb_groq` | 70 | 86 | 81% | Baseline (only 86 ran; 9 had errors/timeouts) |
| `04e2793` | `results/failures_at_04e2793/run_20260617_211323_bnb_groq` | 77 | 94 | 82% | After unuse_restaurant fix |
| `094f087` | `results/failures_at_094f087/run_20260617_213504_bnb_groq` | 80 | 95 | 84% | After poi_ordering fix |

### Passing-sample runs (39 queries that pass at baseline — regression guard)

| Commit | Run dir | Pass | Total | Rate |
|--------|---------|------|-------|------|
| `6dfbe0a` | `results/passing_sample_at_6dfbe0a/run_20260617_193914_bnb_groq` | 39 | 39 | 100% |
| `04e2793` | `results/passing_sample_assembler_fix/run_20260617_205749_bnb_groq` | 39 | 39 | 100% |
| `094f087` | `results/passing_sample_poiorder_fix/run_20260617_213300_bnb_groq` | 39 | 39 | 100% |

### Full-1000 runs

| Run dir | Pass | Total | Rate | Notes |
|---------|------|-------|------|-------|
| `results/FULL1000_BNB_MAX/run_20260616_184445_bnb_groq` | 905 | 1000 | 90.5% | Full dataset before this session's fixes |
| `results/run_20260617_180524_bnb_groq` | 132 | 152 | 86.8% | Partial run during session |

---

## Changes and Impact

### `04e2793` — assembler: unuse_restaurant + opentime check

**+1% on failing set** (70→77/94, 81%→82%)

**Root cause:** `next_restaurant()` marks a restaurant as "used" before `_meal_activity()` checks
if it can actually fit. If a restaurant (e.g. Chef's Wife, opens 11:00) was selected for a
lunch slot where arrival was after 14:00 (the lunch window close), it was permanently consumed
and unavailable for dinner.

**Fix 1:** Skip restaurants where `opentime >= meal_window_close` in `next_restaurant()`.
Added `_MEAL_WIN_CLOSE = {"breakfast": "09:00", "lunch": "14:00", "dinner": "20:00"}` dict
and pre-check in all three loop paths (priority, pool order, fallback).

**Fix 2:** Added `unuse_restaurant()` closure after `next_restaurant()` that returns a
restaurant to the pool (by object identity) when `_meal_activity()` or `_can_afford()` rejects
it. Added `else: unuse_restaurant(rest)` at all 6 meal-slot call sites in assembler.py.

**File:** `chinatravel_tpc/assembler.py`

---

### `094f087` — poi_ordering: ordered pin list + attr_pins re-sort

**+2% on failing set** (77→80/95, 82%→84%)

**Root cause 1:** `_parse_pinned_names()` used Python `set`, losing insertion order. For queries
requiring "Manchester United Dream Theater then Houhai", the set could return Houhai first,
prepending it before Manchester United in `attr_pins` and causing the poi_ordering constraint
to fail.

**Fix 1:** Changed `_parse_pinned_names()` to use ordered `list` (preserving insertion order
from nl2sl literals). `pins.setdefault(cat, [])` with dedup check.

**Fix 2:** Added explicit poi_ordering-aware `attr_pins` re-sort in `bnb_agent.py`. Parses
`idx_activity` patterns from `hard_logic_py` snippets to extract (before_name, after_name)
pairs, then swaps `attr_pins` entries that are in the wrong order.

**Files:** `chinatravel_tpc/agent.py`, `chinatravel_tpc/bnb_agent.py`

---

### `ab8f858` — agent: normalize interior whitespace in pin names

**Impact not yet measured** (committed 2026-06-17, run pending)

**Root cause:** Some DB hotel names have double spaces (e.g., "Atour X Hotel Shanghai  Hongqiao
Airport Konggang Road") but nl2sl predicts single space. `_normalize_pin_name` only handled
parenthesis spacing.

**Fix:** Added `re.sub(r'\s+', ' ', name)` before the parenthesis normalization to collapse
all interior whitespace.

**Expected impact:** Resolves 1 of the 3 `required_hotel_name` failures (UID 20250322205724212077,
Atour X Hotel case). The other two hotel cases appear to be a different bug where the pinned
hotel is found by `_rows_for_pins` but never selected in the BnB skeleton phase.

**File:** `chinatravel_tpc/agent.py`

---

## Remaining 15 Failures at `094f087`

Run: `results/failures_at_094f087/run_20260617_213504_bnb_groq`

| UID | Failure type | Notes |
|-----|-------------|-------|
| 20250321040138918100 | `other` | Unknown constraint |
| 20250321114239878527 | `required_attraction_type` | |
| 20250322130400129262 | `required_hotel_feature` | |
| 20250322142408120417 | `OR_compound` | Hangzhou 4-day, sightseeing ≤200 OR single bed |
| 20250322161842269069 | `required_cuisine_type` | Japanese cuisine at airport hotel area |
| 20250322164349699070 | `required_restaurant` | |
| 20250322165301153800 | `required_cuisine_type` | Japanese cuisine at airport hotel area |
| 20250322171831366188 | `other` | |
| 20250322192555845893 | `poi_timing` | Peppa Pig 12:30-14:00 window |
| 20250322194001155137 | `required_hotel_name` | "Shanghai Lujiazui Babaiban Lan'ou Hotel" — pin found but skeleton ignores it |
| 20250322205724212077 | `required_hotel_name` | "Atour X Hotel Shanghai Hongqiao" — double-space mismatch (fixed in ab8f858) |
| 20250323010327713880 | `poi_timing` | Bistro Sola 17:00-18:00 window |
| 20250323031255302334 | `total_budget`, `poi_timing` | Blue Airflow Skydiving 14:20-15:50 window |
| 20250324082922869744 | `required_attraction` | |
| 20250324223916776179 | `required_hotel_name` | Third hotel pin case |

### Open investigations

**Hotel pin not selected in skeleton (2 cases: 20250322194001155137, 20250324223916776179):**
- `_rows_for_pins(hotel_df_full, ...)` finds the hotel (verified)
- `_prepend_pins(hotel_pins, hotel_winners)` puts it first in hotel_winners
- But skeleton output shows only other hotels (Waiting Hotel, Jinglai Hotel)
- Likely cause: `_find_feasible_skeletons` evaluates hotel against `hard_logic` and the
  hotel fails some constraint (proximity? budget?), or `hotel_winners` is being re-sorted
  after `_prepend_pins` runs
- Next step: add debug print inside `_find_feasible_skeletons` to see why pinned hotel is
  rejected in the skeleton phase

**poi_timing (3 cases):**
- Assembler visits attraction outside its open window
- Need to check if `min_visit_duration` is consuming too much time before the timed attraction

**required_cuisine_type (2 cases):**
- Japanese restaurants near airport hotel areas not being selected
- May be a rank cache issue (ranked low) or proximity filter excluding them

---

## What Remains To Do

1. **Run failing set at `ab8f858`** to confirm double-space fix resolves UID 20250322205724212077
2. **Investigate hotel pin skeleton bypass** — why do pinned hotels get prepended to
   `hotel_winners` but never appear in feasible skeletons (UIDs 20250322194001155137,
   20250324223916776179)
3. **Investigate poi_timing failures** (3 cases) — assembler timing logic
4. **Investigate required_cuisine_type failures** (2 cases) — Japanese cuisine availability
5. **Run full 1000 at latest commit** after fixes accumulate to get updated full-dataset score
