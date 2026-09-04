from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Enzyme, Gene, EnzymeReactionEdge, Reaction
from app.schemas.enzyme import EnzymeCard
from app.schemas.homology import HomologyJobStatus, HomologyResultItem, HomologySearchRequest

_JOBS: Dict[str, HomologyJobStatus] = {}
_FASTA_HEADER_RE = re.compile(r"^>.*$", re.MULTILINE)
_SEQUENCE_RE = re.compile(r"[^A-Za-z]")


def get_homology_job(job_id: str) -> Optional[HomologyJobStatus]:
    return _JOBS.get(job_id)


async def run_homology_search(db: AsyncSession, request: HomologySearchRequest) -> HomologyJobStatus:
    query_sequence = await _resolve_query_sequence(db, request)
    if not query_sequence:
        raise ValueError("Provide either enzymeId with a stored sequence or a sequence string.")

    query_sequence = _clean_sequence(query_sequence)
    if not query_sequence:
        raise ValueError("The query sequence is empty after cleaning FASTA/header characters.")

    max_results = min(max(request.max_results or 50, 1), 200)
    source_types = {_normalize_source_type(item) for item in request.source_types or []}

    rows = await _load_candidates(db)
    gene_names = await _load_gene_names(db, {enzyme.enzyme_id for enzyme, _, _ in rows})
    results: List[HomologyResultItem] = []
    seen = set()

    for enzyme, edge, reaction in rows:
        if enzyme.enzyme_id in seen:
            continue
        seen.add(enzyme.enzyme_id)

        candidate_sequence = _clean_sequence(enzyme.sequence or "")
        if not candidate_sequence:
            continue

        candidate_source = _enum_value(edge.source_type if edge else enzyme.source_type)
        if source_types and _normalize_source_type(candidate_source) not in source_types:
            continue

        identity = _sequence_identity(query_sequence, candidate_sequence)
        e_value = _estimate_e_value(identity, min(len(query_sequence), len(candidate_sequence)))
        if e_value > request.e_value_threshold:
            continue

        results.append(HomologyResultItem(
            enzyme_id=enzyme.enzyme_id,
            e_value=e_value,
            identity=round(identity, 2),
            card=_enzyme_card(enzyme, edge, reaction, gene_names.get(enzyme.enzyme_id)),
        ))

    results.sort(key=lambda item: (item.e_value, -(item.identity or 0), item.enzyme_id))
    results = results[:max_results]

    job_id = _job_id(request, query_sequence)
    status = HomologyJobStatus(
        job_id=job_id,
        status="finished",
        progress=100,
        results=results,
    )
    _JOBS[job_id] = status
    return status


async def _resolve_query_sequence(db: AsyncSession, request: HomologySearchRequest) -> Optional[str]:
    if request.sequence:
        return request.sequence

    if not request.enzyme_id:
        return None

    result = await db.execute(select(Enzyme).where(Enzyme.enzyme_id == request.enzyme_id))
    enzyme = result.scalar_one_or_none()
    return enzyme.sequence if enzyme else None


async def _load_candidates(db: AsyncSession):
    result = await db.execute(
        select(Enzyme, EnzymeReactionEdge, Reaction)
        .outerjoin(EnzymeReactionEdge, Enzyme.enzyme_id == EnzymeReactionEdge.enzyme_id)
        .outerjoin(Reaction, EnzymeReactionEdge.reaction_id == Reaction.reaction_id)
        .where(Enzyme.sequence.is_not(None))
    )
    return result.all()


def _clean_sequence(sequence: str) -> str:
    without_headers = _FASTA_HEADER_RE.sub("", sequence)
    return _SEQUENCE_RE.sub("", without_headers).upper()


def _sequence_identity(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0

    if query == candidate:
        return 100.0

    shorter, longer = (query, candidate) if len(query) <= len(candidate) else (candidate, query)
    aligned = min(len(shorter), len(longer))
    positional = sum(1 for left, right in zip(query, candidate) if left == right) / aligned * 100
    kmer = _kmer_similarity(shorter, longer)
    length_penalty = aligned / max(len(query), len(candidate))
    return max(positional * length_penalty, kmer * 100)


def _kmer_similarity(shorter: str, longer: str, k: int = 3) -> float:
    if len(shorter) < k:
        return 1.0 if shorter in longer else 0.0

    short_kmers = {shorter[index:index + k] for index in range(len(shorter) - k + 1)}
    long_kmers = {longer[index:index + k] for index in range(len(longer) - k + 1)}
    if not short_kmers or not long_kmers:
        return 0.0

    return len(short_kmers & long_kmers) / len(short_kmers)


def _estimate_e_value(identity: float, aligned_length: int) -> float:
    if identity <= 0 or aligned_length <= 0:
        return 1.0

    exponent = min(180.0, max(1.0, (identity / 100.0) * aligned_length / 5.0))
    return math.pow(10.0, -exponent)


def _enzyme_card(
    enzyme: Enzyme,
    edge: Optional[EnzymeReactionEdge],
    reaction: Optional[Reaction],
    gene_name: Optional[str] = None,
) -> EnzymeCard:
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
        reaction_direction=_enum_value(reaction.direction) if reaction and reaction.direction else "unknown",
        source_type=_enum_value(edge.source_type) if edge and edge.source_type else _enum_value(enzyme.source_type),
        review_status=_enum_value(edge.review_status) if edge and edge.review_status else _enum_value(enzyme.review_status),
    )


async def _load_gene_names(db: AsyncSession, enzyme_ids: set[str]) -> Dict[str, Optional[str]]:
    if not enzyme_ids:
        return {}

    result = await db.execute(
        select(Gene)
        .where(Gene.enzyme_id.in_(list(enzyme_ids)))
        .order_by(Gene.enzyme_id, Gene.gene_id)
    )
    gene_names: Dict[str, Optional[str]] = {}
    for gene in result.scalars().all():
        gene_names.setdefault(gene.enzyme_id, gene.gene_name)
    return gene_names


def _job_id(request: HomologySearchRequest, sequence: str) -> str:
    digest = hashlib.sha1(f"{request.enzyme_id or ''}:{sequence}:{datetime.utcnow().isoformat()}".encode("utf-8")).hexdigest()
    return f"homology_{digest[:16]}"


def _enum_value(value) -> str:
    return getattr(value, "value", value) or ""


def _normalize_source_type(value: str) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")
