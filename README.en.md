[中文](README.md) | **English**

# EDGE — IGEM terpene pathway database

The dedicated database built by NJU-CHINA for the iGEM competition. It is centred on terpene
synthases and related compounds, and structures them as an "enzyme–reaction–compound" graph that
supports interactive pathway browsing, entity/pathway search, homology search and data download.

Data scale (measured 2026-10-02): **95,869 enzymes** (1,535 Swiss-Prot + 94,334 TrEMBL),
714 reactions, 734 compounds, 20,616 enzyme–reaction edges, 3,756,596 search-index rows.

---

## Requirements

The frontend uses the browser's `localStorage` to persist search terms, search sets, table filters,
the most recently completed BLAST result that reached the results page, and the download queue.
They are restored automatically on refresh or when you reopen the same site; clearing the download
queue also updates the cache. The cache is isolated per site address, and clearing site data resets
it. A corrupted cache falls back to defaults, and if storage is unavailable or full the page still
works for the current session. Interface data is always refetched; temporary graph expansions,
drag layouts and uncommitted pathway edits are out of scope for this cache.

Pages are determined by URL routing, so links can be copied, a refresh lands directly on the page,
and browser forward/back works: `/` is the home graph, `/search?q=terpene` is keyword search,
`/downloads` is the download table, and `/enzymes/<url-encoded enzyme ID>` is an enzyme detail page.
Opening `/` always lands on the home page and is no longer overridden by the old page cache. The
BLAST table uses `/search?mode=blast`, and its results remain in the current browser's cache — they
cannot be shared with another browser via a link. Unknown paths fall back to the home page.

`npm run dev` and `npm run preview` serve these paths. When deploying `dist`, enable SPA fallback
for the frontend (for example Nginx's `location / { try_files $uri $uri/ /index.html; }`) and keep a
separate `/api/` proxy rule; otherwise visiting a detail URL directly may return a server 404.

- **Python 3.10+** (with pip)
- **MySQL 8.0+**
- **Node.js 20+** (for the frontend; `frontend/package.json`'s `engines` is authoritative)
- Disk: **about 5 GB at steady state**; reserve **25 GB or more** for a full rebuild (measured
  breakdown in [`docs/wiki/database-protocol.md`](docs/wiki/database-protocol.md) §3.1)

---

## Full deployment (raw data → searchable database)

> This is the complete flow for **building the database from scratch** or **rebuilding it from an
> entirely new batch of data** — 7 steps in total.
> To refresh a single source without rebuilding everything, use §Routine updates instead — much
> shorter.

### 0. MySQL preparation (once per machine)

```ini
# my.ini  —— all three must be set explicitly, do not rely on defaults
datadir=D:/MySQLData/
tmpdir=D:/MySQLData/tmp
innodb_buffer_pool_size=2G
innodb_buffer_pool_instances=1
```

`tmpdir` **must be set explicitly**. By default MySQL puts temporary files under
`C:\Windows\ServiceProfiles\...\Temp`, and a full ETL produces several GB of them; once C: fills up
the ETL dies at step 6 with `OS errno 28 - No space left on device`, having got only halfway.

> **This repository ships no script for these three things.** We originally wrote PowerShell
> scripts for our own machine, but they hardcode that machine's drive letters and MySQL install path
> (`D:\MYSQL\bin\mysql.exe`, service name `MYSQL80`), and one of them deletes the old datadir
> irreversibly — not something to distribute with a repository. The operations themselves are
> below; run them one by one in an **administrator** PowerShell.

**① Move `datadir` off C:**

```powershell
Stop-Service MYSQL80 -Force
robocopy 'C:\ProgramData\MySQL\MySQL Server 8.0\Data' 'D:\MySQLData' /MIR /COPYALL /XJ /R:1 /W:1
# Compare "file count + total bytes" on both sides; stop here if they differ, and Start-Service to revert
# If they match → back up my.ini and change datadir= to datadir=D:/MySQLData
icacls 'D:\MySQLData' /grant 'NT AUTHORITY\NetworkService:(OI)(CI)F' /T
Start-Service MYSQL80
```

Once the service is up and the application reads normally, **only then** delete the old C: directory
by hand (this step is irreversible — do not do it early).

> `my.ini` is **UTF-8 without BOM**, and its comments contain a U+2212 minus sign. When editing, use
> `New-Object System.Text.UTF8Encoding($false)` to read and write; rewriting it as ASCII silently
> turns that character into `?`.

**② Add `tmpdir` to `[mysqld]`** — create the directory first, add the line, then restart the
service:

```ini
tmpdir=D:/MySQLData/tmp
```

**③ Pin the buffer pool to disk** — likewise under `[mysqld]`, then restart the service:

```ini
innodb_buffer_pool_size=2G
innodb_buffer_pool_instances=1
```

Verify once at the end:

```sql
SELECT @@datadir, @@tmpdir, @@innodb_buffer_pool_size, @@innodb_buffer_pool_instances;
-- D:\MySQLData\  D:/MySQLData/tmp  2147483648  1
```

### 1. Create the database and tables

```bash
mysql -u root -p < sql/schema.sql
```

`schema.sql` creates 15 tables, including `idx_search_index_value_prefix (field_value(64))` which
search needs — without it search is 500× slower. Do not create tables by hand.

### 2. Install dependencies

```bash
pip install -r etl/requirements.txt          # build stage
pip install -r update_tool/requirements.txt  # collection stage (used by steps 3–5)
pip install -r backend/requirements.txt      # backend
cd frontend && npm install && cd ..
```

### 3. Download the raw data

```bash
python update_tool/download_uniprot.py
```

It downloads the whole search set `(terpene)` at once, then **splits it locally on the `Reviewed`
column** into two segmented files:

```
update_tool/_src/swiss_prot/output_uniprot_unified.tsv   1,535 rows
update_tool/_src/trembl/output_uniprot_unified.tsv      94,334 rows
```

The two segments together equal the `total` UniProt reports — nothing is dropped. This step takes a
few minutes: 95,869 rows, 21 columns.

> **There is exactly one search term.** Do not fall back to the old "one term per source" approach —
> that is two time windows, and an upgrade/downgrade across windows makes the same accession land in
> both segments, or be missed by both.

### 4. Per-source production

```bash
python update_tool/run_all.py --source=swiss_prot    # ~9 minutes
python update_tool/run_all.py --source=trembl        # ~2.5–3 hours (many networked steps)
```

Each source produces 11 tables under `update_tool/_src/<source>/`. The two sources **never overwrite
each other**.

Order does not matter, but both must finish before the next step — `--merge` checks.

### 5. Merge

```bash
python update_tool/run_all.py --merge
```

The 3 cross-entry aggregate tables (substrate–product pairs / compounds / graph nodes) are rebuilt
from **all** segments into `update_tool/_merged/`. These three cannot be split by source: a
substrate→product pair is inherently shared by enzymes from both sources.

### 6. Deploy to `for_*/`

```bash
cd update_tool
python update_database.py --source=swiss_prot --dry-run
python update_database.py --source=trembl     --dry-run
python update_database.py --merge             --dry-run
# ↑ check the three reports, then run each again without --dry-run
```

**It is three commands, not one.** Before overwriting it backs up automatically to
`_backup_<timestamp>/`.

Where the files land (note that the 11th is not under `for_*/`):

```
for_enzyme_detail/child_tables/uniprotkb_{go,isoform_sequences,names_split,references,rhea,sequence_links}.<source>.tsv
for_enzyme_reaction_card/uniprotkb_{enzyme_merged,rhea_summary}.<source>.tsv
for_enzyme_detail/uniprotkb_master.<source>.tsv
for_graph/uniprotkb_terpene_only.<source>.tsv
uniprotkb_terpene_parsed.<source>.tsv                    ← lands in the **repository root**
for_graph/uniprotkb_terpene_pairs.tsv                    ← the following 3 have no suffix (merged)
for_compound_card/uniprotkb_terpene_compounds.tsv
for_graph/all_nodes.tsv
```

### 7. Load into MySQL with the ETL

```bash
cd etl
export IGEM_DB_PASSWORD='your password'    # Windows cmd: set IGEM_DB_PASSWORD=your password
python -u etl_run.py
```

About **41 minutes**, 6 steps: enzymes → compounds → reactions → edges → master → search index.
Building the index accounts for roughly 20 minutes of that.

Add `-u` to turn off Python's output buffering — otherwise the log sits in memory and looks stuck.

Verify after loading:

```sql
SELECT source_type, COUNT(*) FROM enzyme GROUP BY source_type;   -- 1535 / 94334
SELECT COUNT(*) FROM enzyme_id_map WHERE retired_at IS NOT NULL; -- 0
```

### 8. Start the services

```bash
# backend (in another terminal)
cd backend
export IGEM_DB_PASSWORD='your password'
uvicorn app.main:app --port 8000

# frontend
cd frontend
npm run dev
```

Open **http://localhost:5173**; API docs at **http://localhost:8000/docs**.

### Time and output per step (measured on our machine)

| Step | Command | Time | Output |
|---|---|---|---|
| 3 | `download_uniprot.py` | a few minutes | 2 segmented raw tables (1,535 / 94,334 rows) |
| 4 | `run_all.py --source=swiss_prot` | ~9 minutes | 11 files in `_src/swiss_prot/` |
| 4 | `run_all.py --source=trembl` | ~2.5–3 hours | 11 files in `_src/trembl/` |
| 5 | `run_all.py --merge` | a few minutes | 3 files in `_merged/` |
| 6 | `update_database.py` ×3 | seconds | 14 files written under `for_*/` |
| 7 | `etl_run.py` | ~41 minutes | 14 MySQL tables (15 in the database, see §MySQL tables) |

---

## Notes

### A. Disk and MySQL (the most likely thing to kill a full run)

- **Neither MySQL's datadir nor its tmpdir may live on C:** see §0. A full ETL writes about
  **4 GB of binlog** and several GB of temporary files per run; putting either on C: will fill it.
- **binlog follows datadir** (`log-bin` is a relative path), so once datadir moves to D: binlog
  grows there too.
- **After restarting `MYSQL80` you must restart uvicorn.** Every aiomysql connection in the
  backend's pool is dead, and each request returns 500 until uvicorn restarts.
- If a port is taken (`[Errno 10048]`) yet the backend "keeps returning 500", the usual cause is
  **the previous uvicorn process still being alive**: `netstat -ano | grep :8000` to find the PID,
  then `taskkill //PID <pid> //F`.

### B. Commands must carry `--source=` or `--merge` explicitly

Both `run_all.py` and `update_database.py` **force** a choice; run bare, they print usage and exit.
This is the entry gate of the segmentation design — do not add a default "for convenience".

### C. The `enzyme_id` rules — read this section before changing any data

- `enzyme_id_map` is the only guarantee that **an identifier never changes**: if an accession is
  already in the table, a rerun always gets the same number back (measured 95,869/95,869, zero new
  assignments).
- Therefore **`enzyme_id_map` never takes part in any `DELETE` / `TRUNCATE`**, and it deliberately
  **omits** `FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)` — when an entry disappears from a
  new release, the `enzyme` row is replaced and deleted, while the mapping row must survive.
- **Restoring the database must restore it too.** Restore only `enzyme` without `enzyme_id_map` and
  the next ETL reallocates identifiers — the ENZ numbers already distributed now point at other
  enzymes.
- Identifiers **only go up and holes are never filled**: once an entry disappears its number is kept
  forever, and a new entry takes `MAX+1`.
- **Identifiers appear in more than `enzyme.enzyme_id`**; do not look only at that column when
  debugging. They are also in `search_index.entity_id`: rows with `entity_type='enzyme'` are
  `ENZ……`, and the fallback rows with `entity_type='evidence'` that lack a DOI/PMID are
  `ENZ……:reference:N`. Measured: comparing a from-scratch build against the real database yields
  46,410 `search_index` "differences", and **zeroing them requires only swapping those two places
  back to the stable identities** — i.e. every difference is in the identifiers, and the contents
  are row-for-row identical.
- **Identifier succession** (carrying an old number over when an entry changes its primary
  accession) is a different mechanism — `enzyme_alias_map` plus `resolve_vanished`'s 302-redirect
  resolution, with `REDIRECT_LIMIT = 500`, and it is **skipped entirely** in `--source=<s>` mode. Do
  not conflate it with the "identifier retention" above.

### D. Single-source refresh vs. full run: two judgements only a full run can make

Single-source mode sees only half the table, so there are two things it **has no authority** to do.
Both are explicitly skipped in the code (not omissions):

| Thing | Behaviour in single-source mode | Why |
|---|---|---|
| Maintaining `enzyme_id_map.retired_at` | **the whole step is skipped**, with a printed explanation | deciding "an entry disappeared" needs the full view |
| `reaction` attributes (`direction` etc.) | **insert-only** (`INSERT IGNORE`) | the same Rhea ID can carry a different `Direction` in the two sources; a reaction is one objective entity per Rhea ID, and only the merged view has the authority to decide which row it takes |

The consequence of not doing this was measured: running only `--source=trembl` stamps `retired_at`
on all 1,535 SwissProt entries (a false "disappeared" marker) and overwrites 12 rows of
`reaction.direction` from `forward`/`reverse` to `unknown` (adding mirrored edges to the graph).
Both are now fixed.

### E. Idempotence and the one known deviation

Rerunning the ETL with byte-identical input leaves all 14 tables row-for-row unchanged (all five
sandbox acceptances A/A'/B/C/D pass). **The only known deviation**: `search_index` gains **+16 rows**
per rerun — 24 groups of fully duplicate input rows exist on the source side and get separated by
slice boundaries. The row contents are correct, it costs a few dozen extra rows, and no query result
is affected. **This is accepted.**

### F. Checkpoint caches and reruns

| Step | Cache | After a failure |
|---|---|---|
| `fetch_isoform` / `fetch_references` / `fetch_sequence_links` | `_*_cache.json` (deleted on success) | delete the partial output and rerun to resume |
| `fetch_rhea` / `build_terpene_only` | **none** | rerun the step as a whole |
| `build_all_nodes` | **none** (purely offline, finishes in seconds) | rerun the step as a whole |

`run_all.py` **skips existing output by default**; add `--force` to rebuild everything.

### G. Removed old entry points

These two scripts were deleted from `update_tool/` on 2026-10-02. Recorded here so it is clear why
they are gone:

- **`run_workflow.py` / `run_workflow.sh`** — the old entry point from **before** the segmentation
  rework, and already broken: its step ② calls `run_all.py --force --out-dir=... <table>` without
  `--source`, which just prints usage and exits; and its search term hardcodes `reviewed:true`. **We
  did not repair it**, because repairing it would imply it still works.
- **`update_tool/fetch_inchikey.py`** — not wired into any flow. It produces 6 columns and
  **overwrites in place** `for_graph/all_nodes.tsv` (the current table has 4), bypassing
  `update_database.py`'s backup. Its function is now carried by `build_all_nodes.py`, which reads
  `chebi_data/structures.tsv.gz` and needs no network.

### H. When changing tool code

- **The boundary of `--source=<s>` / `--merge` is "does one row correspond to one UniProt entry"**,
  not "does it look splittable". Hard-splitting a cross-entry aggregate table produces duplicate rows
  and duplicate nodes.
- **`update_database.py` overwrites whole files with `shutil.copy2`**; each table under `for_*/*.tsv`
  has exactly one physical location. That is why isolation has to happen at the **production stage**
  (`_src/<source>/`) and cannot rely on the deployment stage.
- **There is one source registry**: `SOURCES` in `update_tool/source_registry.py`. When adding a
  source, **change only that**; `--merge`'s safety valve validates source by source against it, and a
  second copy would drift.
- In the output tables the **`Source` column is always last** — the column widths of `references` /
  `sequence_links` / `master` are **dynamic**, and only a trailing position keeps the name→index
  mapping stable.
- After a full ETL `search_index` has several million rows; **read §Verification's probes before
  changing anything index-related**.

---

## Routine updates

### Refresh a single source

```bash
python update_tool/download_uniprot.py            # 1 download, split locally
python update_tool/run_all.py --source=trembl --force
python update_tool/run_all.py --merge             # the merge must be rerun
python update_tool/update_database.py --source=trembl
python update_tool/update_database.py --merge
cd etl && python -u etl_run.py --source=trembl
```

`--source=trembl` replaces only the TrEMBL rows; the SwissProt rows and their identifiers are
**untouched, not one of them** — this is the point of source isolation (measured: only the known
`search_index` +16 deviation appears).

⚠️ **`--merge` must be rerun alongside it**, because the merged tables read `terpene_only` from all
sources.

⚠️ **`etl_run.py --source=<s>` is a half view** — see §D: it skips the identifier retirement marker
and the shared reaction attributes.

### New data / returning to an earlier state

- **New data**: rerun from §3 (the segments are regenerated in full).
- **Returning to an earlier state**: restore a `mysqldump` from `_db_backup/`. Do **not** expect
  rerunning the ETL to reproduce it.

**This was measured on 2026-09-20** (building `igem_terpene_repro` from scratch with the same
`for_*/`, then comparing row by row):

| Layer | Conclusion |
|---|---|
| Produced → on disk → database contents | **Fully reproducible**: the 14 tables match row count for row count, with contents identical **row by row** (aligned on the stable identity `uniprot_id`, judged by multiset equality) |
| **Identifiers** | **Not reproducible**: the `enzyme_id` values of the 1,535 SwissProt enzymes differ from the real database (the 94,334 TrEMBL ones happen to match) |

The reason: `enzyme_id` is **a carrier of state, not a function of the data**. The real database's
1,535 identifiers come from an earlier positional numbering scheme and were carried over during the
expansion via `enzyme_id_map`; a from-scratch build has no such history, so it reallocates (new
numbers are always `MAX+1`, so it starts at `ENZ000001` and diverges from the real database).

**To "restore" the identifiers**: copy the real database's `enzyme_id_map` and `enzyme.enzyme_id`
over and rerun the ETL — because the only source of identifiers is that table (measured: all
95,869/95,869 carried over, zero new assignments).

---

## Verification

During development we guarded the pipeline with a set of probe scripts, run as needed after changes.
They are **not in this repository** (and have also **been deleted from the working tree**, see
[`docs/wiki/database-protocol.md`](docs/wiki/database-protocol.md) §2.3) — each one needs a
connection to the local `igem_terpene` database and `for_*/`, with paths hardcoded to the development
machine. The table below says what each probe verified, so you can write your own:

> Naming convention: print elapsed time, accumulate a `failures` list, exit non-zero, and print
> `ALL CHECKS PASSED` at the end.
> The Windows console is GBK; set `PYTHONIOENCODING=utf-8` before running.

> **Do not assume they are read-only**: most do only query, but two of them wrote to the database —
> one ran `ALTER TABLE ... ADD INDEX` on `search_index` (the prefix index from §1), the other cleared
> `performance_schema` monitoring tables. If you write your own probes, keep write statements
> separate and never mixed into a script you run casually.

| Check | What it verifies |
|---|---|
| End-to-end chain | Produced ↔ `for_*/` byte for byte; **the ETL's own read layer** parses as many rows as it reads back; file ↔ database aligned table by table |
| Identifier retention | Live mapping → all carried over; empty mapping → all reallocated; retired numbers not recycled |
| Reload comparison | Load the same `for_*/` into another database and compare **row by row** against the real one (aligned on the stable identity `uniprot_id`, multiset equality) |
| Search sets | Partitioning of search / search sets, the truthfulness of `total`, equivalence of EC and species pushdown |
| BLAST counts | The BLAST count endpoint == `searchedSubjects` from an actual run |
| Home graph payload | Item-for-item identical to the baseline |

Manual smoke test after deployment:

1. `GET /metadata/filter-options`'s `sourceTypes` includes `trembl`
2. `GET /search/entries?q=synthase&source_types=trembl` returns a full page **and includes enzymes
   with no reaction edges** (the domain was not swapped for the with-edges subset)
3. `GET /enzymes/{id}` on a TrEMBL enzyme shows the source badge as `trembl`
4. `GET /graph?selection_mode=global&limit_nodes=120` returns 200, and **all** enzymes on its edges
   have reaction edges
5. `GET /blast/subjects`'s count varies across 95,899 / 1,565 / 94,334 with the search set

---

## Rollback

| Layer | How to revert |
|---|---|
| Database | Restore `_db_backup/igem_terpene_*.sql` (`mysql -u root -p < backup.sql`) |
| Old `for_*/` tables | `python update_tool/update_database.py --restore=<backup dir>` (the one created automatically before overwriting) |
| Produced files | Rerun `run_all.py` (with unchanged input the result is byte-for-byte identical) |

---

## Repository layout

```
igem_database/
├── sql/schema.sql                  # table DDL (15 tables + indexes; the ETL loads 14 of them)
├── update_tool/                    # data acquisition and rebuild workflow
│   ├── download_uniprot.py         # ① download + split by Reviewed locally
│   ├── run_all.py                  # ② per-source production ③ merge
│   ├── update_database.py          # ④ deploy into for_*/
│   ├── source_registry.py          #   the single source registry (adding a source touches only this)
│   ├── chebi_data/                 #   bundled ChEBI reference library
│   ├── _src/<source>/              #   11 segmented outputs per source
│   ├── _merged/                    #   3 merged outputs
│   └── WORKFLOW.md                 #   workflow maintenance manual (partly predates the segmentation rework)
├── etl/                            # ⑤ TSV → MySQL
│   ├── config.py                   #   connection config (overridable by environment variables)
│   ├── etl_run.py                  #   runs the 6 steps
│   └── etl_*.py                    #   per-table imports
├── backend/                        # FastAPI backend
│   ├── app/{main,config,database}.py
│   ├── app/{models,schemas,routers,services,utils}/
│   ├── .env.example                #   environment template (copy to .env and fill in)
│   └── .env                        #   database password (not committed to git)
├── frontend/                       # React + Vite frontend
├── for_enzyme_detail/              # on-disk TSVs (segmented files carry a .<source> suffix)
├── for_enzyme_reaction_card/
├── for_compound_card/
└── for_graph/
```

The following are **not in the repository** — they only appear on a machine that has actually run
things (all of them are in `.gitignore`): `backend/.env` (database password), `backend/blast_bin/`
and `backend/blast_work/` (§3.4), `update_tool/_src/` and `_merged/` (intermediates),
`_db_backup/` (mysqldump backups), and the group of development verification probes (**deleted**,
see above).

`update_tool/`'s isolation principle: it **read-only** accesses the raw directories and
`chebi_data/`, and writes every output into its own directories.
**Never modify any file in the raw folders.**

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `IGEM_DB_HOST` | `localhost` | MySQL host |
| `IGEM_DB_PORT` | `3306` | MySQL port |
| `IGEM_DB_USER` | `root` | MySQL user |
| `IGEM_DB_PASSWORD` | *(empty)* | MySQL password, **required by the ETL and the backend** |
| `IGEM_DB_NAME` | `igem_terpene` | Database name (can point at another database for reproduction/sandbox) |
| `IGEM_DATA_DIR` | repository root | which `for_*` the ETL reads (for sandboxes) |
| `IGEM_BLAST_BIN_DIR` | `blast_bin` | directory holding the BLAST+ executables (empty string = use the system PATH) |
| `IGEM_BLAST_WORK_DIR` | `blast_work` | BLAST+ working directory; the path must be all ASCII |
| `PYTHONIOENCODING` | — | set to `utf-8` when running scripts that print Chinese to a Windows console |

The backend can also be configured through `backend/.env`; the template is `backend/.env.example`.
**Note that `backend/.env` only feeds the backend** — the ETL reads these variables directly from
the process environment and does not load that file. Pass the password through environment
variables; **do not put it on the command line** (it shows up in the process list).

---

## MySQL tables (row counts measured 2026-10-02)

| Table | Rows | Meaning |
|---|---|---|
| `enzyme` | 95,869 | static enzyme attributes (1,535 Swiss-Prot + 94,334 TrEMBL) |
| `enzyme_id_map` | 95,869 | **the permanent identifier mapping, never deleted** (see §Notes C) |
| `enzyme_alias_map` | 0 | identifier succession (when an entry changes accession) |
| `enzyme_reaction_edge` | 20,616 | graph edges (enzyme ↔ reaction) |
| `reaction` | 714 | reaction facts (Rhea), an entity shared across sources |
| `compound` | 734 | compound nodes (ChEBI, common small molecules filtered out) |
| `reaction_compound` | 1,557 | substrate/product relations |
| `gene` | 92,252 | gene links for enzymes |
| `gene_sequence_link` | 620,808 | INSDC/RefSeq external sequence links |
| `evidence` | 112,720 | literature evidence |
| `enzyme_go` | 75,874 | GO annotations (biological process only) |
| `enzyme_isoform` | 56 | isoform sequences |
| `enzyme_solubility_score` | 95,899 | per-enzyme model reference score + membrane annotation. **Produced by an external pipeline** (see `NOTICE` › Not redistributed); not generated by `update_tool/` |
| `search_index` | 3,756,596 | search index aggregating all fields |
| `pathway_cache` | 0 | pathway result cache (**the ETL never touches it**) |

The database holds **15 tables** in total. `pathway_cache` is the only one the ETL never writes to
(zero references anywhere under `etl/`); the other **14 are loaded by the ETL** —
`enzyme_solubility_score` among them, even though its data comes from an external pipeline.

So this section has **15 rows**. A few places elsewhere say "14 tables" (for example the
idempotence conclusion in §Notes E, and the reproducibility measurement in §New data); those all
mean the 14 tables the ETL writes. Different scope, not a contradiction.

---

## API endpoints

Base path `/api/v1`. Full documentation: **http://localhost:8000/docs**

| Method | Path | Meaning |
|---|---|---|
| GET | `/metadata/filter-options` | filter options (`?module=graph\|table` selects the species domain) |
| GET | `/graph` | network graph data (`selection_mode=global`, or by enzyme) |
| POST | `/graph/map-scope` | search term → graph scope |
| POST | `/graph/by-enzymes` | given a set of enzymes → graph |
| GET | `/graph/edge-groups/{id}/edges` | expand overlapping edges |
| GET | `/search/entries` | entry search (cards, paginated) |
| GET | `/search/table` | table search (true `total`, pushed-down filters) |
| POST | `/search/table/by-ids` | fetch rows by explicit id (used by BLAST) |
| POST | `/search/pathways` | pathway search |
| GET | `/ketcher/search` | structure search (find reactions by InChIKey) |
| GET | `/enzymes/{enzymeId}` | enzyme detail (including the enzyme's own source) |
| GET | `/compounds/suggest` | compound autocomplete |
| GET | `/compounds/{compoundId}/card` | compound card |
| GET | `/reactions/{reactionId}` | reaction detail |
| GET | `/bundle/{kind}/{entityId}` | entity fields plus its compound structure image URLs in one call (`kind` = `enzyme\|compound\|reaction\|pathway`) |
| GET | `/bundle/compound?smiles=` | fetch a compound by SMILES (the server converts to InChIKey with RDKit; the response is shaped like `/bundle/compound/{id}`) |
| POST | `/bundle/batch` | batch version (≤50 items, returned in argument order; one missing item does not fail the batch) |
| GET | `/blast/subjects` | number of BLAST database sequences under the current search set |
| POST | `/blast/search` | run BLAST (builds the database per search set) |
| GET | `/download/fields` | exportable fields |
| POST | `/download/preview` | download preview |
| POST | `/download/files` | generate a download file |
| GET | `/downloads/{filename}` | retrieve a generated file |
| GET | `/assets/reactions/{rheaId}/atom-map.svg` | reaction atom-map SVG |
| GET | `/assets/compounds/{chebiId}/structure.svg` | compound structure SVG |

With `kind=pathway` the `entityId` is a compound chain (`PATH_A_B_C`), not a table row — it is
returned by `POST /search/pathways`, and is **recomputed** here by the same rules, with
`entity.graph` containing only this chain's edges.
The id **does not encode filter conditions**, so if the original search used
`sourceTypes`/`reviewStatuses`, the retrieval must carry the same query parameters; otherwise the
segments may point at a different set of edges. Batch items cannot carry filters, so pathways inside
a batch are always rebuilt unfiltered.

---

## FAQ

- **Q**: `run_all.py` says "must specify `--source=<s>` or `--merge`"?
  **A**: By design. See §Notes B — the segmented flow has no bare-run mode.
- **Q**: `--merge` refuses to run, saying segments are incomplete?
  **A**: A safety valve. Finish the missing source first (it lists which files are missing). This is
  **deliberate**: an incomplete merge would overwrite a previously complete version.
- **Q**: `update_database.py` says the row count shrank by more than half?
  **A**: Refusing by default is protection. Once you have confirmed the new data really has that
  scale (say you switched to a narrower search term), add `--allow-shrink`.
- **Q**: After a rerun the output has 4 fewer rows — is that normal?
  **A**: Yes. The Rhea annotations for `B5A435 / K9Y6Y9 / Q45222 / S0ENM8` were **withdrawn
  upstream** (they are still Swiss-Prot entries, they simply no longer carry a Rhea cross-reference).
  Their `enzyme` rows and identifiers are retained; only the edges disappear.
- **Q**: `fetch_isoform` produces 0 rows for TrEMBL — are we losing data?
  **A**: No. TrEMBL's automatic annotation records only the annotation type and carries no isoform
  definition (0 rows containing `IsoId`). The Swiss-Prot segment has 26 entries with `IsoId`,
  producing 56 rows.
- **Q**: The ETL looks stuck?
  **A**: Check the log's mtime first. An idle machine also freezes processes (this is not a
  deadlock); during a run add `-u`, so that output buffered in memory is not mistaken for a stall.

---

## Licence and attribution

| Part | Licence | Full text |
|---|---|---|
| Source code (`backend/` `frontend/` `etl/` `update_tool/` `tools/` `sql/`) | Apache License 2.0 | [`LICENSE`](LICENSE) |
| Derived tables (`for_*/`) and documentation (`docs/`, `.docx`, `.png`, `.json`) | CC BY 4.0 | [`LICENSE-DATA`](LICENSE-DATA) |
| Verbatim redistribution of upstream data (`update_tool/chebi_data/`) | ChEBI's own terms (CC BY 4.0) | [`LICENSE-DATA`](LICENSE-DATA) |

The `update_tool/` row above refers to its **code**; the `chebi_data/` beneath it holds EBI's ChEBI
data (the original archives the pipeline downloads, plus the two tables `update_chebi_library.py`
derives from them), which is not our work.

The attribution for the upstream data (UniProt / Rhea / ChEBI / DDBJ), the licence and notices for
the bundled third-party software (Ketcher), and the reasons why NCBI BLAST+ and
DeepSolNet / ESM C 300M are **not** redistributed in this repository are all recorded in
[`NOTICE`](NOTICE).
The data and documentation use CC BY 4.0, which is the upstream sources' own licence — please retain
the attribution in it when reusing this database.
See [`docs/wiki/database-protocol.md`](docs/wiki/database-protocol.md) §12 for the full discussion.
