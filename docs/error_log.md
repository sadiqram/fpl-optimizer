# Error log

Real bugs and false assumptions caught while building `fpl-optimizer`, and how each was
found and fixed. Kept as a running log, not a postmortem-per-incident doc — most of these
were caught by actually running code against live data (the real FPL API, the real vaastav
archive, real understat.com), not by inspection alone. That pattern is worth preserving:
several of these would have shipped silently if the fix had stopped at "the code looks
right" instead of "the code produces correct output against real data."

Entries are in the order they were found. Commit references point to where the fix landed.

---

## 1. Real `.env` value pasted into the tracked `.env.example` template

**When:** M0, right after adding the FPL team ID.
**Found by:** Reviewing the diff before committing (`.env.example` showed as modified when
only `.env` should have changed).
**What happened:** The team ID ended up in both `.env` (correct, gitignored) and
`.env.example` (tracked — would have been committed and pushed to a public repo).
**Fix:** Blanked the value back out in `.env.example` before staging. No code change
needed, just discipline about checking `git status`/diffs before every commit — which is
exactly what `.env.example` vs `.env` was designed to make a non-event (Architecture §5),
and did, because the check happened.

---

## 2. `players.id` keyed on FPL's season-specific element id, not the stable `code`

**When:** Building `archive_loader.py`, before writing any historical-season parsing code.
**Found by:** Deliberately checking whether FPL's `id` field is stable across seasons
before trusting it as a join key — it wasn't an accident catch, it was verified on purpose
because the archive ingestion design depended on the answer.
**What happened:** `SELECT id FROM players_raw.csv` for Mohamed Salah (`code=118748`)
across four seasons: `id` was 191, 233, 308, 328. FPL reassigns element ids every season.
The original schema (`players.id = FPL element id`) would have silently merged unrelated
players' historical stats onto whoever currently holds a given id number.
**Fix:** `players.id` is now FPL's `code` (genuinely stable). `element_id` holds the
current live season's id separately, used only for API calls. Commit `a293123`.
**Second-order version of the same bug, caught before it ran:** the first version of the
fix still let an archive season's stale `element_id` overwrite the correct live one via
`ON CONFLICT ... COALESCE(excluded.element_id, players.element_id)` — COALESCE prefers the
*new* value when non-null, and archive rows had a non-null (but wrong) `element_id`. Added
an explicit `is_live` flag to `upsert_players`; archive callers pass `is_live=False`, which
forces `element_id` to `None` in the write regardless of what the archive row says. Caught
by tracing through the COALESCE logic before running it, then locked down with
`test_archive_upsert_never_clobbers_live_element_id`.

---

## 3. `fixtures.id` and `player_gw_stats.gameweek` collide across seasons

**When:** Same pass as #2, while designing the archive bootstrap's write path.
**Found by:** Checking whether fixture ids are unique before assuming a single-column
primary key would hold across multiple bootstrapped seasons — again, verified on purpose:
fetched 2023-24 and 2024-25 fixtures.csv and confirmed both use ids 1-380.
**What happened:** Fixture ids and gameweek numbers both reset to 1 every season. A schema
keyed on `fixtures.id` alone or `(player_id, gameweek)` alone would let a second season's
GW1 silently overwrite the first season's GW1 on ingest.
**Fix:** Added a required `season` column and composite primary keys — `(season, id)` for
fixtures, `(player_id, season, gameweek, source)` for player_gw_stats, similarly for
team_stats and understat_player_gw. Commit `a293123`.

---

## 4. Archive season referenced an `element_type` (5, "Manager") the live season doesn't have

**When:** First live run of `bootstrap_season("2024-25")`.
**Found by:** Running it — crashed with `sqlite3.IntegrityError: FOREIGN KEY constraint
failed` on the very first attempt.
**What happened:** FPL introduced a "Manager" player type partway through its history (one
per club — Arteta, Emery, etc., 20 rows in 2024-25's players_raw.csv). The current live
`element_types` table only had the 4 traditional positions, so inserting those 20 players
violated the FK.
**Fix:** `_ensure_element_types()` auto-registers any element_type id an archive season
references but the live season doesn't know about, using a known name for id 5 ("Manager")
and an explicit "Unknown position N" placeholder for anything else unrecognized — visible
and queryable rather than crashing or silently mislabeling. Commit `a293123`.

---

## 5. `id_dict.csv` — the archive's documented FPL↔Understat id mapping — doesn't exist

**When:** Implementing `player_id_map` population, following PRD §6a.4's plan to "check
the archive's own mapping first."
**Found by:** Trying to fetch it. 404. Checked six different seasons' `understat/`
directories via the GitHub API to rule out a per-season quirk — still 404 everywhere.
**What happened:** `DATA_DICTIONARY.md` documents `id_dict.csv` as a shipped file. Reading
vaastav's own `understat.py` script showed why it isn't there: `match_ids()` *generates*
that file locally as a script output — it was never committed to the repo.
**Fix:** Read `match_ids()`'s actual logic (exact string match on `"first_name second_name"`
vs Understat's `player_name`, no fuzzy matching) and replicated it in
`archive_loader.match_understat_ids()` against the two files that ARE shipped
(`players_raw.csv`, `understat_player.csv`). PRD's "check the archive's mapping first" plan
turned out to need one extra step, not a different plan. Commit `a293123`.

---

## 6. understat.com's page-embedded-JSON scraping technique no longer works

**When:** Implementing `understat.py`'s live scraper, per Architecture §4.1's original
design (`var playersData = JSON.parse('...')` in a `<script>` tag, matching vaastav's own
`understat.py`).
**Found by:** Testing the regex against a real fetched page before writing any parsing
logic around it — it didn't match. Checked three different pages (league table, a
completed season, a player page) to rule out a one-off fluke. None contained `playersData`,
`teamsData`, or `matchesData` anywhere in the HTML.
**What happened:** understat.com has been redesigned since vaastav's script was written.
The site is now a thin shell (~18KB) that loads stats client-side; the old technique is
simply dead. This is exactly the risk Architecture §6 already named ("Understat/FBref
scraping is fragile to site changes") — it just happened during initial build, not later.
**Fix:** Fetched the site's own minified JS bundles (`player.min.js`, `league.min.js`) and
found the real endpoints it calls (`getLeagueData/{league}/{season}`,
`getPlayerData/{player_id}`) — genuine JSON APIs, no HTML scraping needed. Verified both
live with real data before building `UnderstatClient` around them. Commit `a293123`.

---

## 7. `getPlayerData` returns a player's entire career, not one season

**When:** First live run of `understat.ingest_season("2024-25", max_players=5)`.
**Found by:** The result was almost entirely `unresolved_gameweeks` (89 unresolved vs. 1
resolved) — implausibly bad for a mechanism that had just been verified working. Debugged
by printing one player's raw fetched match and noticing its `"season"` field didn't match
the season being requested.
**What happened:** `getPlayerData/{id}` returns the player's full match history across
every season Understat has for them, not filtered to the one requested. Every match from
the wrong season was being fed into gameweek resolution against that season's fixtures
table and correctly failing to find a match — the resolver was working; the input wasn't
filtered.
**Fix:** Filter `player_matches()` results to `pm["season"] == season_to_understat_year(season)`
before resolving gameweeks. Commit `a293123`.

---

## 8. Feature columns disappeared entirely (not NaN) when there was no history yet

**When:** Live-testing `fpl-optimizer features` against the live preseason 2026-27 season
(zero gameweeks played).
**Found by:** `KeyError: "['appearances_5', 'points_mean_5'] not in index"` when inspecting
the output — the columns weren't NaN, they didn't exist at all.
**What happened:** `form.py` and `minutes.py` had early-exit branches (`if not gw_rows:
return out.reset_index()`, `if recent.empty: continue`) that skipped adding a feature
column entirely rather than adding it filled with NaN. Any downstream consumer expecting a
fixed schema — a model, a test, a merge — would break the moment it ran on a new season.
`fixtures.py` had the same issue for a fully blank gameweek.
**Fix:** Rewrote all three builders to always construct the full declared column set — via
groupby/agg on an explicitly-empty-but-correctly-shaped DataFrame, which naturally produces
all-NaN columns instead of missing ones. Added
`test_assemble_features_stable_schema_regardless_of_history` to lock the contract down.
Commit `002c8c5`.

---

## 9. Not a bug: this sandbox's data sources describe mutually inconsistent fictional seasons

**When:** Debugging why Understat↔archive gameweek resolution had a near-100% miss rate
even after fix #7.
**Found by:** Checking one specific "impossible" result (Liverpool vs Crystal Palace
appearing on a date that didn't match either of their two real fixtures that season) against
real-world knowledge, then checking the archive's own GW38 fixture list — which includes
Hull City and Coventry City in the Premier League. Neither club was in the real 2024-25
Premier League.
**What this means:** the live FPL API, the vaastav archive mirror, and understat.com are
each serving independently fictionalized data in this environment — they don't agree with
each other on what happened in "2024-25." The gameweek-resolution join is working
correctly; it just has little to resolve against here because the two sources disagree on
the season. Confirmed the mechanism itself is sound by checking the one match that *did*
resolve (`understat_player_gw` had exactly one correct row, matching a fixture both sources
agreed on).
**Not fixed, because there's nothing to fix in the code** — this is a property of the
sandbox's data, not a defect. Logged here so it isn't mistaken for a lingering bug in
`resolve_gameweek()` later. Would not occur against the real production FPL API / archive /
Understat.

---

## 10. Archive CSV nulls are the literal string `"None"`, not an empty string

**When:** Building the baseline predictor/optimizer, re-running `bootstrap-season` after
adding archive price-snapshot ingestion (needed so the optimizer has a budget to work
with).
**Found by:** Running it — crashed with `ValueError: invalid literal for int() with base
10: 'None'` on the very first attempt.
**What happened:** `_int_or()` and `db.insert_player_snapshots`'s `_pct()` both treated
`(None, "")` as the full set of "no value" cases, since that's what the FPL API's JSON
gives you for a null. But `players_raw.csv`'s `chance_of_playing_this_round` column (and
others) store an absent value as the four-character string `"None"` — a CSV artifact of
whatever wrote Python's `None` object as text rather than leaving the cell empty.
**Fix:** Both helpers now check against `(None, "", "None", "NA", "nan")`. A reminder that
"null" has more spellings in the wild than the two an API client happens to produce —
worth treating any newly-integrated text source's nulls as an open question, not an
assumption.

---

## 11. Optimizer's leakage guard correctly rejected my own too-early test date

**When:** First live run of `fpl-optimizer recommend` end-to-end.
**Found by:** `now_cost` was 0/804 non-null for every player — traced to `usable` having
zero rows after `dropna`.
**What happened:** Not a bug. The archive only ever gets one price snapshot per season
(dated at that season's last known fixture — see the `bootstrap_season` docstring), and I'd
asked for a recommendation as-of a date several months *before* that snapshot. The as-of
guard (`get_player_snapshots_as_of`, entry #2's whole reason for existing) correctly
returned nothing, because as of that date, in-universe, that price snapshot didn't exist
yet.
**Resolution:** Re-ran with `--as-of` set to the archive's actual snapshot date. Not a code
change — a reminder of a real limitation already documented in PRD §6a.4 and Architecture
§4.2 (archive-sourced state-at-deadline fields are single, approximate snapshots, not true
per-gameweek history): the optimizer can only price a squad as of the one date the archive
happens to give it a price for. Own-snapshot-sourced seasons (the live 2026-27 season,
going forward) won't have this limitation once enough daily snapshots accumulate.

---

## 12. HistGradientBoosting crashes outright on an entirely-NaN feature column

**When:** First live run of `fpl-optimizer train` against real 2024-25 data (M3, the GBM
ensemble).
**Found by:** Running it — crashed with `ValueError: window shape cannot be larger than
input array shape`, deep in sklearn's histogram-binning code, on the very first attempt.
**What happened:** Our real Understat coverage for 2024-25 is almost nonexistent (only 5
players were ever fetched, via the earlier `--max-players 5` demo, and the sandbox's
fictional-data mismatch — entry #9 — meant only 1 row actually resolved). Nine xG/xA/xGI
feature columns were consequently 100% NaN across the entire training set. sklearn's
`HistGradientBoosting*` handles *some* missing values natively (that's exactly why it was
chosen over needing an imputation step — Architecture §4.4), but an entirely-empty column
isn't "some missing values", it's zero data points to bin, and the binning step raises
rather than skipping it.
**Why this isn't specific to this dataset:** any thin data source — Understat coverage in
a brand-new season, a feature that's only just been added — would hit the identical wall.
**Fix:** Added `usable_columns()` (models/base.py): columns are checked for at least 2
distinct non-null values *within the specific training subset they'll be fit on* (not just
globally — `PointsModel` fits two independent regressors on different row subsets, and a
column can be fine overall but degenerate within just one of those slices) before being
handed to sklearn. The selected column set is stored at fit time and reused at predict
time, so train/predict never see a mismatched schema.

---

## 13. `predictions`/`recommendations` had no `season` column

**When:** M5, designing the schema change to persist every `recommend` call's output
(FR6).
**Found by:** Deliberately checking the existing `predictions`/`recommendations` DDL against
the same question entries #2 and #3 already answered for `players`/`fixtures` — do gameweek
numbers collide across seasons here too — before writing any code that would depend on the
answer, rather than after it silently merged two seasons' rows.
**What happened:** Both tables were keyed on `(model_version, player_id, gameweek, run_date)`
/ `gameweek` alone. Nothing had ever written to them yet, but the very first real use —
`fpl-optimizer recommend --season 2024-25 --gameweek 20`, exactly the example already in
`README.md` — would have collided with a `--season 2025-26 --gameweek 20` run under the same
key, identical to entries #2/#3's root cause.
**Fix:** Added a required `season` column to both tables and folded it into `predictions`'
`UNIQUE` constraint. Since both tables were empty (verified: `SELECT COUNT(*)` = 0 on the
local DB), this was a plain schema edit, not a migration.

---

## 14. Every command but `ingest` assumed the schema already existed

**When:** M5, first live run of `fpl-optimizer recommend` after entry #13's schema edit.
**Found by:** Running it — `sqlite3.OperationalError: no such table: predictions` on the
first attempt, immediately after the local `predictions`/`recommendations` tables had been
dropped (to pick up the new `season` column, entry #13) and recreated by manually invoking
`db.init_db()` once by hand.
**What happened:** `db.init_db()` — which is fully idempotent, just `CREATE TABLE IF NOT
EXISTS` statements — was only ever called from `_cmd_ingest`. Every other command
(`recommend`, `train`, `backtest`, `features`, and the new `results`/`evaluate`) called
`db.connect()` and assumed the schema was already current. A fresh clone that ran
`recommend` before ever running `ingest`, or — the case that actually happened here — any
existing DB that predates a schema change, would hit this same crash.
**Fix:** Moved the `init_db()` call into `db.connect()` itself. Since it's idempotent and
cheap at this data size (Architecture P4), every command now self-heals a stale or missing
schema instead of only `ingest` doing so; `_cmd_ingest`'s now-redundant explicit call was
removed.

---

## 15. `recommend` against a brand-new, zero-gameweeks-played season crashed on `np.exp`

**When:** Answering "can this be used before the season starts" by actually trying it —
`fpl-optimizer recommend --season 2026-27 --gameweek 1 --model poisson` against the real,
already-ingested live season (GW1's deadline hadn't passed yet).
**Found by:** Running it — `TypeError: loop of ufunc does not support argument 0 of type
float which has no callable exp method`, on the first attempt.
**What happened:** `features/fixtures.py`'s `_team_rolling_stats` returns
`pd.DataFrame(columns=["team_id", "goals_for_avg", "goals_against_avg", "clean_sheet_rate"])`
when there are no finished fixtures yet to aggregate — the right *columns*, per entry #8's
fix, but `pd.DataFrame(columns=[...])` with no data defaults every column to `object` dtype,
not `float64`. `PoissonPredictor.predict()` then does
`features["goals_against_avg"].fillna(1.5)`, which fills the values but does not change an
object-dtype column back to a numeric one, and `np.exp()` on an object-dtype array raises
instead of broadcasting elementwise. `form.py`/`minutes.py` already avoid this (their empty
branches build the aggregate result via an explicit `pd.Series(dtype=float)`) — this one
empty-fallback in `fixtures.py` was the one entry #8's fix didn't equally harden.
**Fix:** Made `_team_rolling_stats`'s empty-fixtures branch construct each column with an
explicit dtype (`pd.Series(dtype="float64")`, `dtype="int64"` for `team_id`) instead of bare
`columns=[...]`. Regression test asserts the dtype directly rather than just the column set,
since entry #8's existing schema test would not have caught this (it checks *presence*, not
dtype).

---

## 16. Captain/vice-captain ids weren't JSON-serializable

**When:** Same live run as entry #15, immediately after fixing it — the next `recommend`
call to actually reach the persistence step added for M5.
**Found by:** Running it (with `--model naive`, which doesn't touch `np.exp` and so got
past entry #15's bug) — `TypeError: Object of type int64 is not JSON serializable` from
`db.insert_recommendation`'s `json.dumps(payload)`.
**What happened:** `optimize/lineup.py`'s `build_lineup` builds `starting_xi`/`bench` via
`.tolist()` (which converts pandas' underlying numpy int64 values to plain Python ints), but
`captain`/`vice_captain` via `starting_xi.iloc[0]["player_id"]` — scalar row access, which
stays a numpy `int64`. That was invisible until M5 added a JSON payload; nothing before it
ever serialized a recommendation to JSON.
**Fix:** Wrapped both in `int(...)` in `lineup.py`, at the source, rather than defensively
casting in every caller. Regression test asserts `type(...) is int` and round-trips through
`json.dumps` directly, so this can't silently regress back to a numpy scalar.

---

## 17. M7 assumed to be next after M6 — checked before starting, wasn't

**When:** Right after M6 (Strategy layer) shipped, scoping M7 (auto-infer risk parameter)
per the PRD's own milestone order.
**Found by:** Deliberately checking what "calibrated against M4's harness" (PRD §10's own
M7 description) would actually mean before writing any code — same "verify the plan is
buildable before building it" instinct as entries #2/#3, not a bug caught by running
something.
**What happened:** M4's backtest harness and M6's `season_simulation.py` both replay
*archived* seasons using only the model's own predictions. Neither, nor anything else in
this project, has ever tracked a mini-league's rivals — historically or live. So "calibrated
against M4's harness" has no real referent yet: there is no `(gap, GW remaining, swing)`
history anywhere to calibrate a mapping against, which is exactly the overfitting risk the
PRD's own §11 already named as a risk of building this early.
**Resolution:** Not a code change — M7 is deferred (PRD §10/§11 updated to say so
explicitly), not simply next in the queue. Revisit once the live season has started and a
real mini-league's standings have actually been snapshotted for a few gameweeks; there's
nothing to build correctly before then.

---

## 18. `sqlite3.ProgrammingError` under FastAPI — connections aren't thread-affine the way the CLI assumed

**When:** M8, running the web app end-to-end for the first time — `GET /dashboard` (which
calls `GET /recommendations` and `GET /plans`) against a real logged-in session.
**Found by:** Running it — driving the actual Next.js dev server against the actual FastAPI
backend with real HTTP requests (Server Actions' no-JS multipart-POST protocol, curl-driven,
since no headless browser was available in this environment), not by any unit test. The
existing test suite's `TestClient` calls didn't catch this because Starlette's `TestClient`
dispatches synchronously by default in a way that happened not to trigger the same
cross-thread access.
**What happened:** `storage/db.py::connect` had always opened `sqlite3.connect(db_path)` with
default settings, fine for the CLI and every test (both strictly single-threaded per
connection). FastAPI's sync (`def`, not `async def`) routes and `yield`-based dependencies
each get dispatched via `anyio.to_thread.run_sync` independently — so `api/deps.py::get_conn`
can open a connection on one worker thread while the route body that consumes it (also
offloaded to the threadpool) runs on a different one. `sqlite3.Connection` defaults to
`check_same_thread=True`, which raises the moment that happens, even though actual usage
within one request is still strictly sequential — dependency yields the connection, then the
route uses it, never concurrently.
**Fix:** Added `check_same_thread=False` to the `sqlite3.connect(...)` call in
`storage/db.py::connect`. A no-op for the CLI and test suite (still single-threaded, still
never shares a connection across real concurrent access) and the correct fix for the API's
actual access pattern. General lesson repeated from entries above: this class of bug is
invisible to both code review and the unit-test suite, and only surfaces by actually running
the real server process and driving it with real requests.
