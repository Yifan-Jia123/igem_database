"""ETL Step 3: Load reaction + reaction_compound tables."""
import pandas as pd
from sqlalchemy import create_engine
from config import DATA_DIR, DB_URL, DIRECTION_MAP

engine = create_engine(DB_URL)

RHEA_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_rhea.tsv"
TERPENE_COMPOUNDS_FILE = f"{DATA_DIR}/for_compound_card/uniprotkb_terpene_compounds.tsv"
GRAPH_NODES_FILE = f"{DATA_DIR}/for_graph/all_nodes.tsv"


def parse_chebi_ids(chebi_str):
    """Parse ChEBI IDs (equation order) column into substrate and product lists.

    Format: 'CHEBI:138232 | CHEBI:138233; CHEBI:33019'
    Left of '|' = substrates (split by ';')
    Right of '|' = products (split by ';')
    """
    if pd.isna(chebi_str) or not chebi_str.strip():
        return [], []

    parts = chebi_str.split("|")
    substrates = [x.strip() for x in parts[0].split(";") if x.strip()] if len(parts) >= 1 else []
    products = [x.strip() for x in parts[1].split(";") if x.strip()] if len(parts) >= 2 else []
    return substrates, products


def load_allowed_compound_ids():
    """Return curated terpene compound IDs that are allowed as graph nodes."""
    allowed = set()
    for path in (TERPENE_COMPOUNDS_FILE, GRAPH_NODES_FILE):
        df = pd.read_csv(path, sep="\t", usecols=["ChEBI ID"])
        allowed.update(
            str(cid).strip()
            for cid in df["ChEBI ID"].dropna()
            if str(cid).strip()
        )
    return allowed


def load_reactions():
    df = pd.read_csv(RHEA_FILE, sep="\t")

    # Unique reactions
    reactions = df[["Rhea ID", "EC Number", "Equation", "Direction"]].drop_duplicates(subset="Rhea ID")

    reaction_df = pd.DataFrame()
    reaction_df["reaction_id"] = reactions["Rhea ID"]
    reaction_df["rhea_id"] = reactions["Rhea ID"]
    reaction_df["equation"] = reactions["Equation"]
    reaction_df["direction"] = reactions["Direction"].map(DIRECTION_MAP).fillna("unknown")
    reaction_df["ec_number"] = reactions["EC Number"]
    reaction_df["rhea_url"] = reactions["Rhea ID"].apply(
        lambda x: f"https://www.rhea-db.org/rhea/{x.split(':')[-1]}"
    )
    # Remove reactions where Rhea ID is empty/missing
    reaction_df = reaction_df[reaction_df["rhea_id"].notna() & (reaction_df["rhea_id"] != "")]

    # Check for duplicates in existing table to avoid conflicts
    existing = pd.read_sql("SELECT reaction_id FROM reaction", engine)
    existing_ids = set(existing["reaction_id"])
    reaction_df = reaction_df[~reaction_df["reaction_id"].isin(existing_ids)]

    reaction_df["source_type"] = "swiss_prot"
    reaction_df["review_status"] = "official"

    cols = ["reaction_id", "rhea_id", "equation", "direction", "ec_number",
            "rhea_url", "source_type", "review_status"]
    reaction_df[cols].to_sql("reaction", engine, if_exists="append", index=False)
    print(f"  reaction: {len(reaction_df)} rows inserted")


def load_reaction_compounds():
    """Populate reaction_compound from ChEBI IDs column."""
    df = pd.read_csv(RHEA_FILE, sep="\t")
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

    rc_df = pd.DataFrame(rows).drop_duplicates()

    cols = ["reaction_id", "compound_id", "role"]
    rc_df[cols].to_sql("reaction_compound", engine, if_exists="append", index=False)
    print(f"  reaction_compound: {len(rc_df)} rows inserted")
    if skipped_compounds:
        print(f"  reaction_compound: skipped {len(skipped_compounds)} non-curated ChEBI IDs")


if __name__ == "__main__":
    load_reactions()
    load_reaction_compounds()


def run():
    load_reactions()
    load_reaction_compounds()
