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
