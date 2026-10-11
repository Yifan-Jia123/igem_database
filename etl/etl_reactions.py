"""ETL Step 3: the reaction table + reaction_compound -- class B (global dictionaries).

## Why reaction is not deleted by source

The `reaction` table **has** a source_type column, but it is not class A. The same Rhea
reaction is shared by both sources, so a DELETE by source would drop reaction rows the
other source is still referencing, and `enzyme_reaction_edge.fk_edge_reaction` would be a
direct foreign-key error.

A reaction is an **objective chemical entity**, not a property of some UniProt record -- so
it is uniformly recorded as `swiss_prot` and add-only, and source attribution is expressed
by `enzyme` / `enzyme_reaction_edge`.

## Two old defects fixed along the way

1. `drop_duplicates(subset="Rhea ID")` keeping a global merge -- for reaction this is
   **right** (one Rhea ID is one reaction), keep it.
2. The original implementation's "read existing -> filter out those already present" =
   **skip if it already exists**: an updated `smiles` / `equation` for an existing reaction
   would never take effect. Changed to upsert.

## ⚠️ 2026-09-19: in `--source=<s>` mode reaction is insert-only, never update

The "changed to upsert" above holds for **full mode** but is **wrong for single-source
mode** -- measured:

`read_segmented(RHEA_FILE, only=only)` reads only that segment in single-source mode, and
the `drop_duplicates(subset='Rhea ID')` below takes **the first row in that segment's
file**. The `Direction` of the same Rhea ID can differ between the two sources (TrEMBL's
automatic annotation is largely `not specified`), so the cross-source objective entity
"one Rhea ID is one reaction" has its attributes **decided by whichever mode ran last**:

| Direction of RHEA:31007 | first row read | after mapping | effect on the graph |
|---|---|---|---|
| merged view (swiss_prot first) | `left-to-right` | `forward` | draws substrate->product only |
| trembl segment only | `not specified` | `unknown` | **draws both ways** (`DIRECTION_ALLOWS_*` all include unknown) |

Measured running `--source=trembl` once: the `direction` of 12 rows in `reaction` flips from
`forward`/`reverse` to `unknown` (exactly the count of "shared reactions whose two segments
disagree on Direction", 12 of 39 shared reactions). The row count did not change, but the
home graph went from **157 groups / 7,166.6 KB to 169 groups / 8,774.7 KB**, and extra
paired mirror edges `(X,Y)` and `(Y,X)` appeared -- i.e. one "other source" refresh changed
what the graph draws.

So in single-source mode this is **pure insert-only** (`update_cols=[]`, going through
`INSERT IGNORE`): deciding which row a shared reaction should take can only be done by the
merged view; a single source sees half the table and has no right to overwrite. This is the
same rationale as `load_reaction_compounds`'s "ignore if it already exists" in this module,
and it is the isolation principle ("the two sources do not overwrite each other") applied to
a shared entity -- updating a shared reaction's attributes is the job of **full mode**
(full mode reads all segments, and the `SOURCES` order puts swiss_prot first, matching the
result before the fix).
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL, DIRECTION_MAP  # noqa: E402
from db_utils import upsert_dataframe  # noqa: E402
from sources import read_merged, read_segmented  # noqa: E402

engine = create_engine(DB_URL)

RHEA_FILE = 'for_enzyme_detail/child_tables/uniprotkb_rhea.tsv'
TERPENE_COMPOUNDS_FILE = 'for_compound_card/uniprotkb_terpene_compounds.tsv'
GRAPH_NODES_FILE = 'for_graph/all_nodes.tsv'
EXCLUDED_COMMON_COMPOUND_IDS = {"CHEBI:15377", "CHEBI:15378", "CHEBI:33019"}

REACTION_COLS = ["reaction_id", "rhea_id", "equation", "direction", "ec_number",
                 "smiles", "rhea_url", "source_type", "review_status"]


def parse_chebi_ids(chebi_str):
    """'CHEBI:138232 | CHEBI:138233; CHEBI:33019' -> (substrates, products)

    The left of '|' is substrates (';'-separated), the right is products.
    """
    if pd.isna(chebi_str) or not str(chebi_str).strip():
        return [], []

    parts = str(chebi_str).split("|")
    substrates = [x.strip() for x in parts[0].split(";") if x.strip()] if len(parts) >= 1 else []
    products = [x.strip() for x in parts[1].split(";") if x.strip()] if len(parts) >= 2 else []
    return substrates, products


def load_allowed_compound_ids():
    """Compounds allowed as graph nodes (the hand-curated set)."""
    allowed = set()
    for rel in (TERPENE_COMPOUNDS_FILE, GRAPH_NODES_FILE):
        df = read_merged(rel, usecols=["ChEBI ID"])
        allowed.update(
            str(cid).strip() for cid in df["ChEBI ID"].dropna() if str(cid).strip()
        )
    return allowed - EXCLUDED_COMMON_COMPOUND_IDS


def load_reactions(only=None):
    df = read_segmented(RHEA_FILE, only=only, dtype=str)

    # One Rhea ID is one reaction: merge globally by Rhea ID (a cross-source merge,
    # independent of source). A few Rhea IDs have multiple SMILES; take the first for
    # determinism.
    reactions = df[["Rhea ID", "EC Number", "Equation", "Direction",
                    "Reaction SMILES"]].drop_duplicates(subset="Rhea ID")

    out = pd.DataFrame()
    out["reaction_id"] = reactions["Rhea ID"]
    out["rhea_id"] = reactions["Rhea ID"]
    out["equation"] = reactions["Equation"]
    out["direction"] = reactions["Direction"].map(DIRECTION_MAP).fillna("unknown")
    out["ec_number"] = reactions["EC Number"]
    out["smiles"] = reactions["Reaction SMILES"]
    out["rhea_url"] = reactions["Rhea ID"].apply(
        lambda x: f"https://www.rhea-db.org/rhea/{str(x).split(':')[-1]}"
    )
    out = out[out["rhea_id"].notna() & (out["rhea_id"].astype(str).str.strip() != "")]

    # Reactions are uniformly recorded as swiss_prot: it is a shared objective entity, and
    # tagging by source would let a source-scoped DELETE remove it while the other source
    # references it -> foreign-key error. Source attribution is expressed by enzyme / edge.
    out["source_type"] = "swiss_prot"
    out["review_status"] = "official"

    # Single-source mode: insert-only, never overwrite (see the module docstring). Full
    # mode: normal upsert.
    upd = [] if only else [c for c in REACTION_COLS if c != "reaction_id"]
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "reaction", out[REACTION_COLS].drop_duplicates(subset="reaction_id"),
                             update_cols=upd)
    if only:
        # The wording is "input" not "insert": upsert_dataframe returns the input row count,
        # not the written row count (see the note in db_utils). In this mode all 112 of
        # these rows **already exist** and 0 are actually inserted, so writing "inserted 112
        # rows" would make an idempotent re-run look like it keeps reloading the same data.
        print(f"  reaction: 只新增模式, 输入 {n} 行 (冲突即忽略; "
              f"--source={only} 不覆盖共享反应, 属性更新由全量模式负责)")
    else:
        print(f"  reaction: upsert {n} 行")


def load_reaction_compounds(only=None):
    df = read_segmented(RHEA_FILE, only=only, dtype=str)
    allowed_compound_ids = load_allowed_compound_ids()

    rows = []
    skipped_compounds = set()
    for _, row in df.iterrows():
        rhea_id = row["Rhea ID"]
        if pd.isna(rhea_id) or not str(rhea_id).strip():
            continue

        substrates, products = parse_chebi_ids(row.get("ChEBI IDs (equation order)", ""))
        for chebi in substrates:
            if chebi in allowed_compound_ids:
                rows.append({"reaction_id": rhea_id, "compound_id": chebi, "role": "substrate"})
            else:
                skipped_compounds.add(chebi)
        for chebi in products:
            if chebi in allowed_compound_ids:
                rows.append({"reaction_id": rhea_id, "compound_id": chebi, "role": "product"})
            else:
                skipped_compounds.add(chebi)

    if not rows:
        print("  reaction_compound: no rows to insert")
        return

    rc = pd.DataFrame(rows).drop_duplicates()
    # UNIQUE (reaction_id, compound_id, role) guarantees idempotency: ignore on conflict.
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "reaction_compound", rc, update_cols=[])
    print(f"  reaction_compound: upsert {n} 行 (已存在则忽略)")
    if skipped_compounds:
        print(f"  reaction_compound: skipped {len(skipped_compounds)} non-curated ChEBI IDs")


if __name__ == "__main__":
    load_reactions()
    load_reaction_compounds()


def run(only=None):
    load_reactions(only=only)
    load_reaction_compounds(only=only)
