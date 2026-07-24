"""ETL Step 4: Load enzyme_reaction_edge table."""
import pandas as pd
from sqlalchemy import create_engine, text
from config import DATA_DIR, DB_URL

engine = create_engine(DB_URL)

RHEA_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_rhea.tsv"
PAIRS_FILE = f"{DATA_DIR}/for_graph/uniprotkb_terpene_pairs.tsv"


def load_edges_from_rhea():
    """Each row in uniprotkb_rhea.tsv = one enzyme-reaction edge."""
    df = pd.read_csv(RHEA_FILE, sep="\t")

    edges = df[["Entry", "Rhea ID"]].dropna(subset=["Rhea ID", "Entry"])
    edges = edges[(edges["Rhea ID"] != "") & (edges["Entry"] != "")]
    edges = edges.drop_duplicates()

    # Map to internal IDs
    enzyme_map = _get_id_map("enzyme", "uniprot_id", "enzyme_id")
    reaction_map = _get_id_map("reaction", "rhea_id", "reaction_id")

    edges["enzyme_id"] = edges["Entry"].map(enzyme_map)
    edges["reaction_id"] = edges["Rhea ID"].map(reaction_map)

    edges = edges.dropna(subset=["enzyme_id", "reaction_id"])

    edge_df = pd.DataFrame()
    edge_df["enzyme_id"] = edges["enzyme_id"]
    edge_df["reaction_id"] = edges["reaction_id"]
    edge_df["source_type"] = "swiss_prot"
    edge_df["review_status"] = "official"

    return edge_df.drop_duplicates()


def load_edges_from_pairs():
    """Unpivot wide terpene_pairs.tsv: one substrate-product pair → N enzymes."""
    df = pd.read_csv(PAIRS_FILE, sep="\t")

    enzyme_map = _get_id_map("enzyme", "uniprot_id", "enzyme_id")
    reaction_map = _get_id_map("reaction", "rhea_id", "reaction_id")

    rows = []
    for _, row in df.iterrows():
        for i in range(1, 46):  # Enzyme_1..Enzyme_45
            enzyme_col = f"Enzyme_{i}"
            rhea_col = f"Rhea ID_{i}"
            if enzyme_col not in df.columns or rhea_col not in df.columns:
                break
            enzyme_entry = row.get(enzyme_col)
            rhea_id = row.get(rhea_col)
            if pd.isna(enzyme_entry) or not str(enzyme_entry).strip():
                continue
            if pd.isna(rhea_id) or not str(rhea_id).strip():
                continue
            enz_id = enzyme_map.get(str(enzyme_entry).strip())
            rxn_id = reaction_map.get(str(rhea_id).strip())
            if enz_id and rxn_id:
                rows.append({"enzyme_id": enz_id, "reaction_id": rxn_id})

    if not rows:
        return pd.DataFrame()

    edge_df = pd.DataFrame(rows).drop_duplicates()
    edge_df["source_type"] = "swiss_prot"
    edge_df["review_status"] = "official"
    return edge_df


def _get_id_map(table, external_col, internal_col):
    """Fetch a dict mapping external ID -> internal ID from MySQL."""
    df = pd.read_sql(f"SELECT {external_col}, {internal_col} FROM {table}", engine)
    return dict(zip(df[external_col], df[internal_col]))


def load_edges():
    edges_from_rhea = load_edges_from_rhea()
    edges_from_pairs = load_edges_from_pairs()

    all_edges = pd.concat([edges_from_rhea, edges_from_pairs], ignore_index=True).drop_duplicates(
        subset=["enzyme_id", "reaction_id"]
    )

    # Generate edge IDs
    all_edges = all_edges.reset_index(drop=True)
    all_edges["edge_id"] = [f"EDGE{i+1:07d}" for i in range(len(all_edges))]

    # Check existing
    existing = pd.read_sql("SELECT enzyme_id, reaction_id FROM enzyme_reaction_edge", engine)
    existing_pairs = set(zip(existing["enzyme_id"], existing["reaction_id"]))
    new_edges = all_edges[~all_edges.apply(
        lambda r: (r["enzyme_id"], r["reaction_id"]) in existing_pairs, axis=1
    )]

    if len(new_edges) == 0:
        print("  enzyme_reaction_edge: 0 new rows (all already exist)")
        return

    cols = ["edge_id", "enzyme_id", "reaction_id", "source_type", "review_status"]
    new_edges[cols].to_sql("enzyme_reaction_edge", engine, if_exists="append", index=False)
    print(f"  enzyme_reaction_edge: {len(new_edges)} rows inserted")


if __name__ == "__main__":
    load_edges()


def run():
    load_edges()
