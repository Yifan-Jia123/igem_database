"""Real NCBI BLAST+ search over the local enzyme library.

Subjects = the canonical ``Enzyme.sequence`` of every enzyme that has one
(996) plus the true isoform variants from ``EnzymeIsoform`` whose sequence
differs from their canonical sequence (29). Each subject is written to a
local FASTA whose defline is a plain integer index; a parallel ``list`` maps
index -> (enzyme_id, isoform_id). ``makeblastdb`` formats the set once and the
result is reused while the subject signature (ids + lengths) is unchanged.

NCBI binaries (blastp.exe / makeblastdb.exe) are expected in the directory
pointed to by ``settings.blast_bin_dir`` (or on PATH). If they are missing a
clear ``RuntimeError`` is raised so the API can surface a helpful message.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Enzyme, EnzymeIsoform, EnzymeReactionEdge, Gene, Reaction
from app.schemas.blast import BlastHit, BlastPayload, BlastSearchRequest
from app.schemas.enzyme import EnzymeCard

# backend root = .../backend (blast_service.py lives in app/services/)
BACKEND_ROOT = Path(__file__).resolve().parents[2]

_EXE_SUFFIX = ".exe" if os.name == "nt" else ""
_FASTA_HEADER_RE = re.compile(r"^>.*$", re.MULTILINE)
_NON_ALPHA_RE = re.compile(r"[^A-Za-z]")
_MIN_QUERY_AA = 15
# UniProt can carry ambiguous letters blastp does not accept; map them to the
# closest standard residue before alignment.
_AMBIGUOUS_MAP = str.maketrans({"J": "L", "U": "C", "O": "K"})

_OUTFMT_COLUMNS = (
    "sseqid pident length mismatch gapopen "
    "qstart qend sstart send evalue bitscore qcovs"
)

_BUILD_LOCK = threading.Lock()
# in-process cache: work_dir -> (signature, subjects, db prefix)
_READY: Dict[str, tuple] = {}


@dataclass
class BlastSubject:
    enzyme_id: str
    isoform_id: Optional[str]
    sequence: str
    length: int

    @property
    def subject_type(self) -> str:
        return "isoform" if self.isoform_id else "canonical"


class BlastToolMissingError(RuntimeError):
    """NCBI BLAST+ binaries were not found on this machine."""


def _work_dir() -> Path:
    value = settings.blast_work_dir
    path = Path(value)
    if not path.is_absolute():
        path = BACKEND_ROOT / value
    try:
        path.resolve().as_posix().encode("ascii")
    except UnicodeEncodeError:
        # makeblastdb (BLAST+ >= 2.14) stores its DB with LMDB, which cannot
        # open files under a non-ASCII path (e.g. a project folder containing
        # CJK characters). Fall back to an ASCII system-temp location; the DB
        # is small and rebuilt automatically when missing.
        temp = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())
        path = Path(temp) / "igem_blast_work"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tool(name: str) -> str:
    """Locate ``name`` (blastp / makeblastdb) under blast_bin_dir, else PATH."""
    value = settings.blast_bin_dir
    exe = f"{name}{_EXE_SUFFIX}"
    if value:
        base = Path(value)
        if not base.is_absolute():
            base = BACKEND_ROOT / value
        # Accept both a flat layout (blast_bin/blastp.exe) and the nested
        # "bin/" layout of the unpacked NCBI archive (blast_bin/bin/blastp.exe).
        for folder in (base, base / "bin"):
            candidate = folder / exe
            if candidate.is_file():
                return str(candidate)
            candidate = folder / name
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(exe) or shutil.which(name)
    if found:
        return found

    where = settings.blast_bin_dir or "PATH"
    raise BlastToolMissingError(
        f"NCBI BLAST+ binary '{exe}' was not found under '{where}'. "
        f"Place blastp{_EXE_SUFFIX} and makeblastdb{_EXE_SUFFIX} in "
        f"{settings.blast_bin_dir or 'a directory on PATH'} and retry."
    )


# --------------------------------------------------------------------------- #
# Query preparation
# --------------------------------------------------------------------------- #
def clean_query_sequence(raw: str) -> str:
    """Normalise a pasted protein/FASTA block into an alignment-ready string."""
    without_headers = _FASTA_HEADER_RE.sub("", raw or "")
    letters = _NON_ALPHA_RE.sub("", without_headers)
    cleaned = letters.upper().translate(_AMBIGUOUS_MAP)
    if len(cleaned) < _MIN_QUERY_AA:
        raise ValueError(
            f"The query sequence is too short ({len(cleaned)} aa). "
            f"Paste a protein/FASTA sequence of at least {_MIN_QUERY_AA} residues."
        )
    return cleaned


# --------------------------------------------------------------------------- #
# Subject library
# --------------------------------------------------------------------------- #
async def _load_subjects(db: AsyncSession) -> List[BlastSubject]:
    enzymes = (await db.execute(select(Enzyme).where(Enzyme.sequence.is_not(None)))).scalars().all()
    subjects: List[BlastSubject] = []
    for e in enzymes:
        seq = (e.sequence or "").strip()
        subjects.append(
            BlastSubject(
                enzyme_id=e.enzyme_id,
                isoform_id=None,
                sequence=seq,
                length=e.length if e.length else len(seq),
            )
        )

    variants = (
        await db.execute(
            select(EnzymeIsoform).where(
                EnzymeIsoform.sequence.is_not(None),
                or_(
                    EnzymeIsoform.canonical_sequence.is_(None),
                    EnzymeIsoform.sequence != EnzymeIsoform.canonical_sequence,
                ),
            )
        )
    ).scalars().all()
    for iso in variants:
        seq = (iso.sequence or "").strip()
        subjects.append(
            BlastSubject(
                enzyme_id=iso.enzyme_id,
                isoform_id=iso.isoform_id,
                sequence=seq,
                length=iso.isoform_length or len(seq),
            )
        )
    subjects.sort(key=lambda s: (s.enzyme_id, s.isoform_id or ""))
    return subjects


def _signature(subjects: List[BlastSubject]) -> str:
    digest = hashlib.sha1()
    for s in subjects:
        digest.update(f"{s.enzyme_id}\t{s.isoform_id or ''}\t{s.length}\n".encode("utf-8"))
    return digest.hexdigest()


def _write_fasta(subjects: List[BlastSubject], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for index, subject in enumerate(subjects):
            handle.write(f">{index}\n{subject.sequence}\n")


def _db_exists(prefix: Path) -> bool:
    return all((Path(str(prefix) + ext)).exists() for ext in (".phr", ".pin", ".psq"))


async def _run_makeblastdb(fasta: Path, prefix: Path) -> None:
    cmd = [
        _tool("makeblastdb"),
        "-dbtype", "prot",
        "-in", str(fasta),
        "-out", str(prefix),
        "-title", "igem_terpene_enzymes",
    ]

    def run() -> None:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            raise RuntimeError(
                f"makeblastdb failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    await asyncio.to_thread(run)


async def _ensure_blast_db(db: AsyncSession):
    """Return (subjects, db_prefix); rebuild the formatted DB when out of date."""
    work = _work_dir()
    fasta = work / "igem_subjects.fa"
    prefix = work / "igem_enzymes"
    marker = work / "igem.sig"

    subjects = await _load_subjects(db)
    signature = _signature(subjects)

    with _BUILD_LOCK:
        cached = _READY.get(str(work))
        if cached and cached[0] == signature:
            return cached[1], cached[2]

        current = marker.read_text(encoding="utf-8", errors="ignore").strip() if marker.exists() else ""
        if current == signature and _db_exists(prefix):
            _READY[str(work)] = (signature, subjects, prefix)
            return subjects, prefix

        _write_fasta(subjects, fasta)
        await _run_makeblastdb(fasta, prefix)
        marker.write_text(signature, encoding="utf-8")
        _READY[str(work)] = (signature, subjects, prefix)
        return subjects, prefix


# --------------------------------------------------------------------------- #
# blastp
# --------------------------------------------------------------------------- #
async def _run_blastp(
    query: str,
    prefix: Path,
    e_value_threshold: float,
    max_results: int,
) -> List[tuple]:
    """Run blastp; return raw parsed rows [sseqid, pident, length, evalue, bitscore, qcovs, ...]."""
    blastp = _tool("blastp")
    query_dir = _work_dir() / "queries"
    query_dir.mkdir(parents=True, exist_ok=True)
    query_file = query_dir / f"q_{hashlib.sha1(query.encode('utf-8')).hexdigest()[:16]}.fa"
    query_file.write_text(f">query\n{query}\n", encoding="utf-8", newline="\n")

    # Ask blastp for plenty of targets; we sort + trim afterwards. The subject
    # library is ~1000 entries so this keeps the result bounded.
    target_cap = max(min(max_results * 4, 1000), 200)
    cmd = [
        blastp,
        "-db", str(prefix),
        "-query", str(query_file),
        "-outfmt", f"6 {_OUTFMT_COLUMNS}",
        "-evalue", str(e_value_threshold),
        "-max_target_seqs", str(target_cap),
        "-num_threads", "2",
    ]

    def run() -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    try:
        result = await asyncio.to_thread(run)
    except subprocess.TimeoutExpired:
        raise RuntimeError("blastp timed out after 120s; the query may be too long.")

    if result.returncode != 0:
        raise RuntimeError(
            f"blastp failed (exit {result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    rows = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        # 12 columns: sseqid pident length mismatch gapopen qstart qend
        #             sstart send evalue bitscore qcovs   (indices 0..11)
        if len(fields) < 12:
            continue
        try:
            rows.append(
                (
                    int(fields[0]),            # sseqid index
                    float(fields[1]),          # pident
                    int(fields[2]),            # alignment length
                    float(fields[9]),          # evalue
                    float(fields[10]),         # bitscore
                    float(fields[11]),         # qcovs
                )
            )
        except (ValueError, IndexError):
            continue
    return rows


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
async def _load_card_meta(db: AsyncSession, enzyme_ids: set[str]):
    """Batch-load the info needed to build an EnzymeCard for each enzyme id."""
    enzymes = {e.enzyme_id: e for e in (await db.execute(
        select(Enzyme).where(Enzyme.enzyme_id.in_(list(enzyme_ids)))
    )).scalars().all()}

    genes: Dict[str, Optional[str]] = {}
    if enzymes:
        for gene in (await db.execute(
            select(Gene).where(Gene.enzyme_id.in_(list(enzyme_ids))).order_by(Gene.enzyme_id, Gene.gene_id)
        )).scalars().all():
            genes.setdefault(gene.enzyme_id, gene.gene_name)

    # First reaction edge per enzyme (edges + reaction), to surface EC/equation.
    first_edges: Dict[str, tuple] = {}
    if enzymes:
        rows = (
            await db.execute(
                select(EnzymeReactionEdge, Reaction)
                .outerjoin(Reaction, Reaction.reaction_id == EnzymeReactionEdge.reaction_id)
                .where(EnzymeReactionEdge.enzyme_id.in_(list(enzyme_ids)))
                .order_by(EnzymeReactionEdge.enzyme_id, EnzymeReactionEdge.edge_id)
            )
        ).all()
        for edge, reaction in rows:
            first_edges.setdefault(edge.enzyme_id, (edge, reaction))

    return enzymes, genes, first_edges


def _build_card(enzyme, gene_name, edge_reaction) -> EnzymeCard:
    edge, reaction = edge_reaction if edge_reaction else (None, None)
    return EnzymeCard(
        edge_id=edge.edge_id if edge else "",
        enzyme_id=enzyme.enzyme_id,
        primary_name=enzyme.primary_name,
        uniprot_id=enzyme.uniprot_id,
        database_code=enzyme.enzyme_id,
        organism_name=enzyme.organism_name,
        gene_name=gene_name,
        ec_number=reaction.ec_number if reaction else None,
        reaction_id=edge.reaction_id if edge else "",
        reaction_equation=reaction.equation if reaction else "",
        reaction_direction=(reaction.direction.value if reaction and reaction.direction else "unknown"),
        source_type=(edge.source_type.value if edge and edge.source_type
                     else (enzyme.source_type.value if enzyme.source_type else "swiss_prot")),
        review_status=(edge.review_status.value if edge and edge.review_status
                       else (enzyme.review_status.value if enzyme.review_status else "official")),
    )


# --------------------------------------------------------------------------- #
# Public entry
# --------------------------------------------------------------------------- #
async def run_blast_search(db: AsyncSession, request: BlastSearchRequest) -> BlastPayload:
    query = clean_query_sequence(request.sequence)

    threshold = max(1e-200, min(float(request.e_value_threshold), 10.0))
    max_results = max(1, min(request.max_results or 100, 200))

    try:
        subjects, prefix = await _ensure_blast_db(db)
    except BlastToolMissingError:
        raise
    except FileNotFoundError as exc:
        raise RuntimeError(f"BLAST database could not be built: {exc}")

    rows = await _run_blastp(query, prefix, threshold, max_results)
    rows.sort(key=lambda row: (row[3], -row[1]))

    hits: List[BlastHit] = []
    for index, pident, length, e_value, bitscore, qcovs in rows:
        if index >= len(subjects):
            continue
        subject = subjects[index]
        hits.append(
            BlastHit(
                enzyme_id=subject.enzyme_id,
                isoform_id=subject.isoform_id,
                subject_type=subject.subject_type,  # type: ignore[arg-type]
                subject_length=subject.length,
                e_value=e_value,
                identity=round(pident, 2),
                query_cover=round(qcovs, 2),
                alignment_length=length,
                bitscore=bitscore,
            )
        )
        if len(hits) >= max_results:
            break

    if hits:
        enzyme_ids = {hit.enzyme_id for hit in hits}
        enzymes, genes, first_edges = await _load_card_meta(db, enzyme_ids)
        for hit in hits:
            enzyme = enzymes.get(hit.enzyme_id)
            if enzyme is None:
                continue
            hit.card = _build_card(enzyme, genes.get(hit.enzyme_id), first_edges.get(hit.enzyme_id))

    return BlastPayload(
        query_length=len(query),
        searched_subjects=len(subjects),
        threshold=threshold,
        hits=hits,
    )
