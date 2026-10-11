# Database Protocol

This page documents how our terpene enzyme database is built, what it contains, how to run it,
and what the solubility score in the interface actually means. It is written so that a third
party — a judge, or a future team — can decide whether our data is trustworthy and whether the
whole build is reproducible.

Two conventions used throughout:

- **Paths** are relative to the repository root. Run the commands in §9 from the repository root
  unless the step says otherwise.
- **Anything in angle brackets is a placeholder.** `<DATA_DISK>` means "a disk you choose on your
  own machine". We do not prescribe directory locations — we only prescribe where things must
  *not* go (§3.2).

> All size and row-count figures below are a measured snapshot. They drift as UniProt updates
> (we last measured on 2026-09-20). The structural parts — table layout, pipeline, dependencies —
> are the stable parts.

---

## 1. System overview

The database is not a single web application. It is a **four-stage pipeline plus a read-only web
app**. The stages are not coupled through code; they hand off through TSV files on disk.

```
        UniProt / Rhea / ChEBI / DDBJ          (external sources, see §4)
                    │
                    ▼
    1  update_tool/   collect + transform
                    │  output: source-segmented TSVs under for_*/
                    ▼
    2  etl/           load into MySQL (6 steps, ~41 min)
                    │  output: 15 MySQL tables
                    ▼
    3  backend/       read-only FastAPI  ──►  4  frontend/   React SPA
```

| Stage | Directory | Responsibility | Re-runnable |
|---|---|---|---|
| 1 Collect | `update_tool/` | Call external APIs, produce TSVs | Yes; networked steps take ~3 h |
| 2 Load | `etl/` | TSV → MySQL | Yes, idempotent |
| 3 Backend | `backend/` | Read-only queries + BLAST | Stateless |
| 4 Frontend | `frontend/` | Graph and search UI | Stateless |

**Design constraint.** The only contract between stages 1 and 2 is the set of segmented TSV files
under `for_*/`. The ETL reads them through a single entry point (`etl/sources.py`) and **fails hard
when a file is missing** — it never silently skips. The reason is asymmetric risk: reading one
source too many merely wastes time, while reading one source too few loses data silently, and the
signature of that defect is that everything still appears to work.

---

## 2. Repository layout

The tree falls into three groups: **the pipeline and application** (what you need to run the
database), **the hand-off data**, and **development artifacts** (what we used while building it —
none of which is needed to run anything).

### 2.1 Pipeline and application

| Path | Contents |
|---|---|
| `update_tool/` | Stage 1 — collectors (`download_*`, `fetch_*`), builders (`build_*`), plus `run_all.py`, `update_database.py`, `source_registry.py`. Documented in `update_tool/WORKFLOW.md`. |
| `for_*/` | The hand-off TSVs between stages 1 and 2 — see §2.2 |
| `etl/` | Stage 2 — `etl_run.py` and the six stage modules |
| `sql/schema.sql` | The single source of truth for DDL |
| `backend/` | Stage 3 — FastAPI service; BLAST+ at `blast_bin/` (**not in this repo** — see §3.4), BLAST scratch at `blast_work/`, generated exports at `app/downloads/` |
| `frontend/` | Stage 4 — React + Vite single-page app |
| `tools/` | `fix_mysql_timestamp_defaults.sql` — a one-off schema repair |
| `docs/` | This protocol |
| `README.md` | The operational runbook: prerequisites, the deploy sequence, service start commands, and MySQL/uvicorn gotchas |
| `LICENSE`, `LICENSE-DATA`, `NOTICE` | The two license texts and the third-party attribution statement — see §12 |

### 2.2 Hand-off data (`for_*/`)

These four directories are the only contract between stage 1 and stage 2, and the only input the ETL
reads. Within each, a file suffixed `.<source>.tsv` is a per-source segment and the unsuffixed
`.tsv` is the merge of the two.

| Directory | Contents |
|---|---|
| `for_enzyme_detail/` | `uniprotkb_master.{swiss_prot,trembl,}.tsv`, plus `child_tables/` — seven child tables (names, Rhea links, references, sequence links, GO, isoforms, solubility scores) |
| `for_enzyme_reaction_card/` | `uniprotkb_enzyme_merged.*`, `uniprotkb_rhea_summary.*` |
| `for_compound_card/` | `uniprotkb_terpene_compounds.tsv` |
| `for_graph/` | `all_nodes.tsv`, `uniprotkb_terpene_only.*`, `uniprotkb_terpene_pairs.tsv` |

> ℹ️ This directory was misspelled `for_enzyme_reation_card` ("reation" for "reaction") until
> 2026-10-02, and `etl_enzymes.py` / `etl_search_index.py` read it under that spelling. Directory
> and readers were renamed together. A database loaded before that date still carries the old path
> in `search_index.source_file` until `etl_run.py` is re-run.

### 2.3 Development artifacts

While building this database we kept a set of throwaway scripts, and the measurements they produced,
at the repo root. **None of it is needed to run the pipeline or the web app**, and it is excluded
from the disk figures in §3.1. We have since pruned it; this section records what was there.

**Almost none of it was ever in this repository.** The rules that kept it out (`/_*.py`,
`/_*.ps1`, `/_*.log`, plus the five JSON patterns `/_graph_*.json`, `/_iso_*.json`, `/_ab_*.json`,
`/_pool_*.json`, `/st_*.json`) are still in `.gitignore`. There is deliberately **no bare
`_*.json` rule** — a bare pattern ignores at any depth, so it would reach into `update_tool/`
and match the tracked data there (historically `_allnodes_inchikey.json`; that file was removed
2026-10-02 when `build_all_nodes.py` stopped fetching keys and went offline). The rules stay
anchored. The exceptions are called out in the table.

| Group | Count | Status | What it was |
|---|---|---|---|
| `_*.py` at the repo root | 24 | *deleted 2026-10-02* | Probe and measurement scripts. Nothing imported them — but "read-only" would be the wrong description: `_pfx.py` ran an `ALTER TABLE ... ADD INDEX` against `search_index` (the same index `sql/schema.sql` creates), `_perf3.py` truncated a `performance_schema` monitoring table, and five of them wrote the JSON snapshots below. |
| `_*.json` at the repo root | 9 | *deleted 2026-10-02* | Measurement snapshots those probes wrote — A/B payload pairs, buffer-pool comparisons. |
| `_*.ps1` at the repo root | 4 | *deleted 2026-10-02* | One-time MySQL setup — move `datadir`, set `tmpdir`, persist the buffer pool, drop the stale pre-move datadir. Each hard-coded the drive letters, install path and service name of the machine it ran on. All four had already been applied there; §3.2 and §9 spell the same operations out as steps, so nothing depends on the scripts. |
| **`ge60.json`, `graph_full.json`, `group_edges.json`** | 3 | **tracked in this repository** | Graph payload captures, **~350 KB total**. These three *are* committed — they arrived with an early whole-workspace sync commit rather than as a deliberate choice, and along with `icon.png` below they are the only files in this section a reader will actually find in a clone. |
| `st_600.json`, `st_2000.json` | 2 | untracked | Captures of the same kind, written later. |
| `_db_backup/`, `_dbbackup/`, `_pre_merge_backup_<timestamp>/` | — | untracked | Database dumps, **~1.2 GB combined** |
| `_mysql_move.log`, `mysqldata_copy.log` | — | untracked | Logs from the MySQL directory move |
| `uniprotkb_terpene_parsed.{swiss_prot,trembl}.tsv` | 2 | untracked | The **parsed intermediate** written by `update_tool/parse_names.py` (1,536 + 94,335 rows) — not the raw download. The **runtime** ETL does not read them (it reads `for_*/` only), but `update_tool/build_names_split.py` does take them as its input, so they sit on the re-build chain rather than the serving path. Both are byte-identical duplicates of `update_tool/_src/{swiss_prot,trembl}/output_parsed.tsv`. The download they were parsed from (`uniprotkb_terpene_AND_reviewed_true_2026_07_10.tsv`) is no longer on this machine. |
| `icon.png` | 1 | **tracked in this repository** | Site icon |

`update_tool/` keeps its own working directories — `_src/` (staging), `_merged/` (merge staging),
`_sandbox/` (the sandbox tree used by `IGEM_DATA_DIR`, §3.5) — plus `chebi_data/` (including
`curation_overrides.tsv`) and `child_tables/`.

The measurements quoted throughout this page came out of the scripts in the first three groups. Now
that those are gone, the figures are a record of one machine at one time; the ones derived from a
live database (§3.1) can be reproduced by querying it, but the A/B comparisons cannot be re-run as
they were.

---

## 3. Environment

### 3.1 Runtime and hardware

| Item | Requirement | Note |
|---|---|---|
| Python | **3.10+** | Our build environment measured 3.11 |
| MySQL | **8.0+** | InnoDB, utf8mb4 throughout |
| Node.js | **≥ 20** | Frontend only |
| NCBI BLAST+ | `blastp` / `makeblastdb` executables | See §3.4 |
| Disk | **≥ 6 GB** to run; reserve **~25 GB** for a full rebuild | See below |

**Measured disk usage.** Steady-state operation and a full rebuild differ by an order of magnitude.
Do not plan capacity off one number:

| Scenario | Usage | Composition |
|---|---|---|
| **Steady state** (database built, no further rebuilds) | **~5 GB** | MySQL data files 2.7 GB + undo/system tablespace ~1.6 GB + `for_*` outputs 0.4 GB + BLAST binaries and index 0.6 GB + frontend `dist` 0.1 GB |
| **Full rebuild peak** | **~10–12 GB** | The above, plus intermediates `_src/` 0.6 GB + several GB of MySQL temp files + ~1.3 GB of binlog per run |

Two things that are easy to get wrong:

- **The binlog is an accumulation, not a steady-state requirement.** Our development machine's
  MySQL data directory currently holds **19 GB**, of which **12 GB is binlog** — accumulated over
  dozens of ETL runs. A fresh install has none of it, and `PURGE BINARY LOGS` reclaims it.
  Sizing capacity by "how big is the directory" will overestimate severely.
- **A rebuild needs temporary-file space.** Full-ETL temp files land in `tmpdir` (below) and peak at
  several GB. When space runs out the ETL does not exit gracefully; it dies part-way through.

Usage inside the database is highly concentrated: `search_index` alone accounts for 2.0 GB (about
75% of the database), followed by `gene_sequence_link` at 0.32 GB and `evidence` / `enzyme` at
0.12 GB each; the remaining eleven tables together come to under 0.2 GB.

### 3.2 MySQL configuration

Two settings **must be set explicitly** — do not rely on defaults:

```ini
datadir=<DATA_DISK>/mysql-data       # e.g. D:/mysql-data on Windows, /data/mysql-data on Linux
tmpdir=<DATA_DISK>/mysql-data/tmp    # must be explicit, see below
innodb_buffer_pool_size=2G
innodb_buffer_pool_instances=1
```

The only hard rule is **neither directory may sit on the system drive** — not that they sit on any
particular disk. Two reasons:

- With `tmpdir` unset, MySQL puts temporary files in the system drive's user temp directory. A full
  ETL generates several GB of temp files; once the system drive fills, the ETL dies part-way through
  with `OS errno 28 - No space left on device`, having done half the work.
- A full ETL also produces about 4 GB of binlog per run. The binlog follows `datadir` (`log-bin` is
  configured relative), so moving `datadir` to the data disk moves binlog growth there too — which
  is the desired effect.

> On Windows, if `datadir` starts on the system drive, moving it needs administrator rights and
> coordinated changes to both `my.ini` and the directory ACLs. Set it right after installing MySQL
> rather than moving it later.

### 3.3 Python dependencies

The collection stage and the query stage deliberately use **two different database driver chains**
(synchronous vs. asynchronous):

| Location | Dependencies |
|---|---|
| `backend/requirements.txt` | `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, **`aiomysql`**, `pydantic` v2, `pydantic-settings`, `python-dotenv`, `httpx`, `openpyxl`, **`rdkit`** |
| `etl/requirements.txt` | `pandas`, `sqlalchemy`, **`pymysql`**, `requests` |
| `update_tool/requirements.txt` | `requests`, **`rdkit`** |

> **The two driver chains are not interchangeable.** `backend/requirements.txt` does not carry
> `pymysql`, and `etl/requirements.txt` does not carry `aiomysql`. Installing one does not let you
> run the other: the backend uses `create_async_engine` (hence `+aiomysql`) while the ETL uses
> `create_engine` (hence `+pymysql`).
>
> `etl_enzymes.py` calls the UniProt REST API to fill in fields missing from the local files, so
> the ETL is not a fully offline step and needs `requests` as well. `update_tool/` needs it for the
> same reason — every fetch step there talks to a remote API.
>
> `rdkit` is needed offline by `update_tool/`: `build_all_nodes.py` computes
> `compound.inchi_key_derived` from each compound's SMILES. **The backend now carries it as well**, for
> one route — `GET /api/v1/bundle/compound?smiles=` converts an incoming SMILES to an InChIKey
> server-side (`app/utils/chemistry.py`), so a caller holding a SMILES string rather than a Ketcher
> drawing can still reach a compound. It is the same conversion that produced `inchi_key_derived`, so
> the two must stay on the same rdkit version. The **ETL still needs nothing**: the derived key is
> computed at generation time and stored, and every other query path is plain string matching.

### 3.4 External program: NCBI BLAST+

Sequence homology search is not a remote service call — it runs a **local NCBI BLAST+ subprocess**.
Download BLAST+ from NCBI, unpack it, and tell the backend where the executables are. **That
directory differs per machine, so it must be given explicitly:**

- The default is `blast_bin/` relative to the backend's launch directory (we launch from `backend/`,
  so the default resolves to `backend/blast_bin/`).
- Point `IGEM_BLAST_BIN_DIR` anywhere else if you prefer. When the files are missing the backend
  raises a clear error rather than failing silently.
- The executables are **not in this repository**. A BLAST+ build is several hundred MB unpacked and
  is tied to one platform, so carry the download link rather than the binaries and fetch the build
  for your own OS. Both layouts are accepted — an unpacked NCBI archive (executables in a nested
  `bin/`) and a flat directory of executables.
- **Keep the `LICENSE` file from the archive.** It sits at the top level of the NCBI download, beside
  the `bin/` directory. You do not need it to *run* BLAST+ — the software is a U.S. Government work
  and carries no copyright — but if your project redistributes BLAST+ in any form, that file is the
  notice you are expected to pass along, and the archive's own copy is the authoritative one. It is
  easy to lose: extracting only `bin/` (which is what a "point the backend at the executables"
  instruction invites) drops it, and then you are left paraphrasing terms you no longer hold.
  [`NOTICE`](../../NOTICE) records what NCBI's terms say as of 2026-10-02.

The work directory (FASTA and `makeblastdb` output) defaults to `blast_work/` relative to the launch
directory, overridable with `IGEM_BLAST_WORK_DIR`.

- The subject set is every enzyme that has a sequence, plus any isoform whose sequence differs from
  its canonical sequence.
- `makeblastdb` formats once and is then cached and reused, keyed by a signature of the subject set
  (coverage + IDs + lengths).

> **Non-ASCII paths.** The BLAST+ index cannot be written under a path containing non-ASCII
> characters. If the work directory's resolved path contains any (for example, a Windows username
> in Chinese), the backend **falls back to the system temp directory automatically**. Functionality
> is unaffected, but the index cache cannot be reused across restarts.

### 3.5 Configuration and environment variables

Every backend setting uses the **`IGEM_`** prefix via `pydantic-settings`
(`backend/app/config.py`), read from `backend/.env` or from the environment:

| Variable | Default | Meaning |
|---|---|---|
| `IGEM_DB_HOST` | `localhost` | MySQL host |
| `IGEM_DB_PORT` | `3306` | MySQL port |
| `IGEM_DB_USER` | `root` | MySQL user |
| `IGEM_DB_PASSWORD` | *(empty)* | MySQL password |
| `IGEM_DB_NAME` | `igem_terpene` | Database name |
| `IGEM_BLAST_BIN_DIR` | `blast_bin` | BLAST+ executables |
| `IGEM_BLAST_WORK_DIR` | `blast_work` | BLAST+ working files |

The ETL reads the **same `IGEM_DB_*` names** but has its own config (`etl/config.py`) and, unlike
the backend, **reads only environment variables — never `.env`**. It adds `IGEM_DATA_DIR`, which
points at a different `for_*/` tree — used for **sandbox validation**: verify invariants such as
idempotency and identifier stability against a scratch database before touching the real one.

> ⚠️ **Never commit real credentials.** `backend/.env` holds a live database password in plaintext.
> It must be listed in `.gitignore` and must not be published with the repository.

---

## 4. Data sources

### 4.1 Primary source: UniProt

Downloaded through the `rest.uniprot.org` REST interface with **one search term: `(terpene)`**.

The single query is a deliberate design choice, not a simplification. Splitting it into separate
"reviewed" and "unreviewed" downloads would place the two halves in **two different time windows**;
an entry promoted or demoted across the boundary would then land in both segments, or in neither.
With one query the whole dataset is **a single snapshot**, and the split happens locally on the
`Reviewed` column.

| Segment | Rows (2026-09-20) |
|---|---|
| `swiss_prot` (reviewed) | 1,535 |
| `trembl` (unreviewed) | 94,334 |
| **Total** | **95,869** |

The two segments sum to the count UniProt reports for the query — nothing is dropped.

### 4.2 Supplementary sources

The principle is **to add only what the export does not already contain**. Anything parseable
offline from UniProt export columns is parsed offline — offline parsing is also more reproducible.

| Source | Access method | What it provides |
|---|---|---|
| **Rhea** | SPARQL (`sparql.rhea-db.org`) + RDF | Reaction equations, physiological direction, EC numbers, reaction SMILES |
| **ChEBI** | EBI FTP flat files (~125 MB across four archives) | Local SMILES / InChIKey mapping, avoiding per-record lookups. `structures.tsv.gz` (92 MB of the total) supplies the official `standard_inchi_key`. |
| **UniProt export columns** | Offline parse, no API calls | GO annotations, isoform sequences, references (PubMed), nucleotide accessions |
| **DDBJ** | `getentry.ddbj.nig.ac.jp` | Nucleotide sequence links |
| **Manual curation** | In-repo `update_tool/chebi_data/curation_overrides.tsv` | Human corrections to automatic matching |

External links (NCBI, EBI, PubMed, DOI) are used only to generate outbound links; they are
not stored in the database.

### 4.3 Measured dataset size (2026-09-20)

| Item | Count |
|---|---|
| Enzymes | 95,869 (1,535 Swiss-Prot + 94,334 TrEMBL) |
| Reactions | 714 |
| Compounds | 734 |
| Enzyme–reaction edges | 20,616 |
| Search-index rows | 3,756,596 |

---

## 5. The load stage (TSV → MySQL)

### 5.1 Entry point and stages

The loader is `etl/etl_run.py`. Run it from inside `etl/`:

```bash
cd etl && python -u etl_run.py           # full load
cd etl && python -u etl_run.py --source=swiss_prot   # reload one source
```

`-u` disables Python output buffering; without it the log accumulates in memory and the run appears
to hang. The only other accepted flag is `--help`; unknown flags exit non-zero. **There is no
`--dry-run` in `etl/`** — a dry run exists only in the upstream collector, `update_tool/update_database.py`
(step 6 of §9).

Six stages run in a fixed order, followed by (or preceded by) maintenance work:

| Stage | Module | Writes |
|---|---|---|
| 0 (pre) | `etl_edges.snapshot_edge_ids()` | In-memory snapshot of existing edge IDs |
| 0 (pre) | `etl_run.purge_source()` | Deletes the target source's rows, child tables first |
| 1/6 | `etl_compounds` | `compound` |
| 2/6 | `etl_enzymes` | `enzyme`, `enzyme_id_map` |
| 3/6 | `etl_reactions` | `reaction`, `reaction_compound` |
| 4/6 | `etl_edges` | `enzyme_reaction_edge` |
| 5/6 | `etl_master` | `gene`, `gene_sequence_link`, `evidence`, `enzyme_go`, `enzyme_isoform`, `enzyme_solubility_score` |
| 6/6 | `etl_search_index` | `search_index` |

Stage 5 is itself ordered, and the order matters: enzyme master → gene info → sequence links →
evidence → GO terms → **isoforms → solubility scores**. Isoforms must precede solubility, because
the solubility loader resolves isoform identifiers against the isoform table.

### 5.2 Input contract

The loader reads only disk TSVs; it never hits the network, with one exception: a UniProt HTTP 302
lookup to resolve retired accessions (`etl_enzymes._resolve_redirect`).

Segmentation is by filename suffix: `<relative_path>.<source>.tsv`, built by
`etl/sources.segmented_path()`. A missing segment is a hard `FileNotFoundError`. Three merge tables
are read unsuffixed and cannot be split by source (see §10).

### 5.3 Idempotency and stable identifiers

Re-running is structurally safe: the loader **purges a source and reloads it**, rather than
truncating everything.

- **Purge order** is child-first: `search_index` → `enzyme_solubility_score`, `enzyme_isoform`,
  `enzyme_go`, `evidence`, `gene_sequence_link`, `gene` → `enzyme_reaction_edge` → `enzyme`.
- **`enzyme_id` is never recycled.** IDs come from `enzyme_id_map`, which records the identifier a
  UniProt accession first received and is never deleted from — a retired entry is only flagged with
  `retired_at`. Allocating a freed identifier to a different entry would silently repoint every
  external link that used it (§10).
- **`edge_id` is reused** by snapshotting the existing edge IDs *before* the purge.

Documented gotchas:

- **Purge and load are not one transaction.** A crash leaves a recoverable half-state; re-running
  fixes it.
- **In `--source=<s>` mode, `reaction` is insert-only and never updated**, because a shared Rhea
  reaction can have a different direction in each segment. That mode also skips `retired_at`
  maintenance and retired-accession resolution — it needs the full view to do either correctly.
- **The upsert helper returns its input row count, not rows written.** A rerun that logs the same
  row count with "0 new rows" is expected and correct.
- **`search_index` rows are purged selectively.** Only rows carrying an `enzyme_id` are deleted with
  their source. Rows *without* one are compound/entity-level and are source-independent — the same
  compound is the same compound regardless of where it came from — so they are upserted separately
  instead. Purging them by source would make them accumulate a duplicate copy on every refresh,
  because they would never match the delete predicate again.

---

## 6. Database schema

`schema.sql` creates the database and **15 tables**. It is the single source of truth for DDL — the
loader extracts individual `CREATE TABLE` statements from it at runtime. Do not hand-create tables.

| Table | Contents |
|---|---|
| `enzyme` | Enzyme master: UniProt ID, sequence, source type (swiss_prot / trembl) |
| `compound` | Compounds: SMILES / InChI / InChIKey (official + derived) / ChEBI ID |
| `reaction` | Reactions: Rhea ID, equation, direction, EC number |
| `reaction_compound` | Substrate / product links with a `role` |
| `enzyme_reaction_edge` | Enzyme–reaction edges (the graph itself) |
| `gene` / `gene_sequence_link` | Genes and nucleotide sequence links |
| `enzyme_go` / `enzyme_isoform` / `evidence` | GO annotations, isoforms, references |
| `enzyme_solubility_score` | Solubility model scores — see §8 |
| `pathway_cache` | Backend runtime cache; the ETL never touches it |
| `search_index` | Search index (3,756,596 rows measured) |
| `enzyme_id_map` / `enzyme_alias_map` | Identifier-stability tables, see §10 |

**Required index.** `idx_search_index_value_prefix (field_value(64))` on `search_index`. Without it,
search is roughly 500× slower. This is why the schema must be applied from `sql/schema.sql`.

Not every table is written by the loader. `enzyme_alias_map` is **read-only** from the ETL's side —
no `INSERT` for it exists anywhere in this repository; it is populated externally. `pathway_cache` is
created by the schema but is used only by the backend at runtime.

---

## 7. Running the web application

The repository ships no single launcher script. The service is started in two terminals. MySQL must
be up first.

**Backend** (from `backend/`, with the database password provided):

```bash
uvicorn app.main:app --port 8000
```

Interactive API documentation is then at `http://localhost:8000/docs`. Everything is served under
the `/api/v1` prefix. Responses use a uniform envelope: `{success, data, meta?, error?}`.

Router groups: `metadata`, `graph`, `search`, `structure_search`, `enzymes`, `compounds`,
`reactions`, `blast`, `download`, `assets`.

**Frontend** (from `frontend/`):

```bash
npm install
npm run dev        # dev server, default http://localhost:5173
npm run build      # tsc -b && vite build  →  dist/
```

The frontend requests `/api/v1` as a relative path and relies on a proxy: in development, Vite
proxies `/api` to `VITE_BACKEND` (default `http://127.0.0.1:8000`); in production, put a `/api/`
proxy rule and an SPA fallback (`try_files ... /index.html`) in front of `dist/`.

> **After restarting MySQL you must restart the backend.** Every connection in the pool goes stale
> and every request returns 500 until it is restarted.

### 7.1 Chemical informatics — where it runs, and where it does not

**Almost nothing in the serving path is computed from structure.** Structures are **pre-generated
SVG** (`/assets/compounds/{chebi_id}/structure.svg`,
`/assets/reactions/{rhea_id}/atom-map.svg`), and structure search is **exact InChIKey matching** —
**not substructure search**. RDKit is the one exception, and it runs in exactly two places, both of
which only ever turn a structure into a key:

- **Offline, in `update_tool/`**, to precompute the `inchi_key_derived` column (see below).
- **On the backend**, for `GET /api/v1/bundle/compound?smiles=`. A caller who has a SMILES string but
  no InChIKey would otherwise have no entry point: the drawing UI produces keys only from mouse
  input. The backend converts the SMILES with RDKit (`app/utils/chemistry.py`) and then takes the
  same two-column match described below. The response is identical in shape to
  `GET /api/v1/bundle/compound/{compound_id}` — SMILES is just a different way of *naming* the
  compound — and `data.entityId` reports what it resolved to. A SMILES that cannot be parsed, or one
  describing a generic / R-group structure (which has no InChIKey at all), is rejected as an error
  envelope rather than a 500.

The ETL and the browser bundle carry no cheminformatics library. The web UI computes its keys in the
browser, inside Ketcher (Indigo/WASM), and calls `GET /api/v1/ketcher/search?inchikey=`.

Matching is against **two columns**, `compound.inchi_key` (ChEBI's official `standard_inchi_key`)
**or** `compound.inchi_key_derived` (RDKit, computed at generation time from the row's own SMILES).
The two disagree on exactly two compounds (`CHEBI:231826`, `CHEBI:53643`) where ChEBI's own record
is self-inconsistent — its `smiles` and its `standard_inchi` describe different stereoisomers.
Ketcher, fed the stored SMILES, reproduces the *derived* key, and so does the backend's RDKit on the
`?smiles=` route, which is how those two stay reachable from either entry point. Only the official
column is ever returned in API responses.

So our structure search can find *the same compound*, but not *structurally similar compounds*.
Please keep that boundary in mind when citing this feature.

---

## 8. The solubility score

This section exists because the score is the most easily misread number in the interface.

### 8.1 What the score is — and is not

**It is a model reference score. It is not a solubility label, and it is not a calibrated
probability.** Concretely:

- The database contains **no solubility labels at all**, so no threshold can be fitted or validated
  on it. Any threshold applied to these scores is an imported assumption, not a result of this work.
- Benchmarked thresholds do not transfer. The optimal threshold from each of three benchmark sets
  selects wildly different fractions of our library — **a 28× spread** (0.35 selects 27.7% of the
  library; 0.71 selects 1.0%).
- In the interface we therefore label it a **model reference score**, never "soluble".

### 8.2 Provenance and how the value enters the database

The score is computed with the open-source tool **DeepSolNet** using its original weights. Upstream
of that, DeepSolNet requires the protein language model **ESM C 300M** (see §12.2 for the licensing
of both).

The producing run is **not part of this repository**. What is in the repository is the resulting
per-enzyme table, converted to the same shape as the other child tables:

```
for_enzyme_detail/child_tables/uniprotkb_solubility_score.tsv          (legacy merged copy — the loader does NOT read this one)
for_enzyme_detail/child_tables/uniprotkb_solubility_score.swiss_prot.tsv
for_enzyme_detail/child_tables/uniprotkb_solubility_score.trembl.tsv
```

Columns consumed by the loader:

| # | Column | Meaning |
|---|---|---|
| 1 | `Entry` | UniProt accession — the join key |
| 2 | `Isoform_ID` | Empty = canonical row; otherwise an isoform accession (e.g. `P0DI77-2`) |
| 3 | `DeepSolNet Score` | Continuous value in [0, 1], **no threshold applied** |
| 4 | `Membrane` | `membrane` / `non-membrane` / `unannotated` — see §8.3 |
| 5 | `Membrane Evidence` | The raw UniProt annotation text, so any call can be audited |
| 6 | `Sequence Length` | Residue count, used for stratification and QA |
| 7 | `Source` | `swiss_prot` / `trembl` (segment files only) |

The loader (`etl_master.load_solubility_scores`) goes through `read_segmented()`, so it reads the
**two segment files and ignores the unsuffixed copy**. It runs as the last sub-step of stage 5 and
writes `enzyme_solubility_score`:

| Column | Type | Note |
|---|---|---|
| `solubility_record_id` | PK | |
| `enzyme_id` | FK → `enzyme` | Resolved through `uniprot_id` |
| `isoform_id` | nullable | NULL = canonical row |
| `deep_solnet_score` | `DECIMAL(7,6)` | Raw model output |
| `membrane` | `VARCHAR(20)` | Three-state, see §8.3 |
| `membrane_evidence` | `VARCHAR(1024)` | |
| `sequence_length` | | |

Rows are filtered on the way in: canonical rows are always kept; isoform rows are kept **only when
the isoform's sequence actually differs from its canonical sequence**. Rows with an unparseable
score, or with no matching enzyme, are skipped and counted.

> **Note on file format.** The table files contain **no comment lines** — the ETL reads them with
> `csv.DictReader`, which would treat a `#` line as data. The model's provenance is therefore encoded
> in the *column name* (`DeepSolNet Score`), not in a file header. Values are ASCII lowercase
> hyphenated, matching existing enum styles in the database.

### 8.3 The membrane column: `unannotated` is not `non-membrane`

Derived from UniProt's `ft_transmem` and `cc_subcellular_location`:

| Value | Criterion | Share |
|---|---|---|
| `membrane` | `TRANSMEM` present, or `SUBCELLULAR LOCATION` contains "membrane" | 18.06% |
| `non-membrane` | Has an SL annotation, no `TRANSMEM`, SL does not contain "membrane" | 1.35% |
| `unannotated` | Both empty — **UniProt does not say** | 80.59% |

This column is **high precision, low recall**: what it flags can be trusted, but it covers under a
fifth of the library. The remaining four fifths are undetermined and **must not be read as
soluble**.

### 8.4 Score distribution

| Statistic | Value |
|---|---|
| Minimum | 0.0112 |
| Median | 0.2754 |
| Maximum | 0.9914 |
| Percentile of 0.5 | **92.5th** |

A score of 0.5 sitting at the 92.5th percentile is the clearest single argument for why the raw
number must not be read as a probability.

---

## 9. Full reproduction, step by step

Seven steps. Use this for a rebuild from fresh data; to refresh a single source, re-run only steps
4, 6, and 7.

**All commands assume the repository root as the working directory** (step 7 changes into `etl/`
itself). Steps 3, 4, and 5 require network access.

| Step | Command | Time (measured) |
|---|---|---|
| 0 | Configure MySQL `datadir` / `tmpdir` / buffer pool (§3.2) | Once per machine |
| 1 | `mysql -u root -p < sql/schema.sql` | Seconds |
| 2 | Install the three dependency sets from §3.3 | Minutes |
| 3 | `python update_tool/download_uniprot.py` | A few minutes |
| 4 | `python update_tool/run_all.py --source=swiss_prot`<br>`python update_tool/run_all.py --source=trembl` | ~9 min<br>~2.5–3 h |
| 5 | `python update_tool/run_all.py --merge` | A few minutes |
| 6 | `python update_tool/update_database.py --source=swiss_prot --dry-run`<br>(repeat for `trembl`, then for `--merge`; confirm the report, then drop `--dry-run`) | Seconds |
| 7 | `cd etl && python -u etl_run.py` | ~41 min |

Notes:

- In step 4 the two sources **do not overwrite each other**; order does not matter, but both must
  finish before step 5 (`--merge` checks).
- Step 5's three merge tables (substrate–product pairs / compounds / graph nodes) **cannot be split
  by source**: a substrate→product pair is naturally shared by enzymes from both sources.
- Step 6 is **three commands, not one**. Before overwriting, it automatically backs up to a
  timestamped directory.
- Step 7's `-u` disables Python output buffering, as above.
- **Steps 6 and 7 read connection settings differently.** The ETL reads *only* environment variables
  and never `.env`, so step 7 requires `export IGEM_DB_PASSWORD=...` in the current shell
  (`set` on Windows `cmd`). The backend, by contrast, can read `backend/.env`.
- `python` means whichever interpreter you use — substitute `python3` or a virtualenv as needed.
- **Each step has different dependencies:** steps 3–6 run `update_tool/` (needs
  `update_tool/requirements.txt`), step 7 needs `etl/requirements.txt`, and only serving the web app
  needs `backend/requirements.txt`. See §3.3.

---

## 10. Invariants and design decisions

These are the reasons to trust the data; they are also what most of the design effort went into.

**Enzyme identifiers are never recycled.** `enzyme_id_map` records the identifier each UniProt
accession first received. When an entry disappears from a new data release, the row is only flagged
with `retired_at`, never deleted. The table deliberately has no foreign key and never participates in
`DELETE` or `TRUNCATE`. If identifiers were recycled and reassigned to a different entry, every
external link using that identifier would silently point at the wrong enzyme.

**Identifiers survive renumbering.** When UniProt merges entries it demotes the old accession to
secondary. `enzyme_alias_map` carries the identifier across to the same biological entity, so an
enzyme is not treated as new — and does not get a new number — merely because its accession changed.

**Replacement is per-source, not whole-table.** Data updates use "source" as the minimum unit of
replacement. A missing segmented file is a hard error, for the asymmetry given in §1.

**Column widths are dynamic.** Some columns are column families — one enzyme to many nucleotide
accessions, many enzymes, many references — whose width grows with the data. The loader derives
width from the header rather than hard-coding a ceiling; with a hard-coded ceiling, anything beyond
it would not error, it would simply not exist.

---

## 11. Known limitations

1. **Structure search is exact matching only** — no substructure or similarity search (§7.1).
2. **`update_tool/` scripts must be driven through `run_all.py`.** Their default input paths point
   at filenames that never shipped (`../uniprotkb_terpene_AND_reviewed_true_2026_07_*.tsv`); only
   `run_all.py` passes the real paths, so running a fetch script standalone fails.
3. **A rebuild takes hours.** The TrEMBL networked steps run ~2.5–3 h and depend on external API
   availability. Networked steps use a resumable cache, so an interrupted run can continue.
4. **Data drifts with UniProt versions.** The counts in this document are a 2026-09-20 snapshot;
   a fresh download will differ.
5. **The backend must be restarted after MySQL restarts**, or every request returns 500 (§7).
6. **The solubility score is a model reference score**, not a calibrated probability, and no
   threshold in this database is validated against labels (§8).
7. **A SMILES containing `*` resolves to nothing, on any route.** Such a string describes an R-group
   or generic structure rather than a molecule, so it has no InChIKey and no cheminformatics toolkit
   can produce one. Exactly 20 of the 730 rows carrying a SMILES are of this kind (which is also why
   only 710 rows have an official key), and they are unreachable by structure lookup (§7.1).

---

## 12. Licenses and attribution

### 12.1 Our own code and derived data

We license our own work under two licenses, split along the code / data line:

| Part | License | Full text |
|---|---|---|
| Our source code — `backend/`, `frontend/`, `etl/`, `update_tool/`, `tools/`, `sql/` | **Apache License 2.0** | [`LICENSE`](../../LICENSE) |
| Our derived tables — `for_*/` — and our documentation — `docs/`, the `.docx` design notes, the `.png` images, the JSON data files | **Creative Commons Attribution 4.0 International (CC BY 4.0)** | [`LICENSE-DATA`](../../LICENSE-DATA) |

Two licenses rather than one because these are different kinds of work. The code gets a
software license with an explicit patent grant; the data gets the same license its upstream
sources use, which keeps attribution consistent from one end of the pipeline to the other.

**Under CC BY 4.0 you may share and adapt the data — including commercially — provided you
give appropriate credit, link to the license, and indicate if changes were made.** The credit
lines our upstream sources require are collected in [`NOTICE`](../../NOTICE), which doubles as
the attribution statement for our own CC BY 4.0 material.

If you use the database, cite us as described in §12.5.

### 12.2 The solubility model

Two separate components are involved, and they have different upstream terms.

**DeepSolNet** (the scoring tool). Its upstream repository declares the MIT License in its README,
but **as of 2026-10-01 it does not include a `LICENSE` file** — GitHub's license API returns 404 for
the repository and no license file is present. The declared intent is clearly permissive; what is
absent is the formal grant, not the permission in spirit. We record this for transparency and do
not treat it as an obstacle to our use of the tool.

**ESM C 300M** (the protein language model that DeepSolNet builds on). This was originally released
by EvolutionaryScale under the **Cambrian Open License Agreement**, which permits commercial use but
imposes attribution conditions — prominently displaying "Built with ESM", titling derivative works
with a leading "ESM", and including a NOTICE statement. **That is no longer the current state.**
The project has moved to Chan Zuckerberg Biohub and the models are now **MIT-licensed and ungated**:
`Biohub/esm` ships an MIT `LICENSE.md` (Copyright 2026 Chan Zuckerberg Biohub, Inc.), its README
states "These models are available under the MIT license", and the Hugging Face weights are marked
`mit` with `gated: false`. The old `EvolutionaryScale/esmc-300m-2024-12` model repository now
redirects to `biohub/esmc-300m-2024-12`.

Much documentation on the web still describes the Cambrian terms, and the DeepSolNet README still
points at the old download location. **We verified the current terms against the upstream sources
on 2026-10-01**; if you are reusing this pipeline, re-check them yourself rather than trusting a
search result.

What remains is Biohub's Acceptable Use Policy, which the model README asks users to follow and
which restricts harmful biological/chemical uses — it does not restrict commercial use.

### 12.3 Upstream data sources

| Source | License |
|---|---|
| **UniProt** | CC BY 4.0 (UniProt Consortium) |
| **Rhea** | CC BY 4.0 (SIB / EMBL-EBI) |
| **ChEBI** | CC BY 4.0 (EMBL-EBI) |
| **DDBJ** (via INSDC) | **No use or redistribution restrictions.** We store outbound links only — see below |
| **NCBI BLAST+** | Public-domain software from NCBI. **Not redistributed here** — users download it themselves (§3.4) |

Attribution requirements apply under CC BY 4.0: credit the respective consortium.
[`NOTICE`](../../NOTICE) carries the exact credit lines.

**On DDBJ.** INSDC's policy is that the partner databases (DDBJ / ENA / GenBank) do not attach
conditions restricting the use of public nucleotide sequence data, and DDBJ states that it holds
no copyright restricting use or redistribution. The database also does not store DDBJ records —
it stores accession numbers and builds outbound links from them (§4.2), so no sequence data is
redistributed here in any case.

### 12.4 Bundled third-party software

One third-party program ships inside this repository: the structure editor **Ketcher**.

`frontend/public/ketcher_standalone/` is a **prebuilt Ketcher v3.18.0 standalone distribution**
(EPAM Systems, **Apache License 2.0** — https://github.com/epam/ketcher). We ship the upstream build
as we received it. **We changed nothing in it**: apart from the two licence files described below,
every file still carries the timestamp it had when the archive was unpacked.

Apache License 2.0 §4 attaches four obligations to redistributing the work. One of them is not
triggered here; the other three are met by files that sit next to the bundle.

| §4 | Requirement | How it is met |
|---|---|---|
| (a) | Give recipients a copy of the License | [`ketcher_standalone/LICENSE`](../../frontend/public/ketcher_standalone/LICENSE) — EPAM's own copy, appendix and all |
| (b) | Mark modified files | **Not triggered** — we modified none |
| (c) | Retain the work's copyright and attribution notices | The build's `*.LICENSE.txt` sidecars, kept in place — see below |
| (d) | Pass on the notices in the work's `NOTICE` file | [`ketcher_standalone/NOTICE`](../../frontend/public/ketcher_standalone/NOTICE) |

**Those files are not decoration, and the two sideways ones are easy to mistake for clutter.**

The `NOTICE` file was **not** part of the build we received. We fetched it from the EPAM repository;
it is byte-for-byte identical to the file at upstream tag `v3.18.0`, which is the version we ship.

The five `*.LICENSE.txt` sidecars are load-bearing in a less obvious way. Each bundled JavaScript
file ends with a webpack banner that points at one of them:

```
/*! For license information please see main.e47c48ad.js.LICENSE.txt */
```

That pointer exists *because* the sidecar exists; remove the sidecar and the pointer dangles. The
sidecars are also **not a complete inventory**: core-js 3.36.0 (MIT) is bundled, but its author wrote
no `/*!` block, so webpack had nothing to extract and only the licence URL survives inside the
minified code. They record what the bundler found, not everything that is present.

Finally, Ketcher's `NOTICE` lists **thirteen licence families** that its dependencies may fall under,
including LGPL-2.1, the Mozilla Public License and the Academic Free License. Searching the whole
116 MB bundle for those strings returns exactly one hit: the `NOTICE` file itself. Every bundled
library whose attribution we can locate is permissive — Miew and Three.js, lodash, the React
packages, object-assign, buffer, regenerator-runtime, url-search-params-polyfill, crc32.js and
core-js. That thirteen-family list is inherited from a wider project rather than being an inventory
of this build. The caveat is that a library shipping no notice at all would leave no trace, so this
is evidence rather than proof.

**Verified against upstream on 2026-10-05**: the version is read from the bundle's own `index.html`;
`NOTICE` and `LICENSE` were compared against upstream tag `v3.18.0`.

### 12.5 How to cite us

If you use this database, please cite our project and the upstream sources in §13.

---

## 13. Citations

- UniProt Consortium — https://www.uniprot.org
- Rhea — https://www.rhea-db.org
- ChEBI — https://www.ebi.ac.uk/chebi
- DDBJ — https://www.ddbj.nig.ac.jp
- NCBI BLAST+ — https://www.ncbi.nlm.nih.gov
- ESM / ESMC — https://github.com/Biohub/esm
- DeepSolNet — https://github.com/wangxinglong1990/DeepSolNet
- Ketcher (bundled with the frontend) — https://github.com/epam/ketcher

> **This section is not legal advice.** We are documenting what the upstream terms say, as we
> understand them, as of the dates given. Read the licenses themselves before relying on any of it.
