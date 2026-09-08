"""
Pathway search service: distinct start → (…via…) → end chains over the same
"enzyme-edge supported, displayable directed compound-pair" graph the map
renders.

The old implementation walked a reaction-level graph (any substrate × product)
and took the *first* reaction per pair, so returned steps were not guaranteed
to be drawable map edges; IDDFS also re-emitted the same chain at every depth,
allowed paths to overshoot the end compound, and degenerated into "expand from
start" when start == end. This rewrite:

  * builds adjacency from (EnzymeReactionEdge, Enzyme) rows expanded with the
    exact direction semantics graph_service uses, so every enumerated step is a
    pair the map can draw (single edge or composite GROUP);
  * enumerates simple paths with the end compound absorbing (a path stops the
    moment it reaches ``end``, never past it) and via-compounds satisfied as an
    ordered subsequence;
  * deduplicates by the compound chain itself (two different enzymes between
    the same pair = the same map edge = the same pathway);
  * assembles every card's ``edge_ids``/``edge_group_ids``/``segments`` from
    the union GraphPayload produced by graph_service (never hand-built
    ``GROUP_`` strings, never "first reaction").
"""

from typing import List, Dict, Tuple, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Reaction, ReactionCompound, EnzymeReactionEdge,
    Compound, Enzyme,
)
from app.schemas.pathway import PathwayCard, PathwaySegment
from app.schemas.graph import GraphPayload
from app.utils.compound_filters import displayable_compound_filters
from app.services.graph_service import (
    DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT,
    DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE,
    resolve_compound_family,
    suggest_displayable_compounds,
    build_pathway_union_payload,
)

# Cap on DFS hits before sorting/truncation, so a dense network cannot run
# away. Curated DBs stay far below this.
_MAX_COLLECT = 800

FIELD_LABELS = {"start": "起点", "end": "终点", "via": "中间点"}


class PathwayInputError(Exception):
    """Structured, user-facing input error (soft HTTP-200 business error)."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


async def search_pathways(
    db: AsyncSession,
    start_compound_id: Optional[str] = None,
    end_compound_id: Optional[str] = None,
    via_compound_ids: Optional[List[str]] = None,
    max_steps: int = 6,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    limit: int = 10,
) -> Tuple[List[PathwayCard], GraphPayload, int]:
    """Return ``(cards, union_graph_payload, total)`` for the pathway query.

    ``total`` is the number of distinct compound chains that satisfy the
    query (before ``limit`` truncation); ``cards`` carry the top ``limit``
    ordered by ascending step count. Raises :class:`PathwayInputError` for
    unresolvable/missing/invalid endpoints.
    """
    raw_start = (start_compound_id or "").strip()
    raw_end = (end_compound_id or "").strip()
    raw_vias = [v.strip() for v in (via_compound_ids or []) if (v or "").strip()]

    if not raw_start or not raw_end:
        raise PathwayInputError(
            "INVALID_RANGE",
            "通路检索需要提供起点与终点",
            details={"field": "start/end"},
        )
    if max_steps < 1 or max_steps > 8:
        raise PathwayInputError(
            "INVALID_RANGE",
            f"最大步数需在 1–8 之间（收到 {max_steps}）",
            details={"field": "maxSteps", "received": max_steps},
        )
    limit = max(1, min(limit, 40))

    # Resolve every token (start/end/via) to one displayable compound id before
    # touching the (larger) edge tables, so bad input fails fast.
    start_id = await _resolve_token(db, raw_start, "start")
    end_id = await _resolve_token(db, raw_end, "end")

    via_ids: List[str] = []
    seen_vias: Set[str] = set()
    for raw_via in raw_vias:
        via_id = await _resolve_token(db, raw_via, "via")
        if via_id in seen_vias or via_id == start_id or via_id == end_id:
            continue  # redundant on any simple path (start/end already bound)
        seen_vias.add(via_id)
        via_ids.append(via_id)

    if start_id == end_id:
        raise PathwayInputError(
            "SAME_COMPOUND",
            "起点与终点为同一化合物，无法构成通路",
            details={
                "compoundId": start_id,
                "compoundName": (await _compound_name(db, start_id)),
            },
        )

    adjacency, pair_rows = await _load_edge_pair_graph(
        db, source_types, review_statuses
    )

    chains = _enumerate_paths(
        adjacency, start_id, end_id, via_ids, max_steps=max_steps, cap=_MAX_COLLECT
    )
    chains.sort(key=lambda chain: (len(chain), chain))
    selected = chains[:limit]
    if not selected:
        return [], GraphPayload(), 0

    # All step pairs across the returned chains → one union payload.
    step_pairs: Set[Tuple[str, str]] = {
        (chain[i], chain[i + 1])
        for chain in selected
        for i in range(len(chain) - 1)
    }
    union_rows = _dedupe_rows_for_pairs(pair_rows, step_pairs)
    payload = await build_pathway_union_payload(db, union_rows, keep_pair=step_pairs)

    pair_single: Dict[Tuple[str, str], str] = {}
    for edge in payload.edges:
        pair_single.setdefault(
            (edge.source_compound_id, edge.target_compound_id), edge.edge_id
        )
    pair_group: Dict[Tuple[str, str], str] = {}
    for group in payload.edge_groups:
        pair_group.setdefault(
            (group.source_compound_id, group.target_compound_id),
            group.edge_group_id,
        )

    name_map = await _load_compound_names(
        db, {c for chain in selected for c in chain}
    )

    cards: List[PathwayCard] = []
    for chain in selected:
        card = _assemble_card(
            chain, pair_single, pair_group, name_map
        )
        if card is not None:
            cards.append(card)

    if not cards:
        return [], GraphPayload(), 0

    return cards, payload, len(chains)


# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------


async def _resolve_token(db: AsyncSession, token: str, field: str) -> str:
    """Resolve one pathway endpoint token to a single displayable compound id.

    Matching reuses ``resolve_compound_family`` (identifier / ChEBI digits /
    exact name / stereo-stripped base name, literal hits first) and picks the
    best-ranked member — normally the literal match, else the shortest-name
    generic metabolite of the family. Raises COMPOUND_NOT_FOUND otherwise.
    """
    family = await resolve_compound_family(db, token)
    if family:
        return family[0].compound_id

    candidates = await suggest_displayable_compounds(db, token, limit=5)
    raise PathwayInputError(
        "COMPOUND_NOT_FOUND",
        f"{FIELD_LABELS.get(field, field)}「{token}」未匹配到任何库内可检索化合物",
        details={
            "field": field,
            "token": token,
            "candidates": [
                {
                    "compoundId": c.compound_id,
                    "name": c.name,
                    "chebiId": c.chebi_id,
                }
                for c in candidates
            ],
        },
    )


async def _compound_name(db: AsyncSession, compound_id: str) -> Optional[str]:
    row = await db.execute(
        select(Compound.name).where(Compound.compound_id == compound_id)
    )
    return row.scalar()


# ---------------------------------------------------------------------------
# Edge-pair graph (mirrors graph_service's drawable-pair expansion)
# ---------------------------------------------------------------------------


async def _load_edge_pair_graph(
    db: AsyncSession,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
) -> Tuple[Dict[str, Set[str]], Dict[Tuple[str, str], List[Tuple[EnzymeReactionEdge, Enzyme]]]]:
    """Build the drawable directed compound-pair graph from enzyme edges.

    Returns ``(adjacency, pair_rows)`` where adjacency maps each compound to the
    set of reachable targets and ``pair_rows`` maps each ordered pair to the
    ``(EnzymeReactionEdge, Enzyme)`` rows that catalyse it. A pair is only kept
    when at least one displayable substrate → product expansion exists, exactly
    mirroring graph_service's ``_ere_rows_to_graph_payload`` so enumeration and
    the union payload always agree on what is drawable.
    """
    edge_query = select(EnzymeReactionEdge, Enzyme).join(
        Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
    )
    if source_types:
        edge_query = edge_query.where(
            EnzymeReactionEdge.source_type.in_(source_types)
        )
    if review_statuses:
        edge_query = edge_query.where(
            EnzymeReactionEdge.review_status.in_(review_statuses)
        )
    edge_rows = (await db.execute(edge_query)).all()
    if not edge_rows:
        return {}, {}

    reaction_ids = {ere.reaction_id for ere, _ in edge_rows}
    rxn_result = await db.execute(
        select(Reaction).where(Reaction.reaction_id.in_(reaction_ids))
    )
    reaction_map = {rxn.reaction_id: rxn for rxn in rxn_result.scalars().all()}

    rc_result = await db.execute(
        select(ReactionCompound)
        .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
        .where(ReactionCompound.reaction_id.in_(reaction_ids))
        .where(*displayable_compound_filters())
    )
    rxn_compounds: Dict[str, Tuple[List[str], List[str]]] = {}
    for rc in rc_result.scalars().all():
        if rc.reaction_id not in rxn_compounds:
            rxn_compounds[rc.reaction_id] = ([], [])
        if rc.role.value == "substrate":
            rxn_compounds[rc.reaction_id][0].append(rc.compound_id)
        else:
            rxn_compounds[rc.reaction_id][1].append(rc.compound_id)

    adjacency: Dict[str, Set[str]] = {}
    pair_rows: Dict[Tuple[str, str], List[Tuple[EnzymeReactionEdge, Enzyme]]] = {}
    for ere, enz in edge_rows:
        reaction = reaction_map.get(ere.reaction_id)
        if not reaction:
            continue
        substrates, products = rxn_compounds.get(ere.reaction_id, ([], []))
        if not substrates or not products:
            continue
        direction = reaction.direction.value if reaction.direction else "unknown"

        pairs: List[Tuple[str, str]] = []
        if direction in DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT:
            pairs.extend(
                (s, p) for s in substrates for p in products if s != p
            )
        if direction in DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE:
            pairs.extend(
                (p, s) for p in products for s in substrates if p != s
            )

        for source_id, target_id in pairs:
            adjacency.setdefault(source_id, set()).add(target_id)
            pair_rows.setdefault((source_id, target_id), []).append((ere, enz))

    return adjacency, pair_rows


def _dedupe_rows_for_pairs(
    pair_rows: Dict[Tuple[str, str], List[Tuple[EnzymeReactionEdge, Enzyme]]],
    step_pairs: Set[Tuple[str, str]],
) -> List[Tuple[EnzymeReactionEdge, Enzyme]]:
    """Every unique edge row that catalyses at least one requested pair.

    A single reversible row may produce several directed pairs; including the
    row once is enough because the union payload filters its expanded records
    down to ``step_pairs``.
    """
    deduped: List[Tuple[EnzymeReactionEdge, Enzyme]] = []
    seen: Set[str] = set()
    for pair in step_pairs:
        for ere, enz in pair_rows.get(pair, ()):
            if ere.edge_id not in seen:
                seen.add(ere.edge_id)
                deduped.append((ere, enz))
    return deduped


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def _enumerate_paths(
    adjacency: Dict[str, Set[str]],
    start: str,
    end: str,
    vias: List[str],
    max_steps: int,
    cap: int,
) -> List[List[str]]:
    """All simple paths start → (…vias in order…) → end with ≤ max_steps edges.

    The end compound is absorbing: expansion never continues past it, so no
    chain can overshoot ``end``. Via compounds must appear on the chain in the
    given order (as an ordered subsequence). Neighbours are walked sorted for
    deterministic discovery; results are capped at ``cap``.
    """
    if start not in adjacency:
        return []

    results: List[List[str]] = []
    path = [start]
    visited: Set[str] = {start}

    def dfs(node: str, via_done: int) -> None:
        if node == end:
            if via_done == len(vias):
                results.append(list(path))
            return
        steps = len(path) - 1
        if steps >= max_steps:
            return
        for nxt in sorted(adjacency.get(node, ())):
            if nxt in visited:
                continue
            path.append(nxt)
            visited.add(nxt)
            next_done = via_done + 1 if (
                via_done < len(vias) and nxt == vias[via_done]
            ) else via_done
            dfs(nxt, next_done)
            visited.discard(nxt)
            path.pop()
            if len(results) >= cap:
                return

    dfs(start, 0)
    return results


# ---------------------------------------------------------------------------
# Card + payload assembly
# ---------------------------------------------------------------------------


async def _load_compound_names(
    db: AsyncSession, compound_ids: Set[str]
) -> Dict[str, str]:
    if not compound_ids:
        return {}
    rows = await db.execute(
        select(Compound.compound_id, Compound.name).where(
            Compound.compound_id.in_(list(compound_ids))
        )
    )
    return {compound_id: name for compound_id, name in rows.all()}


def _assemble_card(
    chain: List[str],
    pair_single: Dict[Tuple[str, str], str],
    pair_group: Dict[Tuple[str, str], str],
    name_map: Dict[str, str],
) -> Optional[PathwayCard]:
    """Build one card whose edge/group ids and segments come from the union
    payload indexes — never from hand-built GROUP_ strings."""

    edge_ids: List[str] = []
    edge_group_ids: List[str] = []
    segments: List[PathwaySegment] = []

    for i in range(len(chain) - 1):
        source = chain[i]
        target = chain[i + 1]
        key = (source, target)

        group_id = pair_group.get(key)
        if group_id:
            edge_group_ids.append(group_id)
            segments.append(PathwaySegment(
                source_compound_id=source,
                target_compound_id=target,
                edge_group_id=group_id,
            ))
            continue

        edge_id = pair_single.get(key)
        if not edge_id:
            return None  # step not representable on the map — drop the chain
        edge_ids.append(edge_id)
        segments.append(PathwaySegment(
            source_compound_id=source,
            target_compound_id=target,
            edge_id=edge_id,
        ))

    names = [name_map.get(compound_id, compound_id) for compound_id in chain]
    return PathwayCard(
        pathway_id="PATH_" + "_".join(chain),
        summary=" → ".join(names),
        compound_ids=chain,
        edge_ids=edge_ids,
        edge_group_ids=edge_group_ids,
        segments=segments,
        step_count=len(chain) - 1,
    )
