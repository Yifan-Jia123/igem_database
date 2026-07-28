"""
Pathway search service: IDDFS on reaction-level directed graph.

Builds an in-memory adjacency list from reaction_compound + reaction data,
then performs iterative deepening depth-first search with ordered via-compound constraints.
"""

from typing import List, Dict, Tuple, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Reaction, ReactionCompound, EnzymeReactionEdge,
    Compound, Enzyme,
)
from app.schemas.pathway import PathwayCard
from app.schemas.graph import GraphPayload
from app.schemas.compound import CompoundCard


# Direction → which roles can reach which
# forward: substrate → product only
# reverse: product → substrate only
# reversible/unknown: both ways
DIRECTION_ALLOWS = {
    "forward":    {("substrate", "product")},
    "reverse":    {("product", "substrate")},
    "reversible": {("substrate", "product"), ("product", "substrate")},
    "unknown":    {("substrate", "product"), ("product", "substrate")},
}


async def search_pathways(
    db: AsyncSession,
    start_compound_id: Optional[str] = None,
    end_compound_id: Optional[str] = None,
    via_compound_ids: Optional[List[str]] = None,
    max_steps: int = 6,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    limit: int = 10,
) -> List[PathwayCard]:
    """
    Search pathways using IDDFS with ordered via-compound constraints.

    At least two of start/end/via must be provided.
    """

    via = via_compound_ids or []

    # Build adjacency list
    graph, reaction_info = await _build_graph(db, source_types, review_statuses)

    # Determine search strategy
    if start_compound_id and end_compound_id:
        # Full path: start → (via in order) → end
        required = via + [end_compound_id]
        paths = iddfs(graph, start_compound_id, required, max_steps, limit * 3)
    elif start_compound_id and via:
        # start → via → any
        required = via
        paths = iddfs(graph, start_compound_id, required, max_steps, limit * 3)
    elif end_compound_id and via:
        # any → via → end — search backwards from end compound
        reversed_graph = _reverse_graph(graph)
        required = list(reversed(via))
        rev_paths = iddfs(reversed_graph, end_compound_id, required, max_steps, limit * 3)
        paths = [list(reversed(p)) for p in rev_paths]
    elif start_compound_id:
        # No real target — just expand from start
        paths = _expand_from(graph, start_compound_id, max_steps, limit * 3)
    elif end_compound_id:
        reversed_graph = _reverse_graph(graph)
        rev_paths = _expand_from(reversed_graph, end_compound_id, max_steps, limit * 3)
        paths = [list(reversed(p)) for p in rev_paths]
    else:
        paths = []

    # Assemble PathwayCards
    cards = []
    for path in paths:
        if len(cards) >= limit:
            break
        card = await _assemble_pathway_card(db, path, graph, reaction_info)
        if card:
            cards.append(card)

    return cards


def iddfs(
    graph: Dict[str, List[Tuple[str, str]]],
    start: str,
    required: List[str],
    max_depth: int,
    max_results: int,
) -> List[List[str]]:
    """
    Iterative deepening DFS.

    graph: {compound_id: [(target_compound_id, reaction_id), ...]}
    start: starting compound
    required: ordered list of compounds that must appear in path (in order)
    max_depth: maximum path steps
    max_results: stop after finding this many

    Returns list of paths, each path is [compound_id, ...] in traversal order.
    """

    if start not in graph:
        return []

    results: List[List[str]] = []

    for depth in range(1, max_depth + 1):
        if len(results) >= max_results:
            break
        _dls(graph, start, required, depth, [start], {start}, results, max_results)

    return results


def _dls(
    graph: Dict[str, List[Tuple[str, str]]],
    current: str,
    required: List[str],
    remaining_depth: int,
    path: List[str],
    visited: Set[str],
    results: List[List[str]],
    max_results: int,
):
    """Depth-limited DFS step."""

    # Check if current path satisfies required sequence
    if _satisfies_required(path, required):
        # Check if the last compound in path matches the last required element
        # (which is end_compound_id or last via element depending on mode)
        if remaining_depth >= 0:
            results.append(list(path))

    if len(results) >= max_results:
        return

    if remaining_depth <= 0:
        return

    if current not in graph:
        return

    for neighbor, reaction_id in graph[current]:
        if neighbor in visited:
            continue

        path.append(neighbor)
        visited.add(neighbor)

        _dls(graph, neighbor, required, remaining_depth - 1, path, visited, results, max_results)

        visited.discard(neighbor)
        path.pop()

        if len(results) >= max_results:
            return


def _satisfies_required(path: List[str], required: List[str]) -> bool:
    """Check if path contains all required compounds in order."""
    if not required:
        return True

    ri = 0  # index in required
    for node in path:
        if ri < len(required) and node == required[ri]:
            ri += 1
        if ri == len(required):
            return True
    return False


def _expand_from(
    graph: Dict[str, List[Tuple[str, str]]],
    start: str,
    max_depth: int,
    max_results: int,
) -> List[List[str]]:
    """Expand from start without a specific target."""
    results = []
    for depth in range(1, max_depth + 1):
        _dls(graph, start, [], depth, [start], {start}, results, max_results)
        if len(results) >= max_results:
            break
    return results


def _reverse_graph(
    graph: Dict[str, List[Tuple[str, str]]]
) -> Dict[str, List[Tuple[str, str]]]:
    """Reverse all edges in the graph (for backwards search)."""
    reversed_g: Dict[str, List[Tuple[str, str]]] = {}
    for src, edges in graph.items():
        for dst, rxn_id in edges:
            if dst not in reversed_g:
                reversed_g[dst] = []
            reversed_g[dst].append((src, rxn_id))
    return reversed_g


async def _build_graph(
    db: AsyncSession,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[str, Reaction]]:
    """
    Build reaction-level directed graph from DB.

    Returns (graph, reaction_info) where:
      graph: {compound_id: [(target_compound_id, reaction_id), ...]}
      reaction_info: {reaction_id: Reaction} for later card assembly
    """

    # Load all reactions
    rxn_query = select(Reaction)
    if source_types:
        rxn_query = rxn_query.where(Reaction.source_type.in_(source_types))
    if review_statuses:
        rxn_query = rxn_query.where(Reaction.review_status.in_(review_statuses))
    rxn_result = await db.execute(rxn_query)
    reaction_map: Dict[str, Reaction] = {r.reaction_id: r for r in rxn_result.scalars().all()}

    # Load all reaction_compound entries for these reactions
    reaction_ids = list(reaction_map.keys())
    if not reaction_ids:
        return {}, {}

    rc_result = await db.execute(
        select(ReactionCompound).where(ReactionCompound.reaction_id.in_(reaction_ids))
    )
    rc_rows = rc_result.scalars().all()

    # Group by reaction
    rxn_compounds: Dict[str, Tuple[List[str], List[str]]] = {}
    for rc in rc_rows:
        if rc.reaction_id not in rxn_compounds:
            rxn_compounds[rc.reaction_id] = ([], [])
        if rc.role.value == "substrate":
            rxn_compounds[rc.reaction_id][0].append(rc.compound_id)
        else:
            rxn_compounds[rc.reaction_id][1].append(rc.compound_id)

    # Build graph
    graph: Dict[str, List[Tuple[str, str]]] = {}

    for reaction_id, reaction in reaction_map.items():
        substrates, products = rxn_compounds.get(reaction_id, ([], []))
        direction = reaction.direction.value if reaction.direction else "unknown"
        allowed = DIRECTION_ALLOWS.get(direction, DIRECTION_ALLOWS["unknown"])

        if ("substrate", "product") in allowed:
            for s in substrates:
                for p in products:
                    if s != p:
                        if s not in graph:
                            graph[s] = []
                        graph[s].append((p, reaction_id))

        if ("product", "substrate") in allowed:
            for p in products:
                for s in substrates:
                    if s != p:
                        if p not in graph:
                            graph[p] = []
                        graph[p].append((s, reaction_id))

    return graph, reaction_map


async def _assemble_pathway_card(
    db: AsyncSession,
    path: List[str],
    graph: Dict[str, List[Tuple[str, str]]],
    reaction_info: Dict[str, Reaction],
) -> Optional[PathwayCard]:
    """Build a PathwayCard from a path of compound IDs."""

    if len(path) < 2:
        return None

    # Get compound names
    cpd_result = await db.execute(
        select(Compound).where(Compound.compound_id.in_(path))
    )
    cpd_map = {c.compound_id: c.name for c in cpd_result.scalars().all()}

    # Build summary string
    names = [cpd_map.get(cid, cid) for cid in path]
    summary = " → ".join(names)

    # Map each step to a reaction
    reaction_steps = []
    edge_ids = []
    edge_group_ids = []

    for i in range(len(path) - 1):
        from_cpd = path[i]
        to_cpd = path[i + 1]

        # Find which reaction(s) connect these two compounds
        candidate_rxn_ids = []
        if from_cpd in graph:
            for neighbor, rxn_id in graph[from_cpd]:
                if neighbor == to_cpd and rxn_id not in candidate_rxn_ids:
                    candidate_rxn_ids.append(rxn_id)

        # Get one reaction (first one found)
        if not candidate_rxn_ids:
            continue

        chosen_rxn = candidate_rxn_ids[0]
        reaction_steps.append((chosen_rxn, from_cpd, to_cpd))

    # Collect edge data
    reaction_ids_in_order = [rxn_id for rxn_id, _, _ in reaction_steps]
    if reaction_ids_in_order:
        edge_result = await db.execute(
            select(EnzymeReactionEdge, Enzyme)
            .join(Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id)
            .where(EnzymeReactionEdge.reaction_id.in_(reaction_ids_in_order))
        )
        rxn_edges: Dict[str, List[str]] = {}
        for ere, enz in edge_result:
            if ere.reaction_id not in rxn_edges:
                rxn_edges[ere.reaction_id] = []
            rxn_edges[ere.reaction_id].append(ere.edge_id)

        for rxn_id, from_cpd, to_cpd in reaction_steps:
            eids = rxn_edges.get(rxn_id, [])
            if len(eids) == 1:
                edge_ids.append(eids[0])
            elif len(eids) > 1:
                group_id = f"GROUP_{from_cpd}_{to_cpd}"
                edge_group_ids.append(group_id)

    return PathwayCard(
        pathway_id=f"PATH_{'_'.join(path[:3])}",
        summary=summary,
        compound_ids=path,
        edge_ids=edge_ids,
        edge_group_ids=edge_group_ids,
        step_count=len(path) - 1,
    )
