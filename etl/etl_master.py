"""ETL Step 5: Update enzyme (sequence) + load gene + evidence from child tables."""
import pandas as pd
import re
from sqlalchemy import create_engine, text
from config import DATA_DIR, DB_URL

engine = create_engine(DB_URL)

MASTER_FILE = f"{DATA_DIR}/for_enzyme_detail/uniprotkb_master.tsv"
REFERENCES_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_references.tsv"
SEQ_LINKS_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv"


def update_enzyme_from_master():
    """Extract sequence, length, mass from the wide master.tsv."""
    df = pd.read_csv(MASTER_FILE, sep="\t")

    # Use DB enzyme mapping
    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    # Identify columns after the Rhea block
    # Rhea columns are predictable: Rhea ID_1 through ChEBI IDs_22
    rhea_col_prefixes = [
        "Rhea ID_", "Rhea Link_", "Equation_", "Direction_",
        "EC Number_", "Reaction SMILES_", "ChEBI IDs_"
    ]
    rhea_cols = set()
    for col in df.columns:
        for prefix in rhea_col_prefixes:
            if col.startswith(prefix):
                rhea_cols.add(col)

    non_rhea_cols = [c for c in df.columns if c not in rhea_cols and c not in
                     ("Entry", "UniProt Link", "Recommended Name", "Alternative Names")]

    # Try to find sequence, length, mass columns
    # Strategy: find the first column whose data rows are long uppercase strings
    seq_col = None
    for col in non_rhea_cols:
        sample = df[col].dropna().head(5)
        if len(sample) > 0:
            vals = sample.astype(str)
            # Sequence: long string (100+ chars), mostly uppercase letters
            if vals.str.len().mean() > 50 and vals.str.match(r'^[A-Z*]+$').all():
                seq_col = col
                break

    # After sequence, find numeric columns for length and mass
    len_col = None
    mass_col = None
    if seq_col:
        seq_idx = list(df.columns).index(seq_col)
        trailing_cols = df.columns[seq_idx + 1:]
        numeric_cols = []
        for col in trailing_cols:
            sample = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(sample) > len(df) * 0.5:
                numeric_cols.append(col)
        if len(numeric_cols) >= 2:
            len_col = numeric_cols[0]
            mass_col = numeric_cols[1]
        elif len(numeric_cols) == 1:
            len_col = numeric_cols[0]

    if seq_col is None:
        print("  enzyme update: sequence column not found, skipping")
        return

    updated = 0
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        updates = {}
        if pd.notna(row.get(seq_col)):
            updates["sequence"] = str(row[seq_col])
        if len_col and pd.notna(row.get(len_col)):
            try:
                updates["length"] = int(row[len_col])
            except (ValueError, TypeError):
                pass
        if mass_col and pd.notna(row.get(mass_col)):
            try:
                updates["mass"] = float(row[mass_col])
            except (ValueError, TypeError):
                pass

        if updates:
            set_clause = ", ".join(f"{k} = :{k}" for k in updates)
            params = {k: v for k, v in updates.items()}
            params["enzyme_id"] = enzyme_id
            with engine.connect() as conn:
                conn.execute(
                    text(f"UPDATE enzyme SET {set_clause} WHERE enzyme_id = :enzyme_id"),
                    params
                )
                conn.commit()
            updated += 1

    print(f"  enzyme (sequence update): {updated} rows updated")


def load_gene_info():
    """Load gene info from sequence_links.tsv."""
    df = pd.read_csv(SEQ_LINKS_FILE, sep="\t")

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        # Get first available accession from each category
        genbank_id = None
        ena_accession = None
        protein_accession = None
        ncbi_url = None

        # Genomic
        for i in range(1, 15):
            col_id = f"Nuc_Genomic_EMBL_ID_{i}"
            if col_id in df.columns and pd.notna(row.get(col_id)):
                ena_accession = str(row[col_id])
                break

        # mRNA
        for i in range(1, 16):
            col_id = f"Nuc_mRNA_EMBL_ID_{i}"
            if col_id in df.columns and pd.notna(row.get(col_id)):
                genbank_id = str(row[col_id])
                link_col = f"Nuc_mRNA_EMBL_Link_{i}"
                if link_col in df.columns and pd.notna(row.get(link_col)):
                    ncbi_url = str(row.get(link_col))
                break

        # Protein
        for i in range(1, 19):
            col_id = f"Prot_EMBL_ORF_ID_{i}"
            if col_id in df.columns and pd.notna(row.get(col_id)):
                protein_accession = str(row[col_id])
                break

        if any([genbank_id, ena_accession, protein_accession]):
            rows.append({
                "enzyme_id": enzyme_id,
                "gene_name": None,
                "genbank_id": genbank_id,
                "ncbi_url": ncbi_url,
                "ena_accession": ena_accession,
                "protein_accession": protein_accession,
            })

    if not rows:
        print("  gene: no rows to insert")
        return

    gene_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "gene_name", "genbank_id", "ncbi_url", "ena_accession", "protein_accession"]
    gene_df[cols].to_sql("gene", engine, if_exists="append", index=False)
    print(f"  gene: {len(gene_df)} rows inserted")


def load_evidence():
    """Load evidence from references.tsv."""
    df = pd.read_csv(REFERENCES_FILE, sep="\t")

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        pubmed_ids = str(row.get("PubMed IDs", "")) if pd.notna(row.get("PubMed IDs")) else ""
        doi_ids = str(row.get("DOI ID", "")) if pd.notna(row.get("DOI ID")) else ""

        pubmed_list = [x.strip() for x in pubmed_ids.split(";") if x.strip()]
        doi_list = [x.strip() for x in doi_ids.split(";") if x.strip()]

        # Pair up PubMed IDs and DOIs
        max_len = max(len(pubmed_list), len(doi_list))
        for i in range(max_len):
            rows.append({
                "enzyme_id": enzyme_id,
                "pubmed_id": pubmed_list[i] if i < len(pubmed_list) else None,
                "doi": doi_list[i] if i < len(doi_list) else None,
                "source_description": "UniProt Swiss-Prot",
                "review_status": "official",
            })

    if not rows:
        print("  evidence: no rows to insert")
        return

    ev_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "pubmed_id", "doi", "source_description", "review_status"]
    ev_df[cols].to_sql("evidence", engine, if_exists="append", index=False)
    print(f"  evidence: {len(ev_df)} rows inserted")


if __name__ == "__main__":
    update_enzyme_from_master()
    load_gene_info()
    load_evidence()


def run():
    update_enzyme_from_master()
    load_gene_info()
    load_evidence()
