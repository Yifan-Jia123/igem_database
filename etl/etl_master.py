"""ETL Step 5: enzyme sequence backfill + child tables (gene / sequence_link /
evidence / go / isoform).

## Replaced by source (plan Phase 2.1, C class)

All 5 child tables **have an `enzyme_id` foreign key but no `source_type` column**.
Each enzyme_id deterministically belongs to one source, so they can still be replaced
by source -- the criterion is "does it have an enzyme_id FK", not "does it have
source_type".

The original implementation did **5 unconditional `DELETE FROM <table>` (full-table
wipes)** here: refreshing TrEMBL also wiped SwissProt's child-table rows, breaking
source isolation outright. Source-scoped deletion has been centralised into
etl_run.purge_source(); this module only writes.

## The single source of DDL = sql/schema.sql

The original embedded the DDL for 3 tables in this file, duplicating `sql/schema.sql` --
miss one place when the schema changes and the local DDL drifts from schema.sql.
Changed to fetch it from schema.sql.
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL  # noqa: E402
from db_utils import schema_ddl  # noqa: E402
from sources import (columns_of, indexed_groups, read_segmented,  # noqa: E402
                     safe_usecols)

engine = create_engine(DB_URL)

MASTER_FILE = 'for_enzyme_detail/uniprotkb_master.tsv'
REFERENCES_FILE = 'for_enzyme_detail/child_tables/uniprotkb_references.tsv'
SEQ_LINKS_FILE = 'for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv'
GO_FILE = 'for_enzyme_detail/child_tables/uniprotkb_go.tsv'
ISOFORM_FILE = 'for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv'
SOLUBILITY_FILE = 'for_enzyme_detail/child_tables/uniprotkb_solubility_score.tsv'

# The evidence table has no source_type, but its source is decided by enzyme ->
# review status follows the enzyme.
SOURCE_TO_REVIEW = {'swiss_prot': 'official', 'trembl': 'pending'}

EVIDENCE_COLUMNS = {
    "title": "TEXT",
    "authors": "TEXT",
    "journal": "VARCHAR(300)",
    "volume": "VARCHAR(80)",
    "pages": "VARCHAR(80)",
    "publication_year": "INT",
    "reference_type": "VARCHAR(120)",
    "positions": "TEXT",
    "url": "VARCHAR(500)",
}


def _enzyme_source_map():
    """enzyme_id -> source_type. Child tables use it to determine their source;
    review status follows the source too."""
    df = pd.read_sql("SELECT enzyme_id, source_type FROM enzyme", engine)
    return {str(r.enzyme_id): str(r.source_type) for r in df.itertuples()}


def _clean_value(value):
    if pd.isna(value):
        return None
    text_value = str(value).strip()
    if not text_value or text_value == "-":
        return None
    return text_value


def _clean_int(value):
    value = _clean_value(value)
    if not value:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_clean(row, columns):
    for column in columns:
        value = _clean_value(row.get(column))
        if value:
            return value
    return None


def _ensure_sequence_link_table():
    with engine.connect() as conn:
        conn.execute(text(schema_ddl('gene_sequence_link')))
        conn.execute(text("ALTER TABLE gene_sequence_link MODIFY COLUMN link_category VARCHAR(80) NOT NULL"))
        conn.commit()


def _ensure_columns(table_name, columns):
    with engine.connect() as conn:
        for column_name, ddl in columns.items():
            exists = conn.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "AND table_name = :table_name AND column_name = :column_name"
                ),
                {"table_name": table_name, "column_name": column_name},
            ).scalar()
            if not exists:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
        conn.commit()


def _ensure_table(ddl):
    with engine.connect() as conn:
        conn.execute(text(ddl))
        conn.commit()


def update_enzyme_from_master(only=None):
    """Backfill sequence / length / mass from the master wide table into the enzyme
    table.

    The original issued one UPDATE per row, and the statement varied by row (different
    rows updated different columns) -- 96k rows meant 96k statements. Changed to one
    unified statement + executemany, with COALESCE so a "no value this run" does not
    overwrite an existing value.
    """
    # Read only the columns actually needed: master is a **dynamically wide** table
    # (measured SP 141 columns / TrEMBL 473 columns, and the count may grow further at
    # the full 96k rows), while this step uses only Entry + the four sequence/length/
    # mass columns. Parsing all columns at 96k x 473 peaks at GB scale, and only 5
    # columns are wanted here.
    spec_cols = ["Entry", "Canonical Sequence", "Sequence Length", "Canonical Length", "Canonical Mass"]
    have = columns_of(MASTER_FILE, only=only)
    wanted = [c for c in spec_cols if all(c in names for names in have.values())]
    if "Entry" not in wanted or "Canonical Sequence" not in wanted:
        # When the "expected sequence column name" is missing, it falls through to the
        # column-name guess below (scanning all columns for a sequence-like one), which
        # requires reading all columns.
        wanted = None
    df = read_segmented(MASTER_FILE, only=only, dtype=str,
                        **({'usecols': wanted} if wanted else {}))

    # Use DB enzyme mapping
    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    seq_col = "Canonical Sequence" if "Canonical Sequence" in df.columns else None
    len_col = "Sequence Length" if "Sequence Length" in df.columns else None
    if not len_col and "Canonical Length" in df.columns:
        len_col = "Canonical Length"
    mass_col = "Canonical Mass" if "Canonical Mass" in df.columns else None

    if seq_col is None:
        for col in df.columns:
            sample = df[col].dropna().head(5)
            if len(sample) > 0:
                vals = sample.astype(str)
                if vals.str.len().mean() > 50 and vals.str.match(r'^[A-Z*]+$').all():
                    seq_col = col
                    break

    if seq_col is None:
        print("  enzyme update: sequence column not found, skipping")
        return

    params = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        seq = str(row[seq_col]) if pd.notna(row.get(seq_col)) else None
        length = _clean_int(row.get(len_col)) if len_col else None
        mass = None
        if mass_col and pd.notna(row.get(mass_col)):
            try:
                mass = float(str(row[mass_col]).replace(",", ""))
            except (ValueError, TypeError):
                mass = None

        if seq or length is not None or mass is not None:
            params.append({"enzyme_id": enzyme_id, "sequence": seq,
                           "length": length, "mass": mass})

    if not params:
        print("  enzyme (sequence update): 没有可回填的行")
        return

    # COALESCE: a NULL this run leaves the original value alone -- SET sequence =
    # :sequence would blank rows that lack the column.
    sql = text(
        "UPDATE enzyme SET "
        "sequence = COALESCE(:sequence, sequence), "
        "length   = COALESCE(:length, length), "
        "mass     = COALESCE(:mass, mass) "
        "WHERE enzyme_id = :enzyme_id"
    )
    with engine.begin() as conn:
        conn.execute(sql, params)

    print(f"  enzyme (sequence update): {len(params)} 行 (批量, 缺值不覆盖)")


def load_gene_info(only=None):
    """Load compact gene accession summary from sequence_links.tsv."""
    df = read_segmented(SEQ_LINKS_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    # The accession file has no gene symbol, but the master file does — and it is
    # keyed by the same UniProt entry, so the name is free here.
    cols = safe_usecols(MASTER_FILE, ["Entry", "Gene Names"], only=only)
    names_df = read_segmented(MASTER_FILE, only=only, dtype=str,
                              **({'usecols': cols} if cols else {}))
    gene_names = {
        entry: _clean_value(value)
        for entry, value in zip(names_df["Entry"], names_df["Gene Names"])
    }

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # Column width derived from the header, not hard-coded -- this used to be
        # range(1, 25) / range(1, 12), and the current table's last column has exactly
        # 1 real row each (zero slack), so a wider entry would fail to get an accession.
        insdc_nuc_cols = [f"INSDC_Nuc_ID_{i}" for i in indexed_groups(df.columns, "INSDC_Nuc_ID")]
        insdc_genbank_cols = [f"INSDC_Nuc_GenBank_Link_{i}"
                              for i in indexed_groups(df.columns, "INSDC_Nuc_GenBank_Link")]
        insdc_prot_cols = [f"INSDC_Prot_ID_{i}" for i in indexed_groups(df.columns, "INSDC_Prot_ID")]
        refseq_nuc_cols = [f"RefSeq_Nuc_ID_{i}" for i in indexed_groups(df.columns, "RefSeq_Nuc_ID")]
        refseq_nuc_link_cols = [f"RefSeq_Nuc_Link_{i}"
                                for i in indexed_groups(df.columns, "RefSeq_Nuc_Link")]
        refseq_prot_cols = [f"RefSeq_Prot_ID_{i}" for i in indexed_groups(df.columns, "RefSeq_Prot_ID")]

        ena_accession = _first_clean(row, insdc_nuc_cols)
        genbank_id = ena_accession or _first_clean(row, refseq_nuc_cols)
        ncbi_url = _first_clean(row, insdc_genbank_cols) or _first_clean(row, refseq_nuc_link_cols)
        protein_accession = _first_clean(row, insdc_prot_cols) or _first_clean(row, refseq_prot_cols)

        if any([genbank_id, ena_accession, protein_accession]):
            rows.append({
                "enzyme_id": enzyme_id,
                "gene_name": gene_names.get(entry),
                "genbank_id": genbank_id,
                "ncbi_url": ncbi_url,
                "ena_accession": ena_accession,
                "protein_accession": protein_accession,
            })

    # No more DELETE FROM gene -- that is a full-table wipe, clearing the other
    # source's rows too. Source-scoped deletion is done by etl_run.purge_source();
    # this only writes.
    if not rows:
        print("  gene: no rows to insert")
        return

    gene_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "gene_name", "genbank_id", "ncbi_url", "ena_accession", "protein_accession"]
    with engine.begin() as conn:
        gene_df[cols].to_sql("gene", conn, if_exists="append", index=False)
    print(f"  gene: {len(gene_df)} rows inserted")


def _append_sequence_link(rows, enzyme_id, category, accession, url=None, related_accession=None, related_url=None):
    accession = _clean_value(accession)
    if not accession:
        return
    rows.append({
        "enzyme_id": enzyme_id,
        "link_category": category,
        "accession": accession,
        "url": _clean_value(url),
        "related_accession": _clean_value(related_accession),
        "related_url": _clean_value(related_url),
    })


def _append_insdc_links(rows, enzyme_id, row, index):
    nuc_id = _clean_value(row.get(f"INSDC_Nuc_ID_{index}"))
    prot_id = _clean_value(row.get(f"INSDC_Prot_ID_{index}"))
    molecule = _clean_value(row.get(f"INSDC_Molecule_{index}"))
    suffix = f" ({molecule})" if molecule else ""

    for source in ("EMBL", "GenBank", "DDBJ"):
        _append_sequence_link(
            rows,
            enzyme_id,
            f"INSDC nucleotide {source}{suffix}",
            nuc_id,
            row.get(f"INSDC_Nuc_{source}_Link_{index}"),
            prot_id,
        )
        _append_sequence_link(
            rows,
            enzyme_id,
            f"INSDC protein {source}{suffix}",
            prot_id,
            row.get(f"INSDC_Prot_{source}_Link_{index}"),
            nuc_id,
        )

    if nuc_id and not any(_clean_value(row.get(f"INSDC_Nuc_{source}_Link_{index}")) for source in ("EMBL", "GenBank", "DDBJ")):
        _append_sequence_link(rows, enzyme_id, f"INSDC nucleotide{suffix}", nuc_id, related_accession=prot_id)
    if prot_id and not any(_clean_value(row.get(f"INSDC_Prot_{source}_Link_{index}")) for source in ("EMBL", "GenBank", "DDBJ")):
        _append_sequence_link(rows, enzyme_id, f"INSDC protein{suffix}", prot_id, related_accession=nuc_id)


def load_sequence_links(only=None):
    """Load all external sequence accessions from sequence_links.tsv."""
    _ensure_sequence_link_table()
    df = read_segmented(SEQ_LINKS_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # Width derived from the header, not hard-coded. This is one of the places that
        # **produces rows** -- with 24/11 hard-coded, the 25th INSDC link was silently
        # dropped (the current table's 24th column has exactly 1 real row, zero slack).
        for i in indexed_groups(df.columns, "INSDC_Nuc_ID"):
            _append_insdc_links(rows, enzyme_id, row, i)

        for i in indexed_groups(df.columns, "RefSeq_Prot_ID"):
            protein_accession = _clean_value(row.get(f"RefSeq_Prot_ID_{i}"))
            nucleotide_accession = _clean_value(row.get(f"RefSeq_Nuc_ID_{i}"))
            protein_url = _clean_value(row.get(f"RefSeq_Prot_Link_{i}"))
            nucleotide_url = _clean_value(row.get(f"RefSeq_Nuc_Link_{i}"))
            molecule = _clean_value(row.get(f"RefSeq_Molecule_{i}"))
            suffix = f" ({molecule})" if molecule else ""
            _append_sequence_link(rows, enzyme_id, f"RefSeq protein{suffix}", protein_accession, protein_url, nucleotide_accession, nucleotide_url)
            _append_sequence_link(rows, enzyme_id, f"RefSeq nucleotide{suffix}", nucleotide_accession, nucleotide_url, protein_accession, protein_url)

    if not rows:
        print("  sequence links: no rows to insert")
        return

    link_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "link_category", "accession", "url", "related_accession", "related_url"]
    with engine.begin() as conn:
        link_df[cols].to_sql("gene_sequence_link", conn, if_exists="append", index=False)
    print(f"  sequence links: {len(link_df)} rows inserted")


def load_evidence(only=None):
    """Load evidence from references.tsv."""
    _ensure_columns("evidence", EVIDENCE_COLUMNS)
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE evidence MODIFY COLUMN positions TEXT"))
        conn.commit()
    df = read_segmented(REFERENCES_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))
    source_by_enzyme = _enzyme_source_map()

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # The evidence table has no source_type; review status follows its enzyme's
        # source -- the original wrote "official" unconditionally, marking TrEMBL
        # evidence as officially reviewed too.
        review = SOURCE_TO_REVIEW.get(source_by_enzyme.get(enzyme_id, ''))
        if review is None:
            raise ValueError(f'{enzyme_id} 的来源 {source_by_enzyme.get(enzyme_id)!r} 未知, '
                             f'无法确定 evidence.review_status; 已登记 {SOURCE_TO_REVIEW}')

        for i in indexed_groups(df.columns, "PMID"):
            evidence_row = {
                "enzyme_id": enzyme_id,
                "pubmed_id": _clean_value(row.get(f"PMID_{i}")),
                "doi": _clean_value(row.get(f"DOI_{i}")),
                "title": _clean_value(row.get(f"Title_{i}")),
                "authors": _clean_value(row.get(f"Authors_{i}")),
                "journal": _clean_value(row.get(f"Journal_{i}")),
                "volume": _clean_value(row.get(f"Volume_{i}")),
                "pages": _clean_value(row.get(f"Pages_{i}")),
                "publication_year": _clean_int(row.get(f"Year_{i}")),
                "reference_type": _clean_value(row.get(f"Type_{i}")),
                "positions": _clean_value(row.get(f"Positions_{i}")),
                "url": _clean_value(row.get(f"URL_{i}")),
                "source_description": "UniProt reference",
                "review_status": review,
            }
            if not any(v for k, v in evidence_row.items() if k not in {"enzyme_id", "source_description", "review_status"}):
                continue
            rows.append(evidence_row)

    if not rows:
        print("  evidence: no rows to insert")
        return

    ev_df = pd.DataFrame(rows).drop_duplicates()
    cols = [
        "enzyme_id", "pubmed_id", "doi", "title", "authors", "journal", "volume",
        "pages", "publication_year", "reference_type", "positions", "url",
        "source_description", "review_status",
    ]
    with engine.begin() as conn:
        ev_df[cols].to_sql("evidence", conn, if_exists="append", index=False)
    print(f"  evidence: {len(ev_df)} rows inserted")


def load_go_terms(only=None):
    """Load Gene Ontology annotations from uniprotkb_go.tsv."""
    _ensure_table(schema_ddl('enzyme_go'))
    df = read_segmented(GO_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        enzyme_id = entry_to_id.get(_clean_value(row.get("Entry")))
        if not enzyme_id:
            continue
        go_id = _clean_value(row.get("GO ID"))
        go_term = _clean_value(row.get("GO Term"))
        go_url = _clean_value(row.get("GO Link"))
        if go_id or go_term or go_url:
            rows.append({
                "enzyme_id": enzyme_id,
                "go_id": go_id,
                "go_term": go_term,
                "go_url": go_url,
            })

    if not rows:
        print("  GO terms: no rows to insert")
        return

    go_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "go_id", "go_term", "go_url"]
    with engine.begin() as conn:
        go_df[cols].to_sql("enzyme_go", conn, if_exists="append", index=False)
    print(f"  GO terms: {len(go_df)} rows inserted")


def load_isoforms(only=None):
    """Load isoform sequences from uniprotkb_isoform_sequences.tsv."""
    _ensure_table(schema_ddl('enzyme_isoform'))
    df = read_segmented(ISOFORM_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        enzyme_id = entry_to_id.get(_clean_value(row.get("Entry")))
        if not enzyme_id:
            continue
        isoform_id = _clean_value(row.get("Isoform_ID"))
        if not isoform_id:
            continue
        rows.append({
            "enzyme_id": enzyme_id,
            "isoform_id": isoform_id,
            "isoform_length": _clean_int(row.get("Isoform Length")),
            "isoform_mass": _clean_value(row.get("Isoform Mass")),
            "canonical_sequence": _clean_value(row.get("Canonical Sequence")),
            "canonical_length": _clean_int(row.get("Canonical Length")),
            "canonical_mass": _clean_value(row.get("Canonical Mass")),
            "sequence": _clean_value(row.get("Sequence")),
        })

    if not rows:
        print("  isoforms: no rows to insert")
        return

    isoform_df = pd.DataFrame(rows).drop_duplicates()
    cols = [
        "enzyme_id", "isoform_id", "isoform_length", "isoform_mass",
        "canonical_sequence", "canonical_length", "canonical_mass", "sequence",
    ]
    with engine.begin() as conn:
        isoform_df[cols].to_sql("enzyme_isoform", conn, if_exists="append", index=False)
    print(f"  isoforms: {len(isoform_df)} rows inserted")


def _true_variant_isoform_ids():
    """Set of isoform_ids whose sequence genuinely differs from the canonical one.

    Uses the **same predicate** as blast_service._isoform_subject_query -- the system
    should have only one definition of "what counts as a true variant"; there it is the
    BLAST subject database, here it is score ingestion.

    Of the 56 isoform records in the source table, 26 have an id of `-1` (UniProt's
    canonical numbering), with a sequence identical char-for-char to canonical and a
    score differing only in the sixth decimal (measured max Δ 0.000004) -- floating
    point noise from re-running the same sequence, not another biological entity. The
    frontend always reads via `isoform_id IS NULL`, so those 26 rows would never be read
    even if inserted, so they are better left unwritten.
    """
    df = pd.read_sql(
        "SELECT DISTINCT isoform_id FROM enzyme_isoform "
        "WHERE sequence IS NOT NULL "
        "  AND (canonical_sequence IS NULL OR sequence != canonical_sequence)",
        engine,
    )
    return {str(v) for v in df["isoform_id"].dropna()}


def load_solubility_scores(only=None):
    """Load per-enzyme model score + membrane annotation.

    ⚠️ Must run **after** load_isoforms() -- the true-variant filter below queries
    enzyme_isoform.
    """
    _ensure_table(schema_ddl('enzyme_solubility_score'))
    df = read_segmented(SOLUBILITY_FILE, only=only, dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    true_variants = _true_variant_isoform_ids()

    rows = []
    no_enzyme = bad_score = dropped_redundant = 0
    for _, row in df.iterrows():
        enzyme_id = entry_to_id.get(_clean_value(row.get("Entry")))
        if not enzyme_id:
            no_enzyme += 1
            continue

        # ⚠️ Opposite of load_isoforms: an empty isoform_id **is exactly the canonical
        # row**, so do not skip it -- copying that side's `if not isoform_id: continue`
        # would drop all canonical data. Write None (not ''); the frontend reads
        # `isoform_id IS NULL`.
        isoform_id = _clean_value(row.get("Isoform_ID"))
        if isoform_id and isoform_id not in true_variants:
            dropped_redundant += 1
            continue

        raw_score = _clean_value(row.get("DeepSolNet Score"))
        try:
            score = float(raw_score) if raw_score is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            bad_score += 1
            continue

        rows.append({
            "enzyme_id": enzyme_id,
            "isoform_id": isoform_id,
            "deep_solnet_score": score,
            "membrane": _clean_value(row.get("Membrane")),
            "membrane_evidence": _clean_value(row.get("Membrane Evidence")),
            "sequence_length": _clean_int(row.get("Sequence Length")),
        })

    if not rows:
        print("  solubility scores: no rows to insert")
        return

    # De-duplicate by (enzyme_id, isoform_id): downstream `_load_solubility_meta` builds
    # a dict keyed by enzyme_id, where a duplicate key **silently lets the latter
    # overwrite the former**. The source data was verified 1:1; this just makes it
    # explicit.
    score_df = pd.DataFrame(rows)
    keys = ["enzyme_id", "isoform_id"]
    before = len(score_df)
    score_df = score_df.drop_duplicates(subset=keys, keep="first")
    if len(score_df) != before:
        print(f"  solubility scores: ⚠️ 按 {keys} 去掉了 {before - len(score_df)} 条重复键")

    cols = ["enzyme_id", "isoform_id", "deep_solnet_score", "membrane",
            "membrane_evidence", "sequence_length"]
    with engine.begin() as conn:
        score_df[cols].to_sql("enzyme_solubility_score", conn,
                              if_exists="append", index=False)

    canonical = int(score_df["isoform_id"].isna().sum())
    print(f"  solubility scores: {len(score_df)} rows inserted "
          f"(canonical {canonical}, 真变体 {len(score_df) - canonical})")
    print(f"  solubility scores: 跳过 —— 无对应酶 {no_enzyme}, 冗余/未登记 isoform "
          f"{dropped_redundant}, 分数缺失或非法 {bad_score}")


def run(only=None):
    update_enzyme_from_master(only=only)
    load_gene_info(only=only)
    load_sequence_links(only=only)
    load_evidence(only=only)
    load_go_terms(only=only)
    # Must run after load_isoforms: load_solubility_scores relies on enzyme_isoform to
    # identify true variants.
    load_isoforms(only=only)
    load_solubility_scores(only=only)


if __name__ == "__main__":
    run()
