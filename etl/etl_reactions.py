"""ETL Step 3: Load reaction + reaction_compound tables."""
import pandas as pd
from sqlalchemy import create_engine, text
from config import DATA_DIR, DB_URL, DIRECTION_MAP

engine = create_engine(DB_URL)

RHEA_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_rhea.tsv"


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

    rows = []
    for _, row in df.iterrows():
        rhea_id = row["Rhea ID"]
        if pd.isna(rhea_id) or not str(rhea_id).strip():
            continue

        substrates, products = parse_chebi_ids(row.get("ChEBI IDs (equation order)", ""))
        for chebi in substrates:
            rows.append({"reaction_id": rhea_id, "compound_id": chebi, "role": "substrate"})
        for chebi in products:
            rows.append({"reaction_id": rhea_id, "compound_id": chebi, "role": "product"})

    if not rows:
        print("  reaction_compound: no rows to insert")
        return

    rc_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["reaction_id", "compound_id", "role"]
    rc_df[cols].to_sql("reaction_compound", engine, if_exists="append", index=False)
    print(f"  reaction_compound: {len(rc_df)} rows inserted")

    # Also insert any missing compounds into compound table
    all_chebi_ids = set(rc_df["compound_id"].unique())
    existing_cmp = pd.read_sql("SELECT compound_id FROM compound", engine)
    existing_cmp_ids = set(existing_cmp["compound_id"])
    missing = all_chebi_ids - existing_cmp_ids

    if missing:
        missing_df = pd.DataFrame({
            "compound_id": list(missing),
            "chebi_id": list(missing),
            "name": [cid for cid in missing],  # placeholder name = chebi ID
        })
        missing_df.to_sql("compound", engine, if_exists="append", index=False)
        print(f"  compound (from reaction): {len(missing_df)} new rows (non-terpene)")


if __name__ == "__main__":
    load_reactions()
    load_reaction_compounds()


def run():
    load_reactions()
    load_reaction_compounds()
