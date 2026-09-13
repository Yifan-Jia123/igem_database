"""
Download service: field mapping, data fetching, and file generation for the two
Downloading-table pages.

Two export shapes exist, one per page:

* ``enzyme``  — one flat table, **one row per enzyme**. Every collected value that
  comes from a to-many relation (reaction / gene / compound / literature) is
  joined into a single cell with ``"; "`` in a deterministic order, because a
  one-row-per-enzyme table has nowhere else to put the second reaction.

* ``pathway`` — a ZIP laid out as the top folder::

      enzymes/enzymes.csv                     # the enzyme page's own table
      pathways/<route>/enzymes/step_1.csv     # enzymes catalysing step 1
      pathways/<route>/enzymes/step_2.csv
      pathways/<route>/enzymes/all_enzymes.csv  # every step + a leading Step column
      pathways/<route>/pathway_diagram.md     # per-step compounds and enzymes

  A step's enzymes are the ones the user picked in the pathway drawer. A step
  with no pick — the whole route for a plain map search — falls back to every
  enzyme in the database that catalyses that step's compound pair, and the
  diagram marks which steps were resolved that way.
"""

import csv
import io
import json
import os
import re
import zipfile
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Dict, Iterable, List, Optional, Tuple

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Compound,
    Enzyme,
    EnzymeReactionEdge,
    Evidence,
    Gene,
    Reaction,
    ReactionCompound,
)
from app.models._enums import CompoundRole
from app.utils.compound_filters import displayable_compound_filters

DOWNLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "downloads")

# CamelCase field name (from API) → {table, column, label}
FIELD_MAP: Dict[str, dict] = {
    # --- enzyme -----------------------------------------------------------
    "databaseCode":      {"table": "enzyme",   "column": "enzyme_id",       "label": "Database Code"},
    "primaryName":       {"table": "enzyme",   "column": "primary_name",    "label": "Primary Name"},
    "secondaryNames":    {"table": "enzyme",   "column": "secondary_names", "label": "Secondary Names"},
    "uniprotId":         {"table": "enzyme",   "column": "uniprot_id",      "label": "UniProt ID"},
    "organismName":      {"table": "enzyme",   "column": "organism_name",   "label": "Organism"},
    "length":            {"table": "enzyme",   "column": "length",          "label": "Length"},
    "mass":              {"table": "enzyme",   "column": "mass",            "label": "Mass (Da)"},
    "sequence":          {"table": "enzyme",   "column": "sequence",        "label": "Sequence"},
    "sourceType":        {"table": "enzyme",   "column": "source_type",     "label": "Source Type"},
    "reviewStatus":      {"table": "enzyme",   "column": "review_status",   "label": "Review Status"},
    # --- reaction ---------------------------------------------------------
    "ecNumber":          {"table": "reaction", "column": "ec_number",       "label": "EC Number"},
    "reactionEquation":  {"table": "reaction", "column": "equation",        "label": "Reaction Equation"},
    "rheaId":            {"table": "reaction", "column": "rhea_id",         "label": "Rhea ID"},
    "direction":         {"table": "reaction", "column": "direction",       "label": "Direction"},
    "reactionSmiles":    {"table": "reaction", "column": "smiles",          "label": "Reaction SMILES"},
    # --- gene -------------------------------------------------------------
    "geneName":          {"table": "gene",     "column": "gene_name",       "label": "Gene Name"},
    "genbankId":         {"table": "gene",     "column": "genbank_id",      "label": "GenBank ID"},
    "enaAccession":      {"table": "gene",     "column": "ena_accession",   "label": "ENA Accession"},
    "proteinAccession":  {"table": "gene",     "column": "protein_accession", "label": "Protein Accession"},
    # --- compound ---------------------------------------------------------
    "compoundName":      {"table": "compound", "column": "name",            "label": "Compound Name"},
    "chebiId":           {"table": "compound", "column": "chebi_id",        "label": "ChEBI ID"},
    "formula":           {"table": "compound", "column": "formula",         "label": "Formula"},
    "averageMass":       {"table": "compound", "column": "average_mass",    "label": "Compound Mass (Da)"},
    "compoundSmiles":    {"table": "compound", "column": "smiles",          "label": "Compound SMILES"},
    "inchiKey":          {"table": "compound", "column": "inchi_key",       "label": "InChI Key"},
    # --- literature -------------------------------------------------------
    "referenceTitle":    {"table": "evidence", "column": "title",           "label": "Reference Title"},
    "referenceAuthors":  {"table": "evidence", "column": "authors",         "label": "Reference Authors"},
    "journal":           {"table": "evidence", "column": "journal",         "label": "Journal"},
    "publicationYear":   {"table": "evidence", "column": "publication_year","label": "Publication Year"},
    "doi":               {"table": "evidence", "column": "doi",             "label": "DOI"},
    "pubmedId":          {"table": "evidence", "column": "pubmed_id",       "label": "PubMed ID"},
    "referenceUrl":      {"table": "evidence", "column": "url",             "label": "Reference URL"},
}

# Display order + grouping for the column picker. Mirrors FIELD_MAP's tables.
FIELD_GROUPS: List[Tuple[str, str]] = [
    ("enzyme",   "Enzyme"),
    ("reaction", "Reaction"),
    ("gene",     "Gene"),
    ("compound", "Compound"),
    ("literature", "Literature"),
]

# `table` in FIELD_MAP uses "evidence"; the picker labels that group "Literature".
GROUP_TABLE_ALIAS = {"literature": "evidence"}

DEFAULT_FIELDS: List[str] = [
    "databaseCode", "primaryName", "uniprotId", "organismName", "geneName",
    "ecNumber", "reactionEquation", "length", "mass", "sourceType", "doi", "pubmedId",
]

# FASTA is not a table: one header line, one sequence. Only these four fields
# can go into the header, and the sequence is appended to every record whatever
# the picker said — so a pick of `sequence` alone still yields a valid file.
FASTA_FIELDS = ("uniprotId", "organismName", "primaryName", "geneName")
FASTA_REQUIRED_FIELDS = ("sequence",)

# format → human label. Anything not here is rejected; the old code had no `else`
# branch, so "XLSX"/"TXT" silently wrote no file at all and still reported success.
# The order is the order the page offers them in; FASTA leads because it is the
# format this database exists to hand out.
SUPPORTED_FORMATS: Dict[str, str] = {
    "fasta": "FASTA — one sequence per enzyme",
    "xlsx":  "Excel workbook",
    "csv":   "CSV — comma separated",
    "tsv":   "TSV — tab separated",
    "json":  "JSON array",
}
PATHWAY_FORMATS: Dict[str, str] = {"zip": "ZIP — folder tree + Markdown diagram"}

# Which page may ask for which format.
PAGE_FORMATS: Dict[str, Dict[str, str]] = {
    "enzyme":  SUPPORTED_FORMATS,
    "pathway": PATHWAY_FORMATS,
}

# The download_type allowlist. It names the output file, so it doubles as the
# guard against a caller walking out of DOWNLOADS_DIR with "../".
DOWNLOAD_TYPES = ("enzyme", "pathway")

MAX_CELL_CHARS = 32000  # Excel's hard limit is 32767; leave headroom.
FALLBACK_TOKEN = "export"


class DownloadError(ValueError):
    """A request the service refuses to serve (bad format, bad download type)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _get(obj, name: str, default=None):
    """Read `name` from a pydantic model/dataclass or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def fasta_fields(accepted: List[str]) -> List[str]:
    """The FASTA header fields a pick resolves to, in the order they were picked.

    FASTA is one header line and one sequence, not a table, so a column outside
    FASTA_FIELDS has nowhere to go and is dropped. `databaseCode` stands in when
    nothing header-worthy was picked, so a header is never a bare ">".
    """
    header = [f for f in accepted if f in FASTA_FIELDS]
    return header or ["databaseCode"]


def fasta_fetch_fields(accepted: List[str]) -> List[str]:
    """The keys a FASTA export must have in hand: header fields plus the body."""
    return _dedupe(fasta_fields(accepted) + list(FASTA_REQUIRED_FIELDS))


def _dedupe(values: Iterable) -> List:
    """Order-preserving de-duplication, skipping empties."""
    seen = set()
    out = []
    for value in values:
        if value is None or value == "":
            continue
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _val(value) -> str:
    """One cell value as text.

    Enum members are unwrapped to their value: `str(Direction.forward)` renders as
    "Direction.forward", which is what used to leak into every exported CSV.
    """
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (list, tuple, set)):
        return "; ".join(_val(v) for v in value if v not in (None, ""))
    return str(value)


def _joined(values: Iterable) -> str:
    """Collapse a to-many relation into one cell, order preserved, no repeats."""
    return "; ".join(_dedupe(_val(v) for v in values if _val(v) != ""))


def _clip(text: str) -> str:
    """Keep a cell inside Excel's limit and strip control characters."""
    text = "".join(ch for ch in text if ch == "\t" or ch == "\n" or ord(ch) >= 32)
    if len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS] + " …[truncated]"
    return text


def _safe_token(value: str, fallback: str = FALLBACK_TOKEN, limit: int = 40) -> str:
    """A filesystem- and zip-safe name fragment. Never contains a path separator.

    Route labels arrive as "A → B" and compound names carry punctuation like
    "(-)-alpha-gurjunene", so runs of replaced characters are collapsed — without
    that, a folder ends up called `A_____B`.
    """
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    token = re.sub(r"_{2,}", "_", token).strip("._-")
    return (token or fallback)[:limit]


def resolve_format(download_type: str, fmt: Optional[str]) -> str:
    """Validate `format` case-insensitively against what this page may produce."""
    allowed = PAGE_FORMATS.get(download_type)
    if allowed is None:
        raise DownloadError(f"Unknown download type: {download_type!r}")
    candidate = (fmt or "csv").strip().lower()
    if candidate not in allowed:
        raise DownloadError(
            f"Format {fmt!r} is not available for a {download_type} download. "
            f"Choose one of: {', '.join(sorted(allowed))}."
        )
    return candidate


def resolve_fields(fields: Optional[List[str]]) -> Tuple[List[str], List[str]]:
    """Split requested fields into (accepted, unknown), order preserved."""
    accepted, unknown = [], []
    for field in fields or []:
        if field in FIELD_MAP:
            if field not in accepted:  # the old code emitted duplicate columns
                accepted.append(field)
        else:
            unknown.append(field)
    return accepted, unknown


def field_catalog() -> dict:
    """The column picker's data source, so the frontend need not mirror FIELD_MAP."""
    groups = []
    for key, label in FIELD_GROUPS:
        table = GROUP_TABLE_ALIAS.get(key, key)
        members = [
            {"key": name, "label": spec["label"]}
            for name, spec in FIELD_MAP.items()
            if spec["table"] == table
        ]
        if members:
            groups.append({"key": key, "label": label, "fields": members})
    return {
        "groups": groups,
        "defaultFields": DEFAULT_FIELDS,
        "formats": {
            page: [{"key": k, "label": v} for k, v in fmts.items()]
            for page, fmts in PAGE_FORMATS.items()
        },
    }


# ---------------------------------------------------------------------------
# data fetching
# ---------------------------------------------------------------------------

async def _fetch_enzyme_rows(
    db: AsyncSession,
    enzyme_ids: List[str],
    fields: List[str],
) -> List[Dict[str, str]]:
    """One row per enzyme id that exists, in the order given.

    Every to-many relation is gathered in full and ordered by its primary key, so
    repeated exports of the same enzyme produce byte-identical files. (The old
    implementation kept only the first row of each relation in whatever order
    MySQL happened to return, which also made multi-reaction enzymes lose data.)
    """
    ids = [i for i in _dedupe(enzyme_ids) if i]
    if not ids:
        return []

    wanted = [f for f in fields if f in FIELD_MAP]

    result = await db.execute(select(Enzyme).where(Enzyme.enzyme_id.in_(ids)))
    enzymes = {e.enzyme_id: e for e in result.scalars().all()}

    edge_result = await db.execute(
        select(EnzymeReactionEdge.enzyme_id, Reaction)
        .join(Reaction, EnzymeReactionEdge.reaction_id == Reaction.reaction_id)
        .where(EnzymeReactionEdge.enzyme_id.in_(ids))
        .order_by(EnzymeReactionEdge.enzyme_id, Reaction.reaction_id)
    )
    reactions_by_enzyme: Dict[str, List[Reaction]] = {}
    for enzyme_id, reaction in edge_result.all():
        reactions_by_enzyme.setdefault(enzyme_id, []).append(reaction)

    gene_result = await db.execute(
        select(Gene).where(Gene.enzyme_id.in_(ids)).order_by(Gene.enzyme_id, Gene.gene_id)
    )
    genes_by_enzyme: Dict[str, List[Gene]] = {}
    for gene in gene_result.scalars().all():
        genes_by_enzyme.setdefault(gene.enzyme_id, []).append(gene)

    evidence_result = await db.execute(
        select(Evidence)
        .where(Evidence.enzyme_id.in_(ids))
        .order_by(Evidence.enzyme_id, Evidence.evidence_id)
    )
    evidence_by_enzyme: Dict[str, List[Evidence]] = {}
    for item in evidence_result.scalars().all():
        evidence_by_enzyme.setdefault(item.enzyme_id, []).append(item)

    all_reaction_ids = [r.reaction_id for rows in reactions_by_enzyme.values() for r in rows]
    compounds_by_reaction: Dict[str, List[Compound]] = {}
    if all_reaction_ids:
        compound_result = await db.execute(
            select(ReactionCompound.reaction_id, Compound)
            .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
            .where(ReactionCompound.reaction_id.in_(all_reaction_ids))
            .where(*displayable_compound_filters())
            .order_by(ReactionCompound.reaction_id, Compound.compound_id)
        )
        for reaction_id, compound in compound_result.all():
            compounds_by_reaction.setdefault(reaction_id, []).append(compound)

    rows: List[Dict[str, str]] = []
    for enzyme_id in ids:
        enzyme = enzymes.get(enzyme_id)
        if enzyme is None:
            continue
        enzyme_reactions = reactions_by_enzyme.get(enzyme_id, [])
        enzyme_genes = genes_by_enzyme.get(enzyme_id, [])
        enzyme_evidence = evidence_by_enzyme.get(enzyme_id, [])
        enzyme_compounds = [
            compound
            for reaction in enzyme_reactions
            for compound in compounds_by_reaction.get(reaction.reaction_id, [])
        ]

        row: Dict[str, str] = {}
        for field in wanted:
            spec = FIELD_MAP[field]
            table, column = spec["table"], spec["column"]
            if table == "enzyme":
                row[field] = _val(getattr(enzyme, column, None))
            elif table == "reaction":
                row[field] = _joined(getattr(r, column, None) for r in enzyme_reactions)
            elif table == "gene":
                row[field] = _joined(getattr(g, column, None) for g in enzyme_genes)
            elif table == "compound":
                row[field] = _joined(getattr(c, column, None) for c in enzyme_compounds)
            elif table == "evidence":
                row[field] = _joined(getattr(e, column, None) for e in enzyme_evidence)
            else:  # pragma: no cover - FIELD_MAP only names the five tables above
                row[field] = ""
        rows.append(row)

    return rows


async def _resolve_step_enzyme_ids(
    db: AsyncSession,
    source_id: str,
    target_id: str,
) -> List[str]:
    """Every enzyme in the database that catalyses source → target.

    Used for steps with no hand-picked enzyme: a route queued straight off the map
    never went through the enzyme picker, so the honest reading of "the enzymes
    used by this pathway" is all enzymes known to catalyse each step.
    """
    if not source_id or not target_id:
        return []

    as_substrate = select(ReactionCompound.reaction_id).where(
        ReactionCompound.compound_id == source_id,
        ReactionCompound.role == CompoundRole.substrate,
    )
    as_product = select(ReactionCompound.reaction_id).where(
        ReactionCompound.compound_id == target_id,
        ReactionCompound.role == CompoundRole.product,
        ReactionCompound.reaction_id.in_(as_substrate),
    )
    reaction_ids = list((await db.execute(as_product)).scalars().all())
    if not reaction_ids:
        return []

    edge_result = await db.execute(
        select(EnzymeReactionEdge.enzyme_id)
        .where(EnzymeReactionEdge.reaction_id.in_(reaction_ids))
        .order_by(EnzymeReactionEdge.enzyme_id)
    )
    return _dedupe(edge_id for (edge_id,) in edge_result.all())


# ---------------------------------------------------------------------------
# writers — flat formats
# ---------------------------------------------------------------------------

def _header_and_cells(row: Dict[str, str], fields: List[str]) -> List[str]:
    return [_clip(row.get(f, "")) for f in fields]


def _write_delimited(filepath: str, rows: List[dict], fields: List[str], delimiter: str):
    headers = [FIELD_MAP[f]["label"] for f in fields]
    with open(filepath, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(_header_and_cells(row, fields))


def _write_json(filepath: str, rows: List[dict], fields: List[str]):
    payload = [
        {FIELD_MAP[f]["label"]: row.get(f, "") for f in fields}
        for row in rows
    ]
    with open(filepath, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _write_xlsx(filepath: str, rows: List[dict], fields: List[str], sheet_title: str = "Export"):
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet(title=_safe_token(sheet_title, "Export", 31))
    sheet.append([FIELD_MAP[f]["label"] for f in fields])
    for row in rows:
        sheet.append(_header_and_cells(row, fields))
    workbook.save(filepath)


def _write_fasta(filepath: str, rows: List[dict], header_fields: List[str]):
    """FASTA body: one record per enzyme that has a sequence.

    `header_fields` are the picked header fields, joined with "|" — the header's
    shape is FASTA's, not a column list, which is why the picker is limited to
    the four fields that read well there. A record with no sequence is skipped
    rather than written as an empty stub.
    """
    written = 0
    with open(filepath, "w", encoding="utf-8") as handle:
        for row in rows:
            sequence = row.get("sequence", "")
            if not sequence:
                continue
            header = "|".join(_val(row.get(f, "")) for f in header_fields)
            handle.write(f">{header}\n{sequence}\n")
            written += 1
    return written


# ---------------------------------------------------------------------------
# writers — the pathway folder tree
# ---------------------------------------------------------------------------

def _pathway_diagram_md(
    title: str,
    start_label: str,
    end_label: str,
    steps: List[dict],
) -> str:
    """A short Markdown sketch of the route, naming each step's compounds and enzymes."""
    lines = [f"# {title}", ""]
    lines.append(f"`{start_label}` → `{end_label}`")
    lines.append("")
    lines.append(f"**{len(steps)} step{'s' if len(steps) != 1 else ''}**")
    lines.append("")

    chain = " → ".join(
        [steps[0]["source_name"] if steps else start_label]
        + [s["target_name"] for s in steps]
    )
    lines.append("## Route")
    lines.append("")
    lines.append(f"    {chain}")
    lines.append("")
    lines.append("## Steps")
    lines.append("")

    for step in steps:
        lines.append(f"### Step {step['step']}: {step['source_name']} → {step['target_name']}")
        lines.append("")
        lines.append(f"- Substrate: {step['source_name']} (`{step['source_id']}`)")
        lines.append(f"- Product: {step['target_name']} (`{step['target_id']}`)")
        if step["enzymes"]:
            provenance = "chosen in the pathway drawer" if step["picked"] else "all database enzymes for this step"
            lines.append(f"- Enzymes ({len(step['enzymes'])}, {provenance}):")
            lines.append("")
            lines.append("| # | Database code | Enzyme | Organism | UniProt |")
            lines.append("| - | ------------- | ------ | -------- | ------- |")
            for index, enzyme in enumerate(step["enzymes"], start=1):
                lines.append(
                    f"| {index} | {enzyme['enzyme_id']} | {enzyme['primary_name']} "
                    f"| {enzyme['organism_name']} | {enzyme['uniprot_id']} |"
                )
        else:
            lines.append("- Enzymes: none found in the database for this step")
        lines.append("")

    return "\n".join(lines) + "\n"


async def _build_pathway_steps(
    db: AsyncSession,
    item,
) -> List[dict]:
    """Resolve one queued route into ordered step dicts with their enzymes."""
    compound_ids = [c for c in (_get(item, "compound_ids") or []) if c]
    compound_names = list(_get(item, "compound_names") or [])
    picked_steps = {s.step: s for s in (_get(item, "steps") or []) if _get(s, "step")}

    def name_for(index: int, fallback_id: str) -> str:
        if 0 <= index < len(compound_names) and compound_names[index]:
            return compound_names[index]
        return fallback_id

    steps: List[dict] = []
    for index in range(max(len(compound_ids) - 1, 0)):
        step_number = index + 1
        source_id = compound_ids[index]
        target_id = compound_ids[index + 1]
        picked = picked_steps.get(step_number)
        source_name = name_for(index, source_id)
        target_name = name_for(index + 1, target_id)

        picked_enzymes = list(_get(picked, "enzymes") or []) if picked else []
        picked_ids = [e for e in (_get(entry, "enzyme_id") for entry in picked_enzymes) if e]

        if picked_ids:
            enzyme_ids = _dedupe(picked_ids)
            picked_flag = True
        else:
            enzyme_ids = await _resolve_step_enzyme_ids(db, source_id, target_id)
            picked_flag = False

        details = []
        if enzyme_ids:
            detail_result = await db.execute(
                select(Enzyme).where(Enzyme.enzyme_id.in_(enzyme_ids))
            )
            by_id = {e.enzyme_id: e for e in detail_result.scalars().all()}
            for enzyme_id in enzyme_ids:
                enzyme = by_id.get(enzyme_id)
                details.append({
                    "enzyme_id": enzyme_id,
                    "primary_name": _val(getattr(enzyme, "primary_name", "")) if enzyme else "",
                    "organism_name": _val(getattr(enzyme, "organism_name", "")) if enzyme else "",
                    "uniprot_id": _val(getattr(enzyme, "uniprot_id", "")) if enzyme else "",
                })

        steps.append({
            "step": step_number,
            "source_id": source_id,
            "source_name": source_name,
            "target_id": target_id,
            "target_name": target_name,
            "picked": picked_flag,
            "enzyme_ids": enzyme_ids,
            "enzymes": details,
        })

    return steps


async def _write_pathway_zip(
    filepath: str,
    db: AsyncSession,
    items: list,
    fields: List[str],
    enzyme_items: Optional[list] = None,
) -> Dict[str, int]:
    """Build the top folder: the enzyme page's table plus one folder per route."""
    used_names: Dict[str, int] = {}
    stats = {"pathways": 0, "steps": 0, "enzymes": 0, "enzyme_rows": 0}

    with zipfile.ZipFile(filepath, "w", zipfile.ZIP_DEFLATED) as archive:
        # --- the enzyme page's own table, when the queue had enzymes ---------
        if enzyme_items:
            enzyme_ids = [i for i in (_get(item, "entity_id") for item in enzyme_items) if i]
            enzyme_rows = await _fetch_enzyme_rows(db, enzyme_ids, fields)
            stats["enzyme_rows"] = len(enzyme_rows)
            if enzyme_rows:
                buffer = io.StringIO()
                writer = csv.writer(buffer, delimiter=",")
                writer.writerow([FIELD_MAP[f]["label"] for f in fields])
                for row in enzyme_rows:
                    writer.writerow(_header_and_cells(row, fields))
                archive.writestr("enzymes/enzymes.csv", buffer.getvalue().encode("utf-8-sig"))

        # --- one folder per route --------------------------------------------
        for item in items:
            label = _val(_get(item, "display_label")) or _val(_get(item, "entity_id")) or "pathway"
            base = _safe_token(label, "pathway")
            used_names[base] = used_names.get(base, 0) + 1
            folder = f"pathways/{base}" if used_names[base] == 1 else f"pathways/{base}_{used_names[base]}"

            steps = await _build_pathway_steps(db, item)
            if not steps:
                continue
            stats["pathways"] += 1

            all_rows: List[Dict[str, str]] = []
            for step in steps:
                rows = await _fetch_enzyme_rows(db, step["enzyme_ids"], fields)
                stats["steps"] += 1
                stats["enzymes"] += len(rows)

                if rows:
                    buffer = io.StringIO()
                    writer = csv.writer(buffer, delimiter=",")
                    writer.writerow([FIELD_MAP[f]["label"] for f in fields])
                    for row in rows:
                        writer.writerow(_header_and_cells(row, fields))
                    archive.writestr(
                        f"{folder}/enzymes/step_{step['step']}.csv",
                        buffer.getvalue().encode("utf-8-sig"),
                    )

                for row in rows:
                    all_rows.append({"step": str(step["step"]), **row})

            if all_rows:
                buffer = io.StringIO()
                writer = csv.writer(buffer, delimiter=",")
                writer.writerow(["Step"] + [FIELD_MAP[f]["label"] for f in fields])
                for row in all_rows:
                    writer.writerow([row.get("step", "")] + _header_and_cells(row, fields))
                archive.writestr(
                    f"{folder}/enzymes/all_enzymes.csv",
                    buffer.getvalue().encode("utf-8-sig"),
                )

            diagram = _pathway_diagram_md(
                title=label,
                start_label=steps[0]["source_name"],
                end_label=steps[-1]["target_name"],
                steps=steps,
            )
            archive.writestr(f"{folder}/pathway_diagram.md", diagram.encode("utf-8"))

    return stats


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

async def preview(
    download_type: str,
    items: list,
    fields: List[str],
    format: str,
    db: AsyncSession,
    enzyme_items: Optional[list] = None,
) -> Tuple[List[str], int, str, List[str]]:
    """Real columns, a real row count, and any field names that were ignored.

    This used to return `len(items)` without touching the database, so it counted
    ids that do not exist and entity types that produce no rows at all.
    """
    resolved_format = resolve_format(download_type, format)
    accepted, unknown = resolve_fields(fields)
    # The columns to report are the ones the file will show, which for FASTA is
    # the header it will write rather than the pick it was given.
    reported = accepted
    if resolved_format == "fasta":
        reported = list(fasta_fields(accepted)) + list(FASTA_REQUIRED_FIELDS)
        accepted = fasta_fetch_fields(accepted)

    if download_type == "pathway":
        row_count = 0
        for item in items:
            steps = await _build_pathway_steps(db, item)
            for step in steps:
                row_count += len(step["enzyme_ids"])
        if enzyme_items:
            row_count += len(await _fetch_enzyme_rows(
                db, [_get(i, "entity_id") for i in enzyme_items], accepted))
    else:
        row_count = len(await _fetch_enzyme_rows(
            db, [_get(i, "entity_id") for i in items], accepted))

    return (
        [FIELD_MAP[f]["label"] for f in reported],
        row_count,
        _suggested_filename(download_type, resolved_format),
        unknown,
    )


def _suggested_filename(download_type: str, fmt: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{_safe_token(download_type, 'export')}_{ts}.{fmt}"


async def generate_file(
    download_type: str,
    items: list,
    fields: List[str],
    format: str,
    db: AsyncSession,
    enzyme_items: Optional[list] = None,
) -> Tuple[str, str, Dict[str, int], List[str]]:
    """Write the export. Returns (file_url, status, stats, unknown_fields)."""
    if download_type not in DOWNLOAD_TYPES:
        raise DownloadError(f"Unknown download type: {download_type!r}")

    resolved_format = resolve_format(download_type, format)
    accepted, unknown = resolve_fields(fields)
    fasta_header: List[str] = []
    if resolved_format == "fasta":
        fasta_header = fasta_fields(accepted)
        accepted = fasta_fetch_fields(accepted)
    if not accepted:
        # Every writer emits `fields` as its header row, so an empty pick would
        # produce a columnless file that still reports success.
        raise DownloadError("Pick at least one column to export.")

    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    filename = _suggested_filename(download_type, resolved_format)
    filepath = os.path.join(DOWNLOADS_DIR, filename)

    if download_type == "pathway":
        stats = await _write_pathway_zip(filepath, db, items, accepted, enzyme_items)
    else:
        rows = await _fetch_enzyme_rows(
            db, [_get(i, "entity_id") for i in items], accepted)
        stats = {"enzyme_rows": len(rows)}
        if resolved_format == "csv":
            _write_delimited(filepath, rows, accepted, ",")
        elif resolved_format == "tsv":
            _write_delimited(filepath, rows, accepted, "\t")
        elif resolved_format == "json":
            _write_json(filepath, rows, accepted)
        elif resolved_format == "xlsx":
            _write_xlsx(filepath, rows, accepted, "Enzymes")
        elif resolved_format == "fasta":
            stats["sequences"] = _write_fasta(filepath, rows, fasta_header)

    return f"/api/v1/downloads/{filename}", "ready", stats, unknown
