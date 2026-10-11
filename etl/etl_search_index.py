"""ETL Step 6: build the wide-table search index from all TSV sources.

## This table holds both class B and class C rows -- they must be handled separately

The criterion is whether `enzyme_id` is null (`enzyme_id` has an FK to enzyme):

| Row | Source | Handling |
|---|---|---|
| `enzyme_id IS NULL` | produced only by `_add_compounds`: the compound **entity itself** | **class B**: source-independent, fully rebuilt from global files |
| `enzyme_id IS NOT NULL` | enzyme/reaction/GO/reference/sequence link/isoform/compound-enzyme association | **class C**: replaced by source |

⚠️ Treating the whole table as class C is **wrong**: `WHERE enzyme_id IN (SELECT ...
WHERE source_type=<s>)` never matches NULL rows, so on every refresh those rows are
**inserted again**, growing monotonically. Conversely, truncating the whole table
breaks source isolation (refreshing TrEMBL would wipe SwissProt's index rows). So
`_clear_index()` clears in two parts.

**Why class B can be cleared and rebuilt wholesale**: they read only the
`for_compound_card/...terpene_compounds.tsv` and `for_graph/all_nodes.tsv` **hand-off
tables** (no suffix, source-independent), and both modes read exactly the same thing
-- so clearing and rebuilding breaks neither isolation nor data when "only one source
is refreshed". And "a changed value must be reflected" requires the clear: an index
row's identity is (entity, field, value), so a changed compound SMILES is a different
row, the old row is no longer true, and keeping it is dirty data.

## Column width is not hard-coded

`_add_sequence_links` used to hard-code the width as `range(1, 25)` / `range(1, 12)`.
Measured on the current table, `INSDC_Nuc_ID_24` and `RefSeq_Prot_ID_11` **each have
exactly 1 real row** -- zero slack, so one more entry with a 25-segment link would be
silently dropped. Changed to derive it from the column header, like `Enzyme_N`.

## Generate-and-write streaming (plan Phase 3.1)

Previously it was "accumulate all rows into `list[dict]` -> `pd.DataFrame(rows)` then
copy once more -> `drop_duplicates` building one full-database hash -> one `to_sql`" --
a triple peak. At 996 enzymes 138k rows was fine, but at 96k enzymes the measured
34-90 rows per enzyme (see below) means **3.3M-8.7M rows**, peaking at GB scale. Now it
generates, de-duplicates and writes **per table, per slice**, so the peak is independent
of the total row count.

**Equivalent to the original row set**, argued in two steps:

1. The dedupe key `DEDUPE_COLS` includes `source_file`, and each logical table maps to
   a different `source_file` -- so "cross-file duplicates" were never duplicates to
   begin with (a full-database dedupe would not merge them either), and per-table dedupe
   is equivalent to full dedupe;
2. Duplicate rows within a single file **all come from inside a single input row** (the
   same value appearing in several column positions, e.g. `Recommended name` and
   `Recommended Name` holding the same value, or one INSDC link repeated across several
   columns), and an input row falls entirely within one slice -- so per-slice dedupe is
   equivalent too.

Point 2 is not an inference, it is **measured**: a full-database dedupe in the sandbox
removed **1,474 / 166,655** rows (0.9%), distributed by `source_file` as rhea 128 /
sequence_links 384 / rhea_summary 384 / terpene_only 350 / pairs 228, and "dedupe by the
6-column key" and "dedupe by all columns" give **exactly the same** row count (both
165,181) -- i.e. every removed row was **column-for-column identical** to another, with
no case of "same key but different content".

> ⚠️ **Residual risk**: if a source file contains a **fully duplicated input row** (a
> source-side anomaly, not observed in practice) and the two rows straddle a slice
> boundary, per-slice dedupe would miss it. So a "duplicate-fact check" (GROUP BY the
> dedupe key) was added after the write to measure it explicitly -- a non-zero count
> prints a WARN rather than silently leaving duplicate rows.
"""
import hashlib
import re

import pandas as pd
from sqlalchemy import create_engine, text

from cell_values import split_multi_value
from config import DB_URL
from db_utils import schema_ddl
from sources import (columns_of, indexed_groups, indexed_width, read_merged,  # noqa: F401
                     read_segmented)

engine = create_engine(DB_URL)

# Source-segmented tables (11): one per source, for_*/<name>.<source>.tsv, unioned by
# sources.read_segmented.
TERPENE_ONLY_FILE = "for_graph/uniprotkb_terpene_only.tsv"
RHEA_SUMMARY_FILE = "for_enzyme_reaction_card/uniprotkb_rhea_summary.tsv"
ENZYME_MERGED_FILE = "for_enzyme_reaction_card/uniprotkb_enzyme_merged.tsv"
MASTER_FILE = "for_enzyme_detail/uniprotkb_master.tsv"
NAMES_FILE = "for_enzyme_detail/child_tables/uniprotkb_names_split.tsv"
RHEA_FILE = "for_enzyme_detail/child_tables/uniprotkb_rhea.tsv"
REFERENCES_FILE = "for_enzyme_detail/child_tables/uniprotkb_references.tsv"
SEQ_LINKS_FILE = "for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv"
GO_FILE = "for_enzyme_detail/child_tables/uniprotkb_go.tsv"
ISOFORM_FILE = "for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv"

# Hand-off tables (no suffix): aggregated across entries, source-independent -- the same
# substrate-product pair is naturally shared by enzymes from both sources.
COMPOUND_FILE = "for_compound_card/uniprotkb_terpene_compounds.tsv"
ALL_NODES_FILE = "for_graph/all_nodes.tsv"
TERPENE_PAIRS_FILE = "for_graph/uniprotkb_terpene_pairs.tsv"

EXCLUDED_COMMON_COMPOUND_IDS = {"CHEBI:15377", "CHEBI:15378", "CHEBI:33019"}

# ---- Batching parameters (plan Phase 3.1) ----
# Slice granularity in input rows: it sets the peak of "undeduped rows" per pass. Derived
# from the measured rows produced per input row (sequence_links is the widest: ~20 rows
# per link segment, measured width cap 31 segments): 2000 rows -> ~40k output rows.
INPUT_CHUNK = 2000
# Flush to the database once this many **deduped** rows have accumulated. 100k rows ≈
# tens of MB, independent of the total.
FLUSH_ROWS = 100000
# Rows per to_sql commit (one more layer under FLUSH_ROWS, to bound a single INSERT).
INSERT_CHUNK = 10000


def _read_seg(rel_path, only=None, **kwargs):
    return read_segmented(rel_path, only=only, dtype=str, **kwargs)


def _read_merged(rel_path):
    return read_merged(rel_path, dtype=str)


def _clean(value):
    if pd.isna(value):
        return None
    text_value = str(value).strip()
    if not text_value or text_value == "-":
        return None
    return text_value


def _hash(value):
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _add(rows, entity_type, entity_id, enzyme_id, source_file, field_name, value, weight):
    """source_file takes the **logical table name** (the unsuffixed rel path), not the
    path of the on-disk segmented file -- otherwise the same logical table would be
    written as two different values on SwissProt / TrEMBL rows. It is descriptive only
    (no query or UI in the repo reads it), so it just needs to stay stable."""
    value = _clean(value)
    entity_id = _clean(entity_id)
    if not value or not entity_id:
        return
    if entity_type == "compound" and entity_id in EXCLUDED_COMMON_COMPOUND_IDS:
        return
    rows.append({
        "entity_type": entity_type,
        "entity_id": entity_id,
        "enzyme_id": _clean(enzyme_id),
        "source_file": source_file,
        "field_name": field_name,
        "field_value": value,
        "field_value_hash": _hash(value.lower()),
        "weight": weight,
    })


def _get_map(table, external_col, internal_col):
    try:
        df = pd.read_sql(f"SELECT {external_col}, {internal_col} FROM {table}", engine)
    except Exception:
        return {}
    return {
        str(row[external_col]).strip(): str(row[internal_col]).strip()
        for _, row in df.iterrows()
        if _clean(row.get(external_col)) and _clean(row.get(internal_col))
    }


def _filter_chebi_ids(value):
    value = _clean(value)
    if not value:
        return None
    ids = [
        chebi_id for chebi_id in re.findall(r"CHEBI:\d+", value)
        if chebi_id not in EXCLUDED_COMMON_COMPOUND_IDS
    ]
    return "; ".join(dict.fromkeys(ids)) if ids else None


# Enzyme field table: (source column, field_name in the index, weight). The same
# field_name can come from differently spelled columns (names_split uses `Recommended
# name`, master uses `Recommended Name`).
ENZYME_FIELDS = [
    ("Entry", "uniprot_id", 95),
    ("UniProt Link", "uniprot_url", 35),
    ("Entry Name", "entry_name", 65),
    ("Organism", "organism", 35),
    ("Recommended name", "primary_name", 65),
    ("Recommended Name", "primary_name", 65),
    ("Protein name", "primary_name", 60),
    ("Alternative names", "alternative_names", 45),
]


def _enzyme_usecols(rel, only=None):
    """The enzyme field columns that **actually exist** in this file -- read only those.

    master is a dynamically wide table (measured TrEMBL segment 473 columns), yet it
    contributes only 3 fields (Entry / UniProt Link / Recommended Name): parsing all
    columns at 96k x 473 peaks at GB scale, while this step wants only 3 columns. A
    missing column is not an error -- same behaviour as the original `if column in
    df.columns`.

    Returning None means "fall back to reading all columns": if even Entry is absent
    from these columns, using usecols would treat every row as having no Entry and
    **silently skip** it, which is worse than being slow.
    """
    names_by_source = columns_of(rel, only=only)
    present = [c for c, _, _ in ENZYME_FIELDS
               if all(c in names for names in names_by_source.values())]
    absent = [c for c, _, _ in ENZYME_FIELDS if c not in present]
    if absent:
        print(f'  [usecols] {rel}: 缺 {absent} —— 这几个字段本步不产出')
    if "Entry" not in present:
        print(f'  [usecols] {rel}: 连 Entry 都缺, 退回全列读取')
        return None
    return present


def _add_enzyme_file(rows, df, source_file, entry_to_enzyme_id):
    fields = ENZYME_FIELDS
    for _, row in df.iterrows():
        entry = _clean(row.get("Entry"))
        enzyme_id = entry_to_enzyme_id.get(entry)
        if not enzyme_id:
            continue
        for column, field_name, weight in fields:
            if column in df.columns:
                _add(rows, "enzyme", enzyme_id, enzyme_id, source_file, field_name, row.get(column), weight)


def _compound_to_enzyme_ids(entry_to_enzyme_id):
    """Compound -> enzymes that use it as substrate/product. Source filtering is already
    done by build_rows narrowing the mapping."""
    mapping = {}
    terpene_only = _read_seg(TERPENE_ONLY_FILE)
    for _, row in terpene_only.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        for column in ("Substrate ChEBI", "Product ChEBI"):
            compound_id = _clean(row.get(column))
            if compound_id and compound_id not in EXCLUDED_COMMON_COMPOUND_IDS:
                mapping.setdefault(compound_id, set()).add(enzyme_id)

    pairs = _read_merged(TERPENE_PAIRS_FILE)
    for _, row in pairs.iterrows():
        compound_ids = [
            _clean(row.get("Substrate ChEBI")),
            _clean(row.get("Product ChEBI")),
        ]
        compound_ids = [
            compound_id for compound_id in compound_ids
            if compound_id and compound_id not in EXCLUDED_COMMON_COMPOUND_IDS
        ]
        if not compound_ids:
            continue
        for index in indexed_groups(pairs.columns, "Enzyme"):
            enzyme_id = entry_to_enzyme_id.get(_clean(row.get(f"Enzyme_{index}")))
            if not enzyme_id:
                continue
            for compound_id in compound_ids:
                mapping.setdefault(compound_id, set()).add(enzyme_id)

    return mapping


def _add_compounds(rows, df, source_file, compound_to_enzyme_ids):
    fields = [
        ("ChEBI ID", "compound_id", 85),
        ("ChEBI ID", "chebi_id", 85),
        ("Name", "compound_name", 65),
        ("SMILES", "smiles", 30),
        ("Molecular Mass", "average_mass", 20),
        ("ChEBI URL", "chebi_url", 25),
        ("InChI Key", "inchi_key", 50),
    ]
    for _, row in df.iterrows():
        compound_id = _clean(row.get("ChEBI ID"))
        if not compound_id:
            continue
        for column, field_name, weight in fields:
            if column in df.columns:
                # enzyme_id=None: the compound **entity itself** row -> class B,
                # source-independent.
                _add(rows, "compound", compound_id, None, source_file, field_name, row.get(column), weight)
                for enzyme_id in compound_to_enzyme_ids.get(compound_id, set()):
                    _add(rows, "compound", compound_id, enzyme_id, source_file, field_name, row.get(column), weight)


def _add_rhea_rows(rows, df, source_file, entry_to_enzyme_id, rhea_to_reaction_id):
    for _, row in df.iterrows():
        entry = _clean(row.get("Entry"))
        enzyme_id = entry_to_enzyme_id.get(entry)
        rhea_id = _clean(row.get("Rhea ID"))
        reaction_id = rhea_to_reaction_id.get(rhea_id, rhea_id)
        if not enzyme_id:
            continue
        _add(rows, "enzyme", enzyme_id, enzyme_id, source_file, "uniprot_id", entry, 95)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "rhea_id", rhea_id, 90)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "ec_number", row.get("EC Number") or row.get("EC number"), 70)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_equation", row.get("Equation"), 55)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_direction", row.get("Direction"), 25)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_smiles", row.get("Reaction SMILES"), 30)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "chebi_ids", _filter_chebi_ids(row.get("ChEBI IDs (equation order)")), 40)


def _add_terpene_only(rows, df, source_file, entry_to_enzyme_id, rhea_to_reaction_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        reaction_id = rhea_to_reaction_id.get(_clean(row.get("Rhea ID")), _clean(row.get("Rhea ID")))
        if not enzyme_id:
            continue
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "rhea_id", row.get("Rhea ID"), 90)
        _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate_chebi", row.get("Substrate ChEBI"), 80)
        _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate", row.get("Substrate"), 60)
        _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product_chebi", row.get("Product ChEBI"), 80)
        _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product", row.get("Product"), 60)
        _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_direction", row.get("Direction"), 25)


def _add_terpene_pairs(rows, df, source_file, entry_to_enzyme_id, rhea_to_reaction_id):
    """pairs is a hand-off table: both sources' enzymes for the same compound pair land
    on one row. Source filtering is already done when build_rows narrows
    entry_to_enzyme_id, so there is no need to test source here."""
    for _, row in df.iterrows():
        for index in indexed_groups(df.columns, "Enzyme"):
            enzyme_entry = _clean(row.get(f"Enzyme_{index}"))
            enzyme_id = entry_to_enzyme_id.get(enzyme_entry)
            if not enzyme_id:
                continue
            _add(rows, "enzyme", enzyme_id, enzyme_id, source_file, "uniprot_id", enzyme_entry, 95)
            # The 4 lines below are independent of Rhea ID and emit once per enzyme --
            # putting them in the loop below would multiply them by the Rhea count.
            _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate_chebi", row.get("Substrate ChEBI"), 80)
            _add(rows, "compound", row.get("Substrate ChEBI"), enzyme_id, source_file, "substrate", row.get("Substrate"), 60)
            _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product_chebi", row.get("Product ChEBI"), 80)
            _add(rows, "compound", row.get("Product ChEBI"), enzyme_id, source_file, "product", row.get("Product"), 60)
            # Rhea ID_i may be a '; '-joined multi-value cell (see cell_values.py).
            # The original used the whole string as entity_id: the index gained a fake
            # entity that never matches, while the reactions the enzyme really catalyses
            # did not enter the index at all.
            for one_rhea in split_multi_value(row.get(f"Rhea ID_{index}")):
                reaction_id = rhea_to_reaction_id.get(one_rhea, one_rhea)
                _add(rows, "reaction", reaction_id, enzyme_id, source_file, "rhea_id", one_rhea, 90)
                _add(rows, "reaction", reaction_id, enzyme_id, source_file, "reaction_direction", row.get(f"Direction_{index}"), 25)


def _add_references(rows, df, source_file, entry_to_enzyme_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        for index in indexed_groups(df.columns, "PMID"):
            ref_entity = _clean(row.get(f"PMID_{index}")) or _clean(row.get(f"DOI_{index}")) or f"{enzyme_id}:reference:{index}"
            for column, field_name, weight in [
                (f"PMID_{index}", "pubmed_id", 80),
                (f"DOI_{index}", "doi", 80),
                (f"Title_{index}", "reference_title", 55),
                (f"Authors_{index}", "reference_authors", 35),
                (f"Journal_{index}", "journal", 35),
                (f"Volume_{index}", "volume", 15),
                (f"Pages_{index}", "pages", 15),
                (f"Year_{index}", "year", 25),
                (f"Type_{index}", "reference_type", 25),
                (f"Positions_{index}", "evidence_positions", 35),
                (f"URL_{index}", "reference_url", 25),
            ]:
                _add(rows, "evidence", ref_entity, enzyme_id, source_file, field_name, row.get(column), weight)


def _add_go(rows, df, source_file, entry_to_enzyme_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        go_id = _clean(row.get("GO ID")) or f"{enzyme_id}:go"
        _add(rows, "go", go_id, enzyme_id, source_file, "go_id", row.get("GO ID"), 75)
        _add(rows, "go", go_id, enzyme_id, source_file, "go_term", row.get("GO Term"), 55)
        _add(rows, "go", go_id, enzyme_id, source_file, "go_url", row.get("GO Link"), 20)


def _add_sequence_links(rows, df, source_file, entry_to_enzyme_id):
    # Width derived from the header, not hard-coded -- measured on the current table,
    # INSDC_Nuc_ID_24 / RefSeq_Prot_ID_11 each have 1 real row, i.e. the hard-coded cap
    # is already saturated, so one more link segment is silently dropped.
    insdc_max = indexed_width(df.columns, "INSDC_Nuc_ID", "INSDC_Prot_ID", "INSDC_Molecule")
    refseq_max = indexed_width(df.columns, "RefSeq_Nuc_ID", "RefSeq_Prot_ID", "RefSeq_Molecule")
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        if not enzyme_id:
            continue
        for index in range(1, insdc_max + 1):
            nuc_id = _clean(row.get(f"INSDC_Nuc_ID_{index}"))
            prot_id = _clean(row.get(f"INSDC_Prot_ID_{index}"))
            molecule = row.get(f"INSDC_Molecule_{index}")
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "accession", nuc_id, 70)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_source", "INSDC nucleotide", 25)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "molecule_type", molecule, 25)
            for link_col in ("EMBL", "GenBank", "DDBJ"):
                _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_url", row.get(f"INSDC_Nuc_{link_col}_Link_{index}"), 15)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "accession", prot_id, 70)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_source", "INSDC protein", 25)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "molecule_type", molecule, 25)
            for link_col in ("EMBL", "GenBank", "DDBJ"):
                _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_url", row.get(f"INSDC_Prot_{link_col}_Link_{index}"), 15)

        for index in range(1, refseq_max + 1):
            prot_id = _clean(row.get(f"RefSeq_Prot_ID_{index}"))
            nuc_id = _clean(row.get(f"RefSeq_Nuc_ID_{index}"))
            molecule = row.get(f"RefSeq_Molecule_{index}")
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "accession", prot_id, 70)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_source", "RefSeq protein", 25)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "sequence_url", row.get(f"RefSeq_Prot_Link_{index}"), 15)
            _add(rows, "sequence_link", prot_id, enzyme_id, source_file, "molecule_type", molecule, 25)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "accession", nuc_id, 70)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_source", "RefSeq nucleotide", 25)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "sequence_url", row.get(f"RefSeq_Nuc_Link_{index}"), 15)
            _add(rows, "sequence_link", nuc_id, enzyme_id, source_file, "molecule_type", molecule, 25)


def _add_isoforms(rows, df, source_file, entry_to_enzyme_id):
    for _, row in df.iterrows():
        enzyme_id = entry_to_enzyme_id.get(_clean(row.get("Entry")))
        isoform_id = _clean(row.get("Isoform_ID"))
        if not enzyme_id or not isoform_id:
            continue
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_id", isoform_id, 75)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_length", row.get("Isoform Length"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_mass", row.get("Isoform Mass"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "canonical_length", row.get("Canonical Length"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "canonical_mass", row.get("Canonical Mass"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "canonical_sequence", row.get("Canonical Sequence"), 20)
        _add(rows, "isoform", isoform_id, enzyme_id, source_file, "isoform_sequence", row.get("Sequence"), 20)


def _step_sources(entry_to_enzyme_id, compound_to_enzyme_ids, rhea_to_reaction_id, only):
    """[(logical table, is segmented, usecols, generate function)] -- **one logical table
    per step**.

    Stepping keeps peak memory at one step's slice rows rather than all rows. The
    generate functions share the signature `f(rows, df)`. A `usecols` of None means read
    all columns (the dynamically wide hand-off and child tables are narrow to begin with,
    but master must be narrowed). The order matches the original `build_rows` (order does
    not affect the final row set, but matching keeps comparison easy).
    """
    steps = []
    for rel in (NAMES_FILE, RHEA_SUMMARY_FILE, ENZYME_MERGED_FILE, MASTER_FILE):
        steps.append((rel, True, _enzyme_usecols(rel, only=only),
                      lambda rows, df, rel=rel: _add_enzyme_file(rows, df, rel, entry_to_enzyme_id)))
    for rel in (COMPOUND_FILE, ALL_NODES_FILE):
        steps.append((rel, False, None,
                      lambda rows, df, rel=rel: _add_compounds(rows, df, rel, compound_to_enzyme_ids)))
    steps += [
        (RHEA_FILE, True, None, lambda rows, df: _add_rhea_rows(
            rows, df, RHEA_FILE, entry_to_enzyme_id, rhea_to_reaction_id)),
        (TERPENE_ONLY_FILE, True, None, lambda rows, df: _add_terpene_only(
            rows, df, TERPENE_ONLY_FILE, entry_to_enzyme_id, rhea_to_reaction_id)),
        (TERPENE_PAIRS_FILE, False, None, lambda rows, df: _add_terpene_pairs(
            rows, df, TERPENE_PAIRS_FILE, entry_to_enzyme_id, rhea_to_reaction_id)),
        (REFERENCES_FILE, True, None, lambda rows, df: _add_references(
            rows, df, REFERENCES_FILE, entry_to_enzyme_id)),
        (GO_FILE, True, None, lambda rows, df: _add_go(rows, df, GO_FILE, entry_to_enzyme_id)),
        (SEQ_LINKS_FILE, True, None, lambda rows, df: _add_sequence_links(
            rows, df, SEQ_LINKS_FILE, entry_to_enzyme_id)),
        (ISOFORM_FILE, True, None, lambda rows, df: _add_isoforms(
            rows, df, ISOFORM_FILE, entry_to_enzyme_id)),
    ]
    return steps


def _iter_batches(only=None):
    """Yield **deduped** (logical table, DataFrame) per table, per slice. Peak memory is
    independent of the total row count.

    With only=<s> it yields only that source's class C rows; class B rows (compound
    entities) are the same in both modes.

    Source filtering is centralised in **one place**: narrowing `entry_to_enzyme_id`.
    Every class C row hangs off one enzyme, and an enzyme belongs to exactly one source
    -- so as long as this mapping keeps only the target source's enzymes, **every**
    downstream read site (including the pairs / compounds hand-off tables, which cannot
    be segmented and mix both sources on one row) automatically yields only that
    source's rows. Testing source at each read site would miss one -- and that miss
    silently loads a second copy of the other source's index rows.
    """
    entry_to_enzyme_id = _get_map("enzyme", "uniprot_id", "enzyme_id")
    if only:
        source_of = _get_map("enzyme", "enzyme_id", "source_type")
        entry_to_enzyme_id = {entry: eid for entry, eid in entry_to_enzyme_id.items()
                              if source_of.get(eid) == only}
    rhea_to_reaction_id = _get_map("reaction", "rhea_id", "reaction_id")
    compound_to_enzyme_ids = _compound_to_enzyme_ids(entry_to_enzyme_id)

    for rel, segmented, usecols, generate in _step_sources(entry_to_enzyme_id, compound_to_enzyme_ids,
                                                          rhea_to_reaction_id, only):
        if segmented:
            df = _read_seg(rel, only=only, **({'usecols': usecols} if usecols else {}))
        else:
            df = _read_merged(rel)
        for start in range(0, len(df), INPUT_CHUNK):
            rows = []
            # Slices are cut by **input row**: all rows produced by one input row fall
            # in the same slice -- this is the basis for "per-slice dedupe == full
            # dedupe" (see the module docstring).
            generate(rows, df.iloc[start:start + INPUT_CHUNK])
            if not rows:
                continue
            yield rel, pd.DataFrame(rows).drop_duplicates(subset=DEDUPE_COLS)


def _insert(df):
    with engine.begin() as conn:
        df.to_sql("search_index", conn, if_exists="append", index=False,
                  chunksize=INSERT_CHUNK)


def _count_duplicate_facts():
    """Post-write self-check: group by the dedupe key and look for groups with >1 row.

    This is "add-only + append-only" reflected on the index -- the same (entity, field,
    value) should not have two rows. With normal input this is necessarily 0; a non-zero
    value means **the source has fully duplicated input rows split by a slice boundary**
    (see the residual risk note in the module docstring), and that must be said
    explicitly rather than left in the database.
    """
    cols = ", ".join(DEDUPE_COLS)
    sql = (f'SELECT COUNT(*) FROM (SELECT 1 FROM search_index '
           f'GROUP BY {cols} HAVING COUNT(*) > 1) AS dup')
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar() or 0)


def _clear_index(only):
    """Clear the rows to be rebuilt this run. Two parts, neither a full-table wipe --
    see the module docstring."""
    with engine.begin() as conn:
        conn.execute(text(schema_ddl('search_index')))

        # class B: compound entity rows, source-independent. Reads only hand-off
        # tables, so it can be fully rebuilt in both modes.
        b = conn.execute(text('DELETE FROM search_index WHERE enzyme_id IS NULL')).rowcount

        # class C: rows with an enzyme_id, cleared by source. In full mode both sources
        # are cleared.
        if only:
            c = conn.execute(text('DELETE FROM search_index WHERE enzyme_id IN '
                                  '(SELECT enzyme_id FROM enzyme WHERE source_type = :s)'),
                             {'s': only}).rowcount
        else:
            c = conn.execute(text('DELETE FROM search_index WHERE enzyme_id IS NOT NULL')).rowcount
    print(f'  search_index: 清掉 B 类 {b} 行 (化合物实体) / C 类 {c} 行 (来源={only or "全部"})')


DEDUPE_COLS = ["entity_type", "entity_id", "enzyme_id", "source_file", "field_name", "field_value_hash"]


def load_search_index(only=None):
    _clear_index(only)

    buffer, buffered = [], 0
    n_b_class = n_c_class = 0

    def flush():
        nonlocal buffer, buffered, n_b_class, n_c_class
        if not buffer:
            return
        df = pd.concat(buffer, ignore_index=True) if len(buffer) > 1 else buffer[0]
        n_null = int(df["enzyme_id"].isna().sum())
        n_b_class += n_null
        n_c_class += len(df) - n_null
        _insert(df)
        buffer, buffered = [], 0

    for _, df in _iter_batches(only=only):
        buffer.append(df)
        buffered += len(df)
        if buffered >= FLUSH_ROWS:
            flush()
    flush()

    if n_b_class + n_c_class == 0:
        print("  search_index: no rows to insert")
        return
    print(f"  search_index: {n_b_class + n_c_class} rows inserted "
          f"(B 类 {n_b_class} / C 类 {n_c_class})")

    dup = _count_duplicate_facts()
    if dup:
        print(f"  [WARN] search_index: {dup} 组重复事实 —— 源侧有完全重复的输入行, "
              f"且被切片边界分开(见本模块 docstring 的残留说明)。行内容仍然是对的, "
              f"只是同一事实存了多份。")
    else:
        print("  search_index: 重复事实 0 组 (去重键唯一) ✓")


if __name__ == "__main__":
    run()


def run(only=None):
    load_search_index(only=only)
