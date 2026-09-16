"""ETL Step 5: Update enzyme (sequence) + load gene + evidence from child tables."""
import pandas as pd
import re
from sqlalchemy import create_engine, text
from config import DATA_DIR, DB_URL

engine = create_engine(DB_URL)

MASTER_FILE = f"{DATA_DIR}/for_enzyme_detail/uniprotkb_master.tsv"
REFERENCES_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_references.tsv"
SEQ_LINKS_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_sequence_links.tsv"
GO_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_go.tsv"
ISOFORM_FILE = f"{DATA_DIR}/for_enzyme_detail/child_tables/uniprotkb_isoform_sequences.tsv"

SEQUENCE_LINK_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS gene_sequence_link (
    sequence_link_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    link_category VARCHAR(80) NOT NULL,
    accession VARCHAR(80) NOT NULL,
    url VARCHAR(500),
    related_accession VARCHAR(80),
    related_url VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_gene_sequence_link_enzyme (enzyme_id),
    CONSTRAINT fk_gene_sequence_link_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
)
"""

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

GO_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS enzyme_go (
    go_record_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    go_id VARCHAR(30),
    go_term VARCHAR(500),
    go_url VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_go_enzyme (enzyme_id),
    INDEX idx_enzyme_go_id (go_id),
    CONSTRAINT fk_enzyme_go_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
)
"""

ISOFORM_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS enzyme_isoform (
    isoform_record_id INT AUTO_INCREMENT PRIMARY KEY,
    enzyme_id VARCHAR(20) NOT NULL,
    isoform_id VARCHAR(80),
    isoform_length INT,
    isoform_mass VARCHAR(80),
    canonical_sequence TEXT,
    canonical_length INT,
    canonical_mass VARCHAR(80),
    sequence TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_enzyme_isoform_enzyme (enzyme_id),
    INDEX idx_enzyme_isoform_id (isoform_id),
    CONSTRAINT fk_enzyme_isoform_enzyme
        FOREIGN KEY (enzyme_id) REFERENCES enzyme(enzyme_id)
)
"""


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


def _indexed_groups(columns, stem):
    numbers = []
    pattern = re.compile(rf"^{re.escape(stem)}_(\d+)$")
    for col in columns:
        match = pattern.match(col)
        if match:
            numbers.append(int(match.group(1)))
    return range(1, max(numbers, default=0) + 1)


def _collect_indexed_links(row, id_prefix, link_prefix, max_index, category):
    links = []
    for i in range(1, max_index + 1):
        accession = _clean_value(row.get(f"{id_prefix}_{i}"))
        if not accession:
            continue
        links.append({
            "link_category": category,
            "accession": accession,
            "url": _clean_value(row.get(f"{link_prefix}_{i}")),
            "related_accession": None,
            "related_url": None,
        })
    return links


def _ensure_sequence_link_table():
    with engine.connect() as conn:
        conn.execute(text(SEQUENCE_LINK_TABLE_DDL))
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


def update_enzyme_from_master():
    """Extract sequence, length, mass from the wide master.tsv."""
    df = pd.read_csv(MASTER_FILE, sep="\t", dtype=str)

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

    updated = 0
    with engine.connect() as conn:
        for _, row in df.iterrows():
            entry = row["Entry"]
            enzyme_id = entry_to_id.get(entry)
            if not enzyme_id:
                continue

            updates = {}
            if pd.notna(row.get(seq_col)):
                updates["sequence"] = str(row[seq_col])
            if len_col and pd.notna(row.get(len_col)):
                parsed_length = _clean_int(row.get(len_col))
                if parsed_length is not None:
                    updates["length"] = parsed_length
            if mass_col and pd.notna(row.get(mass_col)):
                try:
                    updates["mass"] = float(str(row[mass_col]).replace(",", ""))
                except (ValueError, TypeError):
                    pass

            if updates:
                set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                params = {k: v for k, v in updates.items()}
                params["enzyme_id"] = enzyme_id
                conn.execute(
                    text(f"UPDATE enzyme SET {set_clause} WHERE enzyme_id = :enzyme_id"),
                    params
                )
                updated += 1
        conn.commit()

    print(f"  enzyme (sequence update): {updated} rows updated")


def load_gene_info():
    """Load compact gene accession summary from sequence_links.tsv."""
    df = pd.read_csv(SEQ_LINKS_FILE, sep="\t", dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    # The accession file has no gene symbol, but the master file does — and it is
    # keyed by the same UniProt entry, so the name is free here.
    names_df = pd.read_csv(MASTER_FILE, sep="\t", dtype=str, usecols=["Entry", "Gene Names"])
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

        insdc_nuc_cols = [f"INSDC_Nuc_ID_{i}" for i in range(1, 25)]
        insdc_genbank_cols = [f"INSDC_Nuc_GenBank_Link_{i}" for i in range(1, 25)]
        insdc_prot_cols = [f"INSDC_Prot_ID_{i}" for i in range(1, 25)]
        refseq_nuc_cols = [f"RefSeq_Nuc_ID_{i}" for i in range(1, 12)]
        refseq_nuc_link_cols = [f"RefSeq_Nuc_Link_{i}" for i in range(1, 12)]
        refseq_prot_cols = [f"RefSeq_Prot_ID_{i}" for i in range(1, 12)]

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

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM gene"))
        conn.commit()

    if not rows:
        print("  gene: no rows to insert")
        return

    gene_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "gene_name", "genbank_id", "ncbi_url", "ena_accession", "protein_accession"]
    gene_df[cols].to_sql("gene", engine, if_exists="append", index=False)
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


def load_sequence_links():
    """Load all external sequence accessions from sequence_links.tsv."""
    _ensure_sequence_link_table()
    df = pd.read_csv(SEQ_LINKS_FILE, sep="\t", dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        for i in range(1, 25):
            _append_insdc_links(rows, enzyme_id, row, i)

        for i in range(1, 12):
            protein_accession = _clean_value(row.get(f"RefSeq_Prot_ID_{i}"))
            nucleotide_accession = _clean_value(row.get(f"RefSeq_Nuc_ID_{i}"))
            protein_url = _clean_value(row.get(f"RefSeq_Prot_Link_{i}"))
            nucleotide_url = _clean_value(row.get(f"RefSeq_Nuc_Link_{i}"))
            molecule = _clean_value(row.get(f"RefSeq_Molecule_{i}"))
            suffix = f" ({molecule})" if molecule else ""
            _append_sequence_link(rows, enzyme_id, f"RefSeq protein{suffix}", protein_accession, protein_url, nucleotide_accession, nucleotide_url)
            _append_sequence_link(rows, enzyme_id, f"RefSeq nucleotide{suffix}", nucleotide_accession, nucleotide_url, protein_accession, protein_url)

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM gene_sequence_link"))
        conn.commit()

    if not rows:
        print("  sequence links: no rows to insert")
        return

    link_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "link_category", "accession", "url", "related_accession", "related_url"]
    link_df[cols].to_sql("gene_sequence_link", engine, if_exists="append", index=False)
    print(f"  sequence links: {len(link_df)} rows inserted")


def load_evidence():
    """Load evidence from references.tsv."""
    _ensure_columns("evidence", EVIDENCE_COLUMNS)
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE evidence MODIFY COLUMN positions TEXT"))
        conn.commit()
    df = pd.read_csv(REFERENCES_FILE, sep="\t", dtype=str)

    enzyme_map = pd.read_sql("SELECT enzyme_id, uniprot_id FROM enzyme", engine)
    entry_to_id = dict(zip(enzyme_map["uniprot_id"], enzyme_map["enzyme_id"]))

    rows = []
    for _, row in df.iterrows():
        entry = row["Entry"]
        enzyme_id = entry_to_id.get(entry)
        if not enzyme_id:
            continue

        for i in _indexed_groups(df.columns, "PMID"):
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
                "review_status": "official",
            }
            if not any(v for k, v in evidence_row.items() if k not in {"enzyme_id", "source_description", "review_status"}):
                continue
            rows.append({
                **evidence_row,
            })

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM evidence"))
        conn.commit()

    if not rows:
        print("  evidence: no rows to insert")
        return

    ev_df = pd.DataFrame(rows).drop_duplicates()
    cols = [
        "enzyme_id", "pubmed_id", "doi", "title", "authors", "journal", "volume",
        "pages", "publication_year", "reference_type", "positions", "url",
        "source_description", "review_status",
    ]
    ev_df[cols].to_sql("evidence", engine, if_exists="append", index=False)
    print(f"  evidence: {len(ev_df)} rows inserted")


def load_go_terms():
    """Load Gene Ontology annotations from uniprotkb_go.tsv."""
    _ensure_table(GO_TABLE_DDL)
    df = pd.read_csv(GO_FILE, sep="\t", dtype=str)

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

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM enzyme_go"))
        conn.commit()

    if not rows:
        print("  GO terms: no rows to insert")
        return

    go_df = pd.DataFrame(rows).drop_duplicates()
    cols = ["enzyme_id", "go_id", "go_term", "go_url"]
    go_df[cols].to_sql("enzyme_go", engine, if_exists="append", index=False)
    print(f"  GO terms: {len(go_df)} rows inserted")


def load_isoforms():
    """Load isoform sequences from uniprotkb_isoform_sequences.tsv."""
    _ensure_table(ISOFORM_TABLE_DDL)
    df = pd.read_csv(ISOFORM_FILE, sep="\t", dtype=str)

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

    with engine.connect() as conn:
        conn.execute(text("DELETE FROM enzyme_isoform"))
        conn.commit()

    if not rows:
        print("  isoforms: no rows to insert")
        return

    isoform_df = pd.DataFrame(rows).drop_duplicates()
    cols = [
        "enzyme_id", "isoform_id", "isoform_length", "isoform_mass",
        "canonical_sequence", "canonical_length", "canonical_mass", "sequence",
    ]
    isoform_df[cols].to_sql("enzyme_isoform", engine, if_exists="append", index=False)
    print(f"  isoforms: {len(isoform_df)} rows inserted")


if __name__ == "__main__":
    update_enzyme_from_master()
    load_gene_info()
    load_sequence_links()
    load_evidence()
    load_go_terms()
    load_isoforms()


def run():
    update_enzyme_from_master()
    load_gene_info()
    load_sequence_links()
    load_evidence()
    load_go_terms()
    load_isoforms()
