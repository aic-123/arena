# Arena

**A multi-party structured discussion system built on top of Scaffold.**
**建立在 Scaffold 之上的多人结构化讨论系统。**

[中文](README.md) · [What it is *not*](#what-it-is-not) · [Spec & constraints: `spec/`](spec/)

The AI is a **Secretary / Organizer / Detector** — **not a Judge** (`§C1`).

> Codes like `§C9 #5` or `§T4.2` throughout the source point at the constraint documents
> in [`spec/`](spec/) — **every one of them resolves**. Those documents are the spec this
> system was built against, and they ship with the repo so you don't have to go hunting.
> (They are written in Chinese; the code, tests, and this README are the English surface.)

## What it is *not*

Starting with what it doesn't do, because that explains the system better than a feature list:

- **Not an AI debate platform.** The AI does not generate arguments, does not evaluate them,
  does not pick a winner. It does exactly three things: **split**, **organize**, **surface disagreement**.
- **Not a vote-to-decide tool.** Votes record **public preference**, not truth. There is
  **no operator** connecting the four quantities that could produce "who is more correct".
- **Not a finished product.** It is a **chain that runs end to end**, not a service —
  no frontend, no accounts, no deployment, no database server.
- **No ranking axis, no thresholds, no scores.** These aren't "not done yet", they are
  **forbidden** (`§C7.1` `§C9` #5 #7), and 13 executable checks enforce that.
- **No upper-layer summarization, no auto-split, no auto-merge.** Dormant for this entire phase (`§C7.1`).

## Step 0: get the code

```bash
git clone https://github.com/aic-123/arena.git
cd arena
```

**Zero third-party dependencies — no `pip install`.** Just Python 3 (tested on 3.13 and 3.14).

## Using it (the human-facing side)

```bash
python cli.py new "Should overtime be paid?" alice
python cli.py submit debate-0001 alice "Work intensity is too high now, so nobody wants to climb the ladder."
python cli.py confirm debate-0001 draft-0001 alice   # asks one at a time
python cli.py view debate-0001
python cli.py chart topic-0001     # vote chart (terminal Braille line plot)
python cli.py observe
```

The DB defaults to `arena.db` (override with `ARENA_DB`). Bare `python cli.py` prints usage.

The confirmation step asks **one proposition at a time**: `y` = not distorted / `n` = distorted /
`q` = don't submit / Enter = no answer. **Enter does not mean agree** — if some are unanswered,
nothing is written at all. That's not friction; it's the step `§C5` names: this is where a user
first sees "my one sentence actually contains three propositions".

## Running the checks

```bash
python test_arena.py       # structure and invariants
python test_cli.py         # the entry point (real subprocess + real stdin + real DB)
python test_checks.py      # proves checks.py isn't vacuous (injects real violations, sees if it fires)
python checks.py           # the falsification checks from the declaration (exit 0 = all hold)
python concurrency.py run 4 6   # §T2 step 10: 4 processes, no coordination, same state
python samples/align.py    # §C5.5.1 samples: map labelled nodes back to source text
python samples/test_align.py  # the check for that check ("deleted" and "changed" aren't the same thing)
```

`concurrency.py run` now reports **0 exceptions** — with the same parameters that used to
collide every single time before step 13. But it **prints handoff count before exception count**:
if handoffs are 0, that "not exposed" claim has no denominator and isn't evidence.

Zero third-party dependencies. Python 3 only. CI runs every check on `ubuntu-latest` and `windows-latest`.

## Where this stands

Following `§T2` in order, **no skipping steps**.

| Step | Status |
|---|---|
| 01 Read samples | Done — but all three source documents contain exactly **1** sample (`§C5`), see `samples/` |
| 02 Find the minimal Artifact | Done |
| 03 Build the minimal Scaffold | Done |
| 04 Run one Debate end to end | Done |
| 05 Add semantic confirmation | Done |
| 06 Instrument observation points | Done — six registered, **not one threshold set** (`§C7.2`) |
| 06b Sub-question hints | Done — `§C2.1`. **Not one of the 14 steps**; added by the requester on site |
| 07 Add Claim/Evidence/Challenge | Done — all four `§C4` relation mappings landed; `§C6.1` enforced on the write path |
| 08 Add Revision | Done — lineage visible (`history_of`). **`merge` cannot be expressed**, see DECLARATION §6.0.1 |
| 09 Add Vote | Done — four quantities kept separate, old votes grouped by question version. **"Main dispute" deliberately not built** |
| 10 Two users at once | Done — `concurrency.py`: N **real processes**, no coordination, product API only |
| 11 Record the first real concurrency bug | Done — **it fired**: id collision |
| 12 Introduce a mature fix | Done — let SQLite allocate atomically (`next_seq`), **not by adding a lock** |
| 13/14 Fix it / run again | Done — the colliding parameters went from 1/4/10 collisions to 0; pushed to 8 processes × 12 rounds, still 0 |
| 15 Five ontology gaps exposed by the label file | Done — all five concluded. Also **not one of the 14 steps** |

Steps 10–14 are done, but **two things are unresolved — don't read that as "concurrency is fine now"**:

1. **The "allocate a number, then you must commit" contract is not enforced.** The person who
   wrote the doc tripped over it ten minutes later. Today it relies on every call site remembering to commit.
2. **Concurrency was only tested under the single-file SQLite assumption.** None of the components
   `§C11.3` forbids were introduced, so it has never been exercised multi-machine or multi-writer —
   and it isn't meant to be in this phase.

**The entry point (`cli.py`) was added on 2026-09-25, not as a `§T2` step.** Before it, using this
required writing Python — that's *having no product surface*, not "no frontend yet".

**One thing is still not connected** — don't read "it runs" as "the product is complete":

- **The "cast a vote" half is not wired up (`§C12`).** It needs a `vote_context()` captured at render
  time, which needs a voting page — how options are presented to a human is a product decision,
  not an implementation detail. **The "see" half is wired up**: `cli.py chart <topic_id>` draws the
  vote line plot.

**Two things were decided on 2026-09-25 and are worth knowing:**

- **The write paths were unified.** The old `add_position` path (no splitting, no per-item
  confirmation, storing only `{text, raw_text}`) is **deleted**. Content can only be written through
  `confirm.propose()` → `confirm.confirm(reviewed=[…])` → `open_topic()` / `add_to_topic()`.
- **The overrule-rate's scope and its reading were both settled.** `contains` edges — structural
  membership the user has no action to reject — are **excluded from the denominator** (numerator and
  denominator use the *same* criterion). And since "reject an edge" is deliberately **not** given a
  UI, the numerator is structurally 0 — so it prints **"cannot be computed — no action can produce
  the numerator"**, never `0.0000`. The rule: **if a number's "0" and its "absent" are
  indistinguishable, it must not be printed as 0.**

## Files

| File | What it is |
|---|---|
| `DECLARATION.md` | The selection declaration (`§T4.2`) — every choice together with its falsification check |
| `cli.py` | **The human-facing side.** Thin: only calls existing actions, adds no write path |
| `scaffold.py` | **Scaffold layer**: Artifact / Relation / Revision. Knows nothing about users, votes, or disagreement |
| `debate.py` | **Arena layer**: discussion behaviour. One-way dependency, `uses` Scaffold |
| `segment.py` | Deterministic rule-based splitter. **No semantic rewriting** — proposition text is always a verbatim slice |
| `confirm.py` | Semantic confirmation (`§C5`). Confirms **whether the meaning was distorted**, not which spans enter the structure |
| `observe.py` | Observation points (`§C7.2`). **Records how to compute, never what counts as "high"**. Derived view, read-only |
| `hints.py` | `§C2.1` sub-question hints. **Discovers, never proposes** — points back at questions the user already wrote |
| `vote.py` | Voting (`§C12`). Four quantities kept separate, **never combined by any operator**. `chart()` is a read-only derived view |
| `concurrency.py` | The step-10 apparatus. N real processes, **no coordination**, product API only (B9 watches this) |
| `checks.py` | The executable falsification checks B1–B13 (`§T4.2`) |
| `test_arena.py` | Structure and invariant tests |
| `test_cli.py` | Entry-point tests. **Exercises the product surface**: subprocess + real stdin, never imports the library to "simulate" a user |
| `test_checks.py` | Injection-based verification of `checks.py` — **proves it isn't vacuous** |
| `samples/` | `§C5.5.1` splitting-quality samples. `proposed_count` 20/20, `boundaries` **4/20** |
| `spec/` | **What this system was built against** — identity, task card, constraint appendix, original design draft. Every `§` code points here |
| `.github/workflows/ci.yml` | CI: two platforms × two Python versions, all checks |
| `LICENSE` | Apache-2.0 |

## Three constraints that don't rely on good intentions

They live in the storage layer. There is no second write path:

- **`§C3.2`** A Relation is a traceable object — its own table, an identity, an origin, rejectable,
  and **not deleted after rejection** (otherwise the overrule rate can't be computed)
- **`§C10`** No overwriting updates — not one line of an old version changes; concurrent edits fork
  naturally and **the system does not pick a branch for you**
- **`§C9` #3** A machine may not self-grant `verified` — one path only, `grant_verified(hook=...)`,
  and it must name a hook

## What this repo deliberately does not do

No ranking, no scoring, no locking, no thresholds, no upper-layer summarization.
See `DECLARATION.md` §4 — these are not omissions, they are required by `§C11.2` `§C7.1` `§C7.2`.

**There are exactly three permitted derived views.** All read-only, none writes back, none creates objects:

- Ratios and counts in `observe.py` — "cannot be computed" has **two** distinct sources, and the
  line says which: **denominator is 0** (nothing recorded yet) vs. **the numerator cannot be produced**
  (no action in the product can produce it — that 0 means "unmeasurable", not "zero")
- Sub-question hints in `hints.py` — points back at positions in the user's own text,
  **generates no questions** (`§C2.0`)
- The `chart()` line plot in `vote.py` — plots only the accumulation of **existing votes**;
  no ranking, no thresholds, **never across question versions** (old votes belong to old questions, `§C12.5`)

---

Licensed under Apache-2.0. See [LICENSE](LICENSE).
