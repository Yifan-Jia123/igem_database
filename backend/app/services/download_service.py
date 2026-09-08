"""
Download service: field mapping, data fetching, and file generation for entry/pathway exports.
"""

import os
import csv
import json
import io
import zipfile
from datetime import datetime
from typing import List, Dict, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Enzyme, Gene, Reaction, Compound, Evidence, EnzymeReactionEdge, ReactionCompound
)
from app.utils.compound_filters import displayable_compound_filters

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")

# CamelCase field name (from API) → {table, column, label}
FIELD_MAP: Dict[str, dict] = {
    "primaryName":       {"table": "enzyme",   "column": "primary_name",    "label": "Primary Name"},
    "organismName":      {"table": "enzyme",   "column": "organism_name",   "label": "Organism"},
    "sequence":          {"table": "enzyme",   "column": "sequence",        "label": "Sequence"},
    "length":            {"table": "enzyme",   "column": "length",          "label": "Length"},
    "mass":              {"table": "enzyme",   "column": "mass",            "label": "Mass (Da)"},
    "uniprotId":         {"table": "enzyme",   "column": "uniprot_id",      "label": "UniProt ID"},
    "databaseCode":      {"table": "enzyme",   "column": "enzyme_id",       "label": "Database Code"},
    "ecNumber":          {"table": "reaction", "column": "ec_number",       "label": "EC Number"},
    "reactionEquation":  {"table": "reaction", "column": "equation",        "label": "Reaction Equation"},
    "direction":         {"table": "reaction", "column": "direction",       "label": "Direction"},
    "smiles":            {"table": "reaction", "column": "smiles",          "label": "SMILES"},
    "chebiId":           {"table": "compound", "column": "chebi_id",        "label": "ChEBI ID"},
    "averageMass":       {"table": "compound", "column": "average_mass",    "label": "Average Mass"},
    "inchiKey":          {"table": "compound", "column": "inchi_key",        "label": "InChI Key"},
    "geneName":          {"table": "gene",     "column": "gene_name",       "label": "Gene Name"},
    "genbankId":         {"table": "gene",     "column": "genbank_id",      "label": "GenBank ID"},
    "enaAccession":      {"table": "gene",     "column": "ena_accession",   "label": "ENA Accession"},
    "proteinAccession":  {"table": "gene",     "column": "protein_accession","label": "Protein Accession"},
    "doi":               {"table": "evidence", "column": "doi",             "label": "DOI"},
    "pubmedId":          {"table": "evidence", "column": "pubmed_id",       "label": "PubMed ID"},
    "referenceTitle":    {"table": "evidence", "column": "title",           "label": "Reference Title"},
    "referenceAuthors":  {"table": "evidence", "column": "authors",         "label": "Reference Authors"},
    "journal":           {"table": "evidence", "column": "journal",         "label": "Journal"},
    "publicationYear":   {"table": "evidence", "column": "publication_year","label": "Publication Year"},
    "referenceUrl":      {"table": "evidence", "column": "url",             "label": "Reference URL"},
    "sourceType":        {"table": "enzyme",   "column": "source_type",     "label": "Source Type"},
    "reviewStatus":      {"table": "enzyme",   "column": "review_status",   "label": "Review Status"},
}


async def preview(
    download_type: str,
    items: list,
    fields: List[str],
    format: str,
    db: AsyncSession,
) -> Tuple[List[str], int, str]:
    """Return columns, rowCount, and estimated filename."""
    columns = _resolve_columns(fields)
    row_count = len(items)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{download_type}_{ts}.{format}"

    return columns, row_count, filename


async def generate_file(
    download_type: str,
    items: list,
    fields: List[str],
    format: str,
    db: AsyncSession,
    include_external_links: bool = False,
    include_graph_image: bool = False,
) -> Tuple[str, str]:
    """
    Generate download file. Returns (file_url, status).

    file_url is relative like /api/v1/downloads/filename.ext
    """

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{download_type}_{ts}.{format}"
    filepath = os.path.join(DOWNLOADS_DIR, filename)

    if format == "fasta":
        rows = await _fetch_entry_data(db, items, fields, download_type)
        _write_fasta(filepath, rows, fields)
    elif format == "zip":
        await _write_zip(filepath, db, items, fields, download_type, include_graph_image)
    else:
        rows = await _fetch_entry_data(db, items, fields, download_type)
        if format in ("csv", "tsv"):
            delimiter = "\t" if format == "tsv" else ","
            _write_delimited(filepath, rows, fields, delimiter)
        elif format == "json":
            _write_json(filepath, rows, fields)

    url = f"/api/v1/downloads/{filename}"
    return url, "ready"


def _resolve_columns(fields: List[str]) -> List[str]:
    """Map API field names to human-readable column labels."""
    return [FIELD_MAP[f]["label"] for f in fields if f in FIELD_MAP]


def _item_value(item, snake_key: str, camel_key: str):
    if hasattr(item, snake_key):
        return getattr(item, snake_key)
    if isinstance(item, dict):
        return item.get(snake_key) or item.get(camel_key)
    return None


async def _fetch_entry_data(
    db: AsyncSession,
    items: list,
    fields: List[str],
    download_type: str,
) -> List[Dict[str, str]]:
    """Fetch data for each item according to requested fields."""

    enzyme_ids = set()
    compound_ids = set()
    for item in items:
        entity_id = _item_value(item, "entity_id", "entityId")
        entity_type = (_item_value(item, "entity_type", "entityType") or "").lower()
        if not entity_id:
            continue
        if entity_type == "compound" or str(entity_id).startswith("CHEBI:"):
            compound_ids.add(entity_id)
        else:
            enzyme_ids.add(entity_id)

    if not enzyme_ids and not compound_ids:
        return []

    # Fetch enzymes
    result = await db.execute(
        select(Enzyme).where(Enzyme.enzyme_id.in_(list(enzyme_ids)))
    )
    enzymes: Dict[str, Enzyme] = {e.enzyme_id: e for e in result.scalars().all()}

    compound_result = await db.execute(
        select(Compound).where(Compound.compound_id.in_(list(compound_ids)))
    )
    compounds: Dict[str, Compound] = {c.compound_id: c for c in compound_result.scalars().all()}

    # Fetch first reaction per enzyme
    edge_result = await db.execute(
        select(EnzymeReactionEdge, Reaction)
        .join(Reaction, EnzymeReactionEdge.reaction_id == Reaction.reaction_id)
        .where(EnzymeReactionEdge.enzyme_id.in_(list(enzyme_ids)))
    )
    enzyme_reactions: Dict[str, Reaction] = {}
    for ere, rxn in edge_result:
        if ere.enzyme_id not in enzyme_reactions:
            enzyme_reactions[ere.enzyme_id] = rxn

    reaction_compounds: Dict[str, Compound] = {}
    reaction_ids = [rxn.reaction_id for rxn in enzyme_reactions.values()]
    if reaction_ids:
        cpd_result = await db.execute(
            select(ReactionCompound, Compound)
            .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
            .where(ReactionCompound.reaction_id.in_(reaction_ids))
            .where(*displayable_compound_filters())
        )
        for rc, cpd in cpd_result:
            if rc.reaction_id not in reaction_compounds:
                reaction_compounds[rc.reaction_id] = cpd

    # Fetch gene info
    gene_result = await db.execute(
        select(Gene).where(Gene.enzyme_id.in_(list(enzyme_ids)))
    )
    enzyme_genes: Dict[str, Gene] = {}
    for g in gene_result.scalars():
        if g.enzyme_id not in enzyme_genes:
            enzyme_genes[g.enzyme_id] = g

    # Fetch first evidence per enzyme
    ev_result = await db.execute(
        select(Evidence).where(Evidence.enzyme_id.in_(list(enzyme_ids)))
    )
    enzyme_evidences: Dict[str, Evidence] = {}
    for ev in ev_result.scalars():
        if ev.enzyme_id not in enzyme_evidences:
            enzyme_evidences[ev.enzyme_id] = ev

    # Build rows
    rows = []
    for item in items:
        entity_id = _item_value(item, "entity_id", "entityId")
        entity_type = (_item_value(item, "entity_type", "entityType") or "").lower()

        if entity_type == "compound" or str(entity_id).startswith("CHEBI:"):
            cpd = compounds.get(entity_id)
            if not cpd:
                continue

            row = {}
            for field in fields:
                if field not in FIELD_MAP:
                    continue
                cfg = FIELD_MAP[field]
                if cfg["table"] == "compound":
                    row[field] = _val(getattr(cpd, cfg["column"], None))
                else:
                    row[field] = ""
            rows.append(row)
            continue

        eid = entity_id
        enz = enzymes.get(eid) if eid else None
        if not enz:
            continue

        rxn = enzyme_reactions.get(eid)
        gene = enzyme_genes.get(eid)
        ev = enzyme_evidences.get(eid)
        cpd = reaction_compounds.get(rxn.reaction_id) if rxn else None

        row = {}
        for field in fields:
            if field not in FIELD_MAP:
                continue
            cfg = FIELD_MAP[field]
            table = cfg["table"]
            col = cfg["column"]

            if table == "enzyme":
                row[field] = _val(getattr(enz, col, None))
            elif table == "reaction":
                row[field] = _val(getattr(rxn, col, None)) if rxn else ""
            elif table == "gene":
                row[field] = _val(getattr(gene, col, None)) if gene else ""
            elif table == "compound":
                row[field] = _val(getattr(cpd, col, None)) if cpd else ""
            elif table == "evidence":
                row[field] = _val(getattr(ev, col, None)) if ev else ""

        rows.append(row)

    return rows


def _val(v) -> str:
    """Convert value to string for export."""
    if v is None:
        return ""
    return str(v)


def _write_delimited(filepath: str, rows: List[dict], fields: List[str], delimiter: str):
    """Write CSV or TSV file."""
    valid_fields = [f for f in fields if f in FIELD_MAP]
    headers = [FIELD_MAP[f]["label"] for f in valid_fields]
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(f, "") for f in valid_fields])


def _write_json(filepath: str, rows: List[dict], fields: List[str]):
    """Write JSON file."""
    valid_fields = [f for f in fields if f in FIELD_MAP]
    output = []
    for row in rows:
        item = {FIELD_MAP[f]["label"]: row.get(f, "") for f in valid_fields}
        output.append(item)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def _write_fasta(filepath: str, rows: List[dict], fields: List[str]):
    """Write FASTA file. Each entry requires enzyme_id and sequence."""
    with open(filepath, "w", encoding="utf-8") as f:
        for row in rows:
            header = f">enzyme_id:{row.get('databaseCode','')}|{row.get('primaryName','')}"
            sequence = row.get("sequence", "")
            if sequence:
                f.write(f"{header}\n{sequence}\n")


async def _write_zip(
    filepath: str,
    db: AsyncSession,
    items: list,
    fields: List[str],
    download_type: str,
    include_graph_image: bool,
):
    """Write a ZIP bundle (TSV + optional graph SVG)."""
    rows = await _fetch_entry_data(db, items, fields, download_type)
    valid_fields = [f for f in fields if f in FIELD_MAP]

    with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add data as TSV
        tsv_buffer = io.StringIO()
        writer = csv.writer(tsv_buffer, delimiter="\t")
        headers = [FIELD_MAP[f]["label"] for f in valid_fields]
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(f, "") for f in valid_fields])
        zf.writestr("data.tsv", tsv_buffer.getvalue().encode("utf-8-sig"))

        # Add FASTA
        fasta_lines = io.StringIO()
        for row in rows:
            seq = row.get("sequence", "")
            if seq:
                fasta_lines.write(f">enzyme_id:{row.get('databaseCode','')}|{row.get('primaryName','')}\n{seq}\n")
        zf.writestr("sequences.fasta", fasta_lines.getvalue().encode("utf-8"))
