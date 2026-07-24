"""ETL Step 1: Load compound table."""
import pandas as pd
from sqlalchemy import create_engine
from config import DATA_DIR, DB_URL

engine = create_engine(DB_URL)

def load_compounds():
    compounds_path = f"{DATA_DIR}/for_compound_card/uniprotkb_terpene_compounds.tsv"
    df = pd.read_csv(compounds_path, sep="\t")

    df["compound_id"] = df["ChEBI ID"]
    df["name"] = df["Name"]
    df["chebi_id"] = df["ChEBI ID"]
    df["smiles"] = df["SMILES"]
    df["average_mass"] = pd.to_numeric(df["Molecular Mass"], errors="coerce")
    df["chebi_url"] = df["ChEBI URL"]
    df["structure_image_url"] = df["ChEBI ID"].apply(
        lambda x: f"https://www.ebi.ac.uk/chebi/displayImage.do?defaultImage=true&chebiId={x.split(':')[-1]}"
    )

    cols = ["compound_id", "name", "chebi_id", "smiles", "average_mass",
            "chebi_url", "structure_image_url"]
    df[cols].to_sql("compound", engine, if_exists="append", index=False)
    print(f"  compound: {len(df)} rows inserted")


def supplement_from_all_nodes():
    """Add any compounds from all_nodes.tsv that aren't already in the table."""
    all_nodes_path = f"{DATA_DIR}/for_graph/all_nodes.tsv"
    df = pd.read_csv(all_nodes_path, sep="\t")

    existing = pd.read_sql("SELECT compound_id FROM compound", engine)
    existing_ids = set(existing["compound_id"])

    new_rows = df[~df["ChEBI ID"].isin(existing_ids)]
    if len(new_rows) == 0:
        print(f"  (no new compounds from all_nodes.tsv)")
        return

    insert = pd.DataFrame({
        "compound_id": new_rows["ChEBI ID"],
        "name": new_rows["Name"],
        "chebi_id": new_rows["ChEBI ID"],
    })
    insert.to_sql("compound", engine, if_exists="append", index=False)
    print(f"  compound (from all_nodes): {len(insert)} new rows")


if __name__ == "__main__":
    load_compounds()
    supplement_from_all_nodes()


def run():
    load_compounds()
    supplement_from_all_nodes()
