"""ETL Step 1: the compound table -- class B (a global dictionary, no notion of source).

A compound is an objective chemical entity: the same ChEBI ID is the same compound,
regardless of which source's enzyme references it. So it is **not segmented by source and
not deleted by source**, only upserted (add-only + update in place).

Defect of the original implementation: `append` with no de-dup -> a primary-key conflict on
the second run, and ETL step 1 hits `sys.exit(1)`.
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_URL  # noqa: E402
from db_utils import upsert_dataframe  # noqa: E402
from sources import read_merged  # noqa: E402

engine = create_engine(DB_URL)

COMPOUNDS_FILE = 'for_compound_card/uniprotkb_terpene_compounds.tsv'
ALL_NODES_FILE = 'for_graph/all_nodes.tsv'

EXCLUDED_COMMON_COMPOUND_IDS = {"CHEBI:15377", "CHEBI:15378", "CHEBI:33019"}

COMPOUND_COLUMNS = {
    "inchi_key": "VARCHAR(100)",
    # The second key (computed by RDKit from smiles). Used only for server-side matching,
    # absent from every API response. Note _ensure_columns only ADD COLUMNs, it does **not
    # create indexes** -- the live database's idx_compound_inchi_key_derived needs a manual
    # ALTER once, see sql/schema.sql.
    "inchi_key_derived": "VARCHAR(100)",
}


def _fill_blank_name(out):
    """Compounds with a blank name fall back to compound_id.

    compound.name is NOT NULL, and all_nodes mixes in entries whose name cannot be
    parsed -- the full TrEMBL run measured 4 POLYMER:* entries (ChEBI polymer entities
    from Rhea equations, with no name or structure in the ChEBI reference library). A
    blank name rolls back the **whole upsert transaction** with (1048, "Column 'name'
    cannot be null"), dropping the non-blank rows too.

    The fallback takes compound_id rather than an empty string: the frontend
    compound_filters.py already assumes `Compound.name != Compound.compound_id` is what
    makes a compound displayable, so name=id exactly expresses "this entity has no real
    name" -> not drawn into the graph (it would be an unlabelled node anyway), but still
    kept in the dictionary, so reaction_compound's foreign key is unaffected.

    Both an empty string and None must be recognised (COALESCE treats both as "no
    value", and MySQL rejects them).
    """
    blank = out["name"].isna() | (out["name"].astype(str).str.strip() == "")
    out.loc[blank, "name"] = out.loc[blank, "compound_id"]
    return out


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


def load_compounds():
    _ensure_columns("compound", COMPOUND_COLUMNS)
    df = read_merged(COMPOUNDS_FILE)
    df = df[~df["ChEBI ID"].isin(EXCLUDED_COMMON_COMPOUND_IDS)]

    out = pd.DataFrame()
    out["compound_id"] = df["ChEBI ID"]
    out["name"] = df["Name"]
    out["chebi_id"] = df["ChEBI ID"]
    out["smiles"] = df["SMILES"]
    out["average_mass"] = pd.to_numeric(df["Molecular Mass"], errors="coerce")
    out["chebi_url"] = df["ChEBI URL"]
    out["inchi_key"] = None          # filled by supplement_from_all_nodes
    out["inchi_key_derived"] = None  # same
    out["structure_image_url"] = df["ChEBI ID"].apply(
        lambda x: f"https://www.ebi.ac.uk/chebi/displayImage.do?defaultImage=true&chebiId={x.split(':')[-1]}"
    )
    _fill_blank_name(out)

    cols = ["compound_id", "name", "chebi_id", "smiles", "average_mass",
            "chebi_url", "inchi_key", "inchi_key_derived", "structure_image_url"]
    with engine.begin() as conn:
        n = upsert_dataframe(conn, "compound", out[cols],
                             update_cols=[c for c in cols if c != "compound_id"],
                             preserve=("inchi_key", "inchi_key_derived"))
    print(f"  compound: upsert {n} 行 (两个 inchi key 传 NULL 不覆盖已有值)")


def supplement_from_all_nodes():
    """Supplement the compound table with compounds from all_nodes.tsv and fill both
    InChI Keys."""
    _ensure_columns("compound", COMPOUND_COLUMNS)
    df = read_merged(ALL_NODES_FILE)
    df = df[~df["ChEBI ID"].isin(EXCLUDED_COMMON_COMPOUND_IDS)]

    key_cols = [
        ("inchi_key", "InChI Key"),                  # ChEBI official value: the published column
        ("inchi_key_derived", "InChI Key Derived"),  # computed live by RDKit: server-side matching only
    ]
    out = pd.DataFrame()
    out["compound_id"] = df["ChEBI ID"]
    out["name"] = df["Name"]
    out["chebi_id"] = df["ChEBI ID"]
    for column, source in key_cols:
        present = source in df.columns
        out[column] = df[source].fillna("") if present else None
        # An empty string makes COALESCE think "has a value" and blanks the existing
        # value -> normalise to None first.
        if present:
            out.loc[out[column].astype(str).str.strip() == "", column] = None
    _fill_blank_name(out)

    with engine.begin() as conn:
        n = upsert_dataframe(conn, "compound", out,
                             update_cols=["name", "chebi_id"] + [c for c, _ in key_cols],
                             preserve=("name", "chebi_id") + tuple(c for c, _ in key_cols))
    print(f"  compound (from all_nodes): upsert {n} 行")


if __name__ == "__main__":
    load_compounds()
    supplement_from_all_nodes()


def run(only=None):
    """only is unused -- compounds are source-independent (class B). The parameter is
    kept for a consistent step signature."""
    load_compounds()
    supplement_from_all_nodes()
