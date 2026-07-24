"""ETL Step 2: Load enzyme table."""
import pandas as pd
from sqlalchemy import create_engine
from config import DATA_DIR, DB_URL

engine = create_engine(DB_URL)

SOURCE_ENZYME_FILE = f"{DATA_DIR}/for_enzyme_reation_card/uniprotkb_rhea_summary.tsv"
NAMES_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_names_split.tsv"


def load_enzymes():
    # Load rhea_summary (has the broadest enzyme coverage)
    df_rhea = pd.read_csv(SOURCE_ENZYME_FILE, sep="\t")
    enzymes_from_rhea = df_rhea[["Entry", "Organism", "Protein name"]].drop_duplicates(subset="Entry")

    # Load names_split (has richer name data)
    df_names = pd.read_csv(NAMES_FILE, sep="\t")
    df_names = df_names.rename(columns={
        "Organism": "Organism_detailed",
        "Recommended name": "Recommended_name",
        "Alternative names": "Alternative_names",
    })

    # Merge: use names_split as primary, fill gaps from rhea
    merged = enzymes_from_rhea.merge(
        df_names[["Entry", "Organism_detailed", "Recommended_name", "Alternative_names"]],
        on="Entry", how="left"
    )

    # Build enzyme DataFrame
    enzyme_df = pd.DataFrame()
    enzyme_df["uniprot_id"] = merged["Entry"]
    # Prefer names_split name if available, else rhea's Protein name
    enzyme_df["primary_name"] = merged["Recommended_name"].fillna(merged["Protein name"])
    enzyme_df["organism_name"] = merged["Organism_detailed"].fillna(merged["Organism"])
    # secondary_names as JSON array
    enzyme_df["secondary_names"] = merged["Alternative_names"].fillna("").apply(
        lambda s: [x.strip() for x in s.split(";") if x.strip()] if s else []
    )

    # Assign internal enzyme IDs
    enzyme_df = enzyme_df.reset_index(drop=True)
    enzyme_df["enzyme_id"] = [f"ENZ{i+1:06d}" for i in range(len(enzyme_df))]
    enzyme_df["source_type"] = "swiss_prot"
    enzyme_df["review_status"] = "official"

    # Keep only table columns
    cols = ["enzyme_id", "uniprot_id", "primary_name", "organism_name",
            "secondary_names", "source_type", "review_status"]
    # Convert secondary_names list to JSON-friendly format for MySQL
    import json
    enzyme_df["secondary_names"] = enzyme_df["secondary_names"].apply(json.dumps)

    enzyme_df[cols].to_sql("enzyme", engine, if_exists="append", index=False)
    print(f"  enzyme: {len(enzyme_df)} rows inserted")


if __name__ == "__main__":
    load_enzymes()


def run():
    load_enzymes()
