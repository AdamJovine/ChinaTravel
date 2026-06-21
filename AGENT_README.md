# LISTEN + B&B Agent — System Overview

This document describes the pipeline I built on top of the ChinaTravel benchmark for the TPC@IJCAI 2026 competition.
The agent lives in [`listen/chinatravel_tpc/`](listen/chinatravel_tpc/) (git submodule at `listen/`).

---

## System Diagram

```
╔══════════════════════════════════════════════════════════════════╗
║  TPC Query JSON                                                  ║
║  { start_city, target_city, days, people_number,                ║
║    nature_language, hard_logic_py (ground-truth, hidden) }       ║
╚══════════════════════════╤═══════════════════════════════════════╝
                           │
                           ▼
          ┌────────────────────────────────┐
          │          NL2SL                 │   nl2sl.py
          │                                │
          │  LLM reads nature_language     │
          │  → extracts JSON params        │
          │  → renders DSL snippets        │
          │                                │
          │  + boilerplate snippets        │
          │    (day_count, people_count,   │
          │     tickets, taxi_cars)        │
          └──────────────┬─────────────────┘
                         │  predicted hard_logic_py
                         │  (used for ALL planning below)
                         ▼
          ┌────────────────────────────────┐
          │    Candidate Loading           │   candidates.py
          │    + Pre-filtering             │
          │                                │
          │  load_transport()              │
          │  load_hotels()                 │
          │  load_attractions()            │
          │  load_restaurants()            │
          │                                │
          │  Filters applied:              │
          │  • budget ceiling (attr/rest/  │
          │    hotel/total)                │
          │  • exclude named POIs          │
          │  • exclude cuisine/attr types  │
          │  • hotel feature filter        │
          │  • min_beds filter             │
          │  • hotel proximity to landmark │
          │  • hotel proximity to terminus │
          │    (inner-city budget guard)   │
          │  • name pins (required POIs    │
          │    forced to front of pool)    │
          │  • type/cuisine pins           │
          └──────────────┬─────────────────┘
                         │  filtered DataFrames
                         ▼
          ┌────────────────────────────────┐
          │       LISTEN Ranking           │   agent.py
          │                                │
          │  For each category, run LLM    │
          │  to rank candidates by         │
          │  preference given              │
          │  nature_language:              │
          │                                │
          │  tournament (default):         │
          │   Phase 1 — prelim batches     │
          │     each candidate seen once   │
          │     batch winner → champions   │
          │   Phase 2 — sequential removal │
          │     rank all champions         │
          │                                │
          │  utility (alt):                │
          │   LLM scores all items in      │
          │   one pass                     │
          │                                │
          │  Categories ranked:            │
          │   transport / return_transport │
          │   hotel                        │
          │   attraction                   │
          │   restaurant                   │
          │                                │
          │  (rank cache speeds reruns)    │
          └──────────────┬─────────────────┘
                         │  ranked winner lists
                         │  (pins prepended)
                         ▼
          ┌────────────────────────────────────────────────────┐
          │          Branch-and-Bound Search                    │   bnb_agent.py
          │                                                     │
          │  Phase 1 — Skeleton Feasibility                     │
          │  ─────────────────────────────                      │
          │  Enumerate (transport × hotel × return_transport)   │
          │  triples from LISTEN winners.                       │
          │                                                     │
          │  For each triple: build_itinerary() with no         │
          │  activities, then check_hard_constraints().         │
          │  Keep triples where only ACTIVITY-dependent         │
          │  constraints fail (budget on transport/hotel        │
          │  passes; attr/rest slots will be filled in P2).     │
          │                                                     │
          │  Sort feasible skeletons by meal-slot capacity      │
          │  (early arrivals first) when required restaurants   │
          │  are present.                                       │
          │                                                     │
          │  Phase 2 — B&B Activity Search (per skeleton)       │
          │  ─────────────────────────────────────────────      │
          │  Initial node: top LISTEN-ranked attrs + rests      │
          │                                                     │
          │  Loop (best-first heap, max_nodes budget):          │
          │   1. Pop node with fewest failing constraints       │
          │   2. Generate repair moves:                         │
          │      • inject attr of required missing type         │
          │      • inject rest of required missing cuisine      │
          │      • inject required named restaurant             │
          │      • inject required named attraction             │
          │      • drop most-expensive attrs (budget fail)      │
          │      • drop most-expensive rests (budget fail)      │
          │      • swap expensive taxi legs → metro/walk        │
          │      • reduce attractions (free meal slots)         │
          │      • expand with next LISTEN-ranked item          │
          │   3. Evaluate each move; return on first PASS       │
          │   4. Track best plan seen (fewest failures)         │
          │                                                     │
          │  Repeat for next skeleton if no PASS found.         │
          └──────────────┬─────────────────────────────────────┘
                         │  best plan found
                         ▼
          ┌────────────────────────────────┐
          │      Assemble Itinerary        │   assembler.py
          │      build_itinerary()         │
          │                                │
          │  Lays out day-by-day schedule: │
          │   arrival intercity transport  │
          │   → taxi to hotel              │
          │   → hotel check-in (night 1)   │
          │   → Day 2…N-1: attract + meals │
          │   → final day attractions      │
          │   → return intercity transport │
          │                                │
          │  Handles:                      │
          │   • meal window guards         │
          │   • opening-hour checks        │
          │   • inner-city taxi routing    │
          │   • transport mode overrides   │
          └──────────────┬─────────────────┘
                         │
                         ▼
          ┌────────────────────────────────┐
          │   Constraint Evaluation        │   constraints.py
          │   check_hard_constraints()     │
          │                                │
          │  Runs each ground-truth        │
          │  hard_logic_py snippet against │
          │  the assembled plan.           │
          │                                │
          │  Reports: pass/fail per        │
          │  snippet + diagnostics         │
          │  (actual costs, name sets, …)  │
          └──────────────┬─────────────────┘
                         │
                         ▼
          ╔══════════════════════════════╗
          ║  Output Plan JSON            ║
          ║  { itinerary, uid,           ║
          ║    hard_constraint_pass,     ║
          ║    nl2sl_predicted,          ║
          ║    nl2sl_ground_truth, … }   ║
          ╚══════════════════════════════╝
```

---

## Module Reference

| File | Role |
|---|---|
| [`run_tpc.py`](run_tpc.py) | CLI entry point — single query or batch, parallel workers |
| [`listen/chinatravel_tpc/nl2sl.py`](listen/chinatravel_tpc/nl2sl.py) | NL → DSL translation: LLM extracts JSON params, templates render hard_logic_py snippets |
| [`listen/chinatravel_tpc/agent.py`](listen/chinatravel_tpc/agent.py) | LISTEN ranking (tournament/utility) + greedy solve (`solve()`) |
| [`listen/chinatravel_tpc/bnb_agent.py`](listen/chinatravel_tpc/bnb_agent.py) | Branch-and-bound solver (`solve_bnb()`) — Phase 1 skeleton + Phase 2 activity B&B |
| [`listen/chinatravel_tpc/assembler.py`](listen/chinatravel_tpc/assembler.py) | Builds the day-by-day itinerary dict from skeleton + activity rows |
| [`listen/chinatravel_tpc/candidates.py`](listen/chinatravel_tpc/candidates.py) | Loads transport/hotel/attraction/restaurant DataFrames from the ChinaTravel DB |
| [`listen/chinatravel_tpc/constraints.py`](listen/chinatravel_tpc/constraints.py) | Executes hard_logic_py snippets against a plan; returns pass/fail + diagnostics |
| [`eval_tpc.py`](eval_tpc.py) | Official evaluator — scores a results directory against ground truth |

---

## Two-Stage Design: Why NL2SL First, Then LISTEN+B&B

### Stage 1 — NL2SL

The competition provides each query with a `nature_language` string (free-text traveler requirements) and a hidden `hard_logic_py` list (ground-truth constraint code). The agent cannot see the ground truth during planning.

`nl2sl_translate()` bridges this gap:

1. A single LLM call extracts all constraint parameters into a typed JSON object (budgets, required/excluded POIs, cuisine types, timing constraints, hotel features, etc.).
2. Python templates render each parameter into a `hard_logic_py` DSL snippet — same format the evaluator uses.
3. Four boilerplate snippets (day count, people count, ticket counts, taxi car count) are added without any LLM call.

The predicted `hard_logic_py` then drives all downstream filtering and the B&B search — the ground-truth is **only used for final pass/fail reporting**, never for planning.

### Stage 2 — LISTEN Ranking

LISTEN (LLM-based preference ranking) runs once per candidate category before any search. It uses the raw `nature_language` as the preference prompt, so it captures soft preferences (which attractions sound interesting, which hotel type fits the vibe) that aren't captured in the hard constraint DSL.

The `tournament` algorithm (default) is a two-phase deterministic selection:
- Phase 1: partition all candidates into batches; one LLM call per batch picks the winner
- Phase 2: sequentially rank all batch winners with one LLM call each

This produces a preference-ordered pool that the B&B uses as its search ordering — the most preferred items are tried first.

### Stage 3 — Branch-and-Bound

The greedy `solve()` (in `agent.py`) just takes the top LISTEN picks and assembles them, falling back across a few (transport, hotel, return) combinations. This works for simple queries but fails on complex hard constraints.

`solve_bnb()` (in `bnb_agent.py`) adds systematic constraint repair:

**Phase 1** prunes the (transport, hotel, return_transport) space down to skeletons that pass all constraints that don't depend on which activities are chosen (intercity budget, hotel feature, inner-city taxi estimate, transport type). This is cheap because it calls `build_itinerary()` with empty activity lists.

**Phase 2** does a best-first search over subsets of the LISTEN-ranked activity pool. It starts with the top-ranked attractions and restaurants, checks all constraints, then generates targeted repair moves when constraints fail — injecting required cuisine types, swapping taxi legs to metro, dropping expensive items, etc. The heap is ordered by number of failing constraints, so the search converges toward feasibility efficiently.

---

## Running the Agent

```bash
# Single query
python run_tpc.py \
    --query TPC_IJCAI_2026_phase1_EN/<uid>.json \
    --algo bnb \
    --api-model groq \
    --nl2sl-model openai/gpt-oss-120b \
    --bnb-nodes 60

# Full batch (parallel)
python run_tpc.py \
    --query-dir TPC_IJCAI_2026_phase1_EN/ \
    --output-dir results/ \
    --algo bnb \
    --api-model groq \
    --nl2sl-model openai/gpt-oss-120b \
    --bnb-nodes 60 \
    --bnb-transport 20 \
    --bnb-hotels 20 \
    --workers 4 \
    --rank-cache-dir rank_cache/

# Rerun only failures from a previous run
python run_tpc.py \
    --rerun-failures results/<run_id>/ \
    --algo bnb \
    --api-model groq

# Evaluate results
python eval_tpc.py \
    --method tpc_listen_en \
    --lang en \
    --results-dir results/<run_id>/
```

### Key flags

| Flag | Default | Notes |
|---|---|---|
| `--algo` | `tournament` | `bnb` enables Phase 1+2 search; `tournament`/`utility` are greedy |
| `--nl2sl-model` | `openai/gpt-oss-120b` | Model for NL→DSL extraction (via Groq or OpenAI client) |
| `--api-model` | `groq` | LLM provider for LISTEN ranking (`groq`, `gemini`, `openai`) |
| `--bnb-nodes` | `60` | Node budget per skeleton in Phase 2 |
| `--bnb-transport` | `20` | LISTEN top-k for transport (larger = more skeleton diversity) |
| `--bnb-hotels` | `20` | LISTEN top-k for hotels |
| `--bnb-batches` | `10` | Target prelim batches per LISTEN category (controls LLM calls) |
| `--rank-cache-dir` | `None` | Cache LISTEN rankings by content hash; required for safe parallel runs |
| `--workers` | `1` | Parallel query workers (use with `--rank-cache-dir`) |

---

## Key Design Decisions

**NL2SL uses a single LLM call, not per-category splits.** Many constraints are ambiguous without seeing all field definitions together (`inner_city_budget` vs `intercity_budget`; `required_cuisines` vs `required_cuisines_any` vs `required_dishes`). Splitting by category would also break `or_groups`, which can mix any constraint types.

**Ground truth is never used during planning.** `bnb_agent.py` line 809–820: the predicted `hard_logic_py` is what drives search; `gt_hard_logic` is only read at the very end to compute the final `hard_constraint_pass` field.

**LISTEN ranking runs once before B&B, not inside the search.** Re-ranking inside the search loop would multiply LLM calls. The rank cache (`rank_cache/`) persists rankings across reruns so you can experiment with B&B parameters without paying ranking costs again.

**Phase 1 skeleton check ignores activity-dependent constraint failures.** Attraction/restaurant slots are empty at this stage, so failures on `attraction_name_set`, `restaurant_type_set`, `total_cost`, etc. are expected and skipped. Only failures on skeleton-level constraints (transport type, hotel feature, inner-city taxi estimate) eliminate a skeleton.
