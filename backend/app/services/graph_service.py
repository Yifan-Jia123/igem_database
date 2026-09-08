import re
from typing import List, Dict, Set, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import (
    Compound, Enzyme, Gene, Reaction,
    ReactionCompound, EnzymeReactionEdge,
)
from app.schemas.graph import GraphPayload, ReactionEdge, EdgeGroup, EdgeGroupItem, FocusPoint
from app.schemas.compound import CompoundCard
from app.schemas.enzyme import EnzymeCard
from app.utils.compound_filters import displayable_compound_filters
from app.services.search_service import search_entries


DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT = {"forward", "reversible", "unknown"}
DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE = {"reverse", "reversible", "unknown"}


async def build_graph_payload(
    db: AsyncSession,
    center_compound_id: Optional[str] = None,
    depth: int = 2,
    limit_nodes: Optional[int] = None,
    selection_mode: Optional[str] = None,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
) -> GraphPayload:

    if not center_compound_id and selection_mode == "global":
        return await _build_global_graph_payload(
            db,
            limit_nodes=limit_nodes,
            source_types=source_types,
            review_statuses=review_statuses,
        )

    # 1. Select center compound
    if center_compound_id:
        result = await db.execute(
            select(Compound).where(Compound.compound_id == center_compound_id)
            .where(*displayable_compound_filters())
        )
        center = result.scalar()
    else:
        center = await _pick_default_center(db)

    if not center:
        return GraphPayload()

    # 2. BFS traversal
    compound_ids, edge_records = await _bfs_subgraph(
        db, center.compound_id, depth, source_types, review_statuses
    )

    # 3. Build compound cards
    compounds = await _fetch_compounds(db, compound_ids)
    cards = [_compound_to_card(c) for c in compounds]
    card_map = {c.compound_id: c for c in cards}
    gene_names = await _load_gene_names(db, {record["enzyme_id"] for record in edge_records})

    # 4. Build edges and edge groups
    edges, edge_groups = _build_edges_and_groups(edge_records, card_map, gene_names)

    # 5. Limit nodes without dropping every drawable edge endpoint
    if limit_nodes and len(cards) > limit_nodes:
        cards, edges, edge_groups = _limit_graph_payload(
            cards,
            edges,
            edge_groups,
            center.compound_id,
            limit_nodes,
        )

    return GraphPayload(
        nodes=cards,
        edges=edges,
        edge_groups=edge_groups,
        focus=FocusPoint(node_id=center.compound_id),
    )


def _limit_graph_payload(
    cards: List[CompoundCard],
    edges: List[ReactionEdge],
    edge_groups: List[EdgeGroup],
    center_id: Optional[str],
    limit_nodes: int,
) -> Tuple[List[CompoundCard], List[ReactionEdge], List[EdgeGroup]]:
    card_map = {card.compound_id: card for card in cards}
    degree_score: Dict[str, int] = {}

    def bump(compound_id: str, value: int = 1) -> None:
        degree_score[compound_id] = degree_score.get(compound_id, 0) + value

    for edge in edges:
        bump(edge.source_compound_id)
        bump(edge.target_compound_id)
    for group in edge_groups:
        bump(group.source_compound_id, group.count)
        bump(group.target_compound_id, group.count)

    selected_ids: Set[str] = set()
    ordered_ids: List[str] = []

    def add(compound_id: str) -> bool:
        if compound_id in selected_ids:
            return True
        if compound_id not in card_map or len(selected_ids) >= limit_nodes:
            return False
        selected_ids.add(compound_id)
        ordered_ids.append(compound_id)
        return True

    if center_id:
        add(center_id)

    pair_candidates = [
        (
            group.source_compound_id,
            group.target_compound_id,
            group.count,
            group.label,
        )
        for group in edge_groups
    ] + [
        (
            edge.source_compound_id,
            edge.target_compound_id,
            1,
            edge.label,
        )
        for edge in edges
    ]
    pair_candidates.sort(key=lambda item: (-item[2], item[3] or "", item[0], item[1]))

    selected_pair_keys: Set[Tuple[str, str]] = set()
    selected_pair_counts: Dict[str, int] = {}
    max_pairs_per_compound = max(6, min(10, limit_nodes // 12)) if center_id is None else None

    for source_id, target_id, _, _ in pair_candidates:
        if max_pairs_per_compound is not None:
            if (
                selected_pair_counts.get(source_id, 0) >= max_pairs_per_compound
                or selected_pair_counts.get(target_id, 0) >= max_pairs_per_compound
            ):
                continue
        needed = len({compound_id for compound_id in (source_id, target_id) if compound_id not in selected_ids})
        if len(selected_ids) + needed > limit_nodes:
            continue
        if add(source_id) and add(target_id):
            selected_pair_keys.add((source_id, target_id))
            if max_pairs_per_compound is not None:
                selected_pair_counts[source_id] = selected_pair_counts.get(source_id, 0) + 1
                selected_pair_counts[target_id] = selected_pair_counts.get(target_id, 0) + 1

    remaining_cards = sorted(
        cards,
        key=lambda card: (-(degree_score.get(card.compound_id, 0)), card.name or "", card.compound_id),
    )
    for card in remaining_cards:
        if len(selected_ids) >= limit_nodes:
            break
        add(card.compound_id)

    limited_edges = [
        edge
        for edge in edges
        if edge.source_compound_id in selected_ids and edge.target_compound_id in selected_ids
        and (center_id is not None or (edge.source_compound_id, edge.target_compound_id) in selected_pair_keys)
    ]
    visible_edge_ids = {edge.edge_id for edge in limited_edges}
    limited_edge_groups = []
    for group in edge_groups:
        if group.source_compound_id not in selected_ids or group.target_compound_id not in selected_ids:
            continue
        if center_id is None and (group.source_compound_id, group.target_compound_id) not in selected_pair_keys:
            continue
        kept_edge_ids = [edge_id for edge_id in group.edge_ids if edge_id in visible_edge_ids] or group.edge_ids
        kept_items = [item for item in group.items if item.edge_id in visible_edge_ids] or group.items
        limited_edge_groups.append(EdgeGroup(
            edge_group_id=group.edge_group_id,
            source_compound_id=group.source_compound_id,
            target_compound_id=group.target_compound_id,
            label=group.label,
            count=group.count,
            edge_ids=kept_edge_ids,
            items=kept_items,
        ))

    return [card_map[compound_id] for compound_id in ordered_ids], limited_edges, limited_edge_groups


async def _pick_default_center(db: AsyncSession) -> Optional[Compound]:
    result = await db.execute(
        select(ReactionCompound.compound_id, func.count().label("cnt"))
        .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
        .where(*displayable_compound_filters())
        .group_by(ReactionCompound.compound_id)
        .order_by(func.count().desc())
        .limit(1)
    )
    row = result.first()
    if not row:
        return None
    result = await db.execute(
        select(Compound).where(Compound.compound_id == row[0])
    )
    return result.scalar()


async def _build_global_graph_payload(
    db: AsyncSession,
    limit_nodes: Optional[int],
    source_types: Optional[List[str]],
    review_statuses: Optional[List[str]],
) -> GraphPayload:
    compounds = await _fetch_all_displayable_compounds(db)
    if not compounds:
        return GraphPayload()

    displayable_ids = {compound.compound_id for compound in compounds}
    rc_query = select(ReactionCompound, Reaction).join(
        Reaction, ReactionCompound.reaction_id == Reaction.reaction_id
    ).where(ReactionCompound.compound_id.in_(displayable_ids))

    if source_types:
        rc_query = rc_query.where(Reaction.source_type.in_(source_types))
    if review_statuses:
        rc_query = rc_query.where(Reaction.review_status.in_(review_statuses))

    rc_result = await db.execute(rc_query)
    rc_reaction_pairs = rc_result.all()

    reaction_map: Dict[str, Reaction] = {}
    rxn_compounds: Dict[str, Tuple[List[str], List[str]]] = {}
    for rc, reaction in rc_reaction_pairs:
        if rc.compound_id not in displayable_ids:
            continue
        reaction_map[reaction.reaction_id] = reaction
        if reaction.reaction_id not in rxn_compounds:
            rxn_compounds[reaction.reaction_id] = ([], [])
        substrates, products = rxn_compounds[reaction.reaction_id]
        target_list = substrates if rc.role.value == "substrate" else products
        if rc.compound_id not in target_list:
            target_list.append(rc.compound_id)

    reaction_ids = [
        reaction_id
        for reaction_id, (substrates, products) in rxn_compounds.items()
        if substrates and products
    ]
    if not reaction_ids:
        cards = [_compound_to_card(compound) for compound in compounds]
        return GraphPayload(nodes=cards[:limit_nodes] if limit_nodes else cards)

    edge_query = select(EnzymeReactionEdge, Enzyme).join(
        Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
    ).where(EnzymeReactionEdge.reaction_id.in_(reaction_ids))

    if source_types:
        edge_query = edge_query.where(EnzymeReactionEdge.source_type.in_(source_types))
    if review_statuses:
        edge_query = edge_query.where(EnzymeReactionEdge.review_status.in_(review_statuses))

    edge_result = await db.execute(edge_query)
    edge_rows = edge_result.all()
    gene_names = await _load_gene_names(db, {ere.enzyme_id for ere, _ in edge_rows})

    edge_records: List[dict] = []
    for ere, enz in edge_rows:
        reaction = reaction_map.get(ere.reaction_id)
        if not reaction:
            continue
        substrates, products = rxn_compounds.get(ere.reaction_id, ([], []))
        direction = reaction.direction.value if reaction.direction else "unknown"
        pairs: List[Tuple[str, str]] = []
        if direction in DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT:
            pairs.extend((source_id, target_id) for source_id in substrates for target_id in products if source_id != target_id)
        if direction in DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE:
            pairs.extend((source_id, target_id) for source_id in products for target_id in substrates if source_id != target_id)

        for source_id, target_id in pairs:
            edge_records.append({
                "from_cpd": source_id,
                "to_cpd": target_id,
                "edge_id": ere.edge_id,
                "enzyme_id": ere.enzyme_id,
                "enzyme": enz,
                "reaction_id": reaction.reaction_id,
                "reaction": reaction,
                "direction": direction,
                "source_type": ere.source_type.value if ere.source_type else "swiss_prot",
                "review_status": ere.review_status.value if ere.review_status else "official",
            })

    cards = [_compound_to_card(compound) for compound in compounds]
    card_map = {card.compound_id: card for card in cards}
    edges, edge_groups = _build_edges_and_groups(edge_records, card_map, gene_names)

    if limit_nodes and len(cards) > limit_nodes:
        cards, edges, edge_groups = _limit_graph_payload(
            cards,
            edges,
            edge_groups,
            None,
            limit_nodes,
        )

    return GraphPayload(
        nodes=cards,
        edges=edges,
        edge_groups=edge_groups,
    )


async def build_graph_payload_for_enzymes(
    db: AsyncSession,
    enzyme_ids: List[str],
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    limit_nodes: Optional[int] = 80,
) -> GraphPayload:
    """Build a focused subgraph from a specific set of enzymes.

    Used by the home "map search" reveal: given enzyme hits (e.g. top-N from a
    full-library entry search), return only the compound endpoints and the
    reaction edges those enzymes catalyse. Reuses the same edge-building path
    as the global/BFS graphs so the payload shape stays identical.
    """

    enzyme_ids = list(dict.fromkeys(enzyme_id for enzyme_id in enzyme_ids if enzyme_id))
    if not enzyme_ids:
        return GraphPayload()

    edge_query = select(EnzymeReactionEdge, Enzyme).join(
        Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
    ).where(EnzymeReactionEdge.enzyme_id.in_(enzyme_ids))

    if source_types:
        edge_query = edge_query.where(EnzymeReactionEdge.source_type.in_(source_types))
    if review_statuses:
        edge_query = edge_query.where(EnzymeReactionEdge.review_status.in_(review_statuses))

    edge_result = await db.execute(edge_query)
    edge_rows = edge_result.all()
    if not edge_rows:
        return GraphPayload()

    gene_names = await _load_gene_names(db, {ere.enzyme_id for ere, _ in edge_rows})
    return await _ere_rows_to_graph_payload(
        db,
        edge_rows,
        gene_names,
        limit_nodes=limit_nodes,
    )


async def _ere_rows_to_graph_payload(
    db: AsyncSession,
    edge_rows: List[Tuple[EnzymeReactionEdge, Enzyme]],
    gene_names: Dict[str, Optional[str]],
    limit_nodes: Optional[int] = 80,
    center_id: Optional[str] = None,
    keep_pair: Optional[Set[Tuple[str, str]]] = None,
) -> GraphPayload:
    """Shared "(EnzymeReactionEdge, Enzyme) row → GraphPayload" builder.

    Turns the given edge rows into reaction endpoints and the edges those rows
    catalyse, grouping unique rows by (source, target) exactly like the
    global/BFS graphs so every mode produces identical edge semantics. Used by
    both the whole-enzyme reveal (``build_graph_payload_for_enzymes``) and the
    compound-centered map scope. ``center_id`` is forwarded to the node limiter
    (and emitted as ``focus``) so the anchor is kept whenever the graph is
    trimmed.

    ``keep_pair`` — an optional set of ordered ``(source, target)`` compound
    pairs — restricts the drawn edges to exactly those pairs. Only that pair
    subset feeds ``endpoint_ids``, so a pathway union payload can limit its
    nodes to the compounds actually stepped through while keeping full
    per-group ``items``/per-edge ``card`` data for the client-side species /
    source-type filters.
    """

    if not edge_rows:
        return GraphPayload()

    reaction_ids = {ere.reaction_id for ere, _ in edge_rows}
    rxn_result = await db.execute(
        select(Reaction).where(Reaction.reaction_id.in_(reaction_ids))
    )
    reaction_map = {rxn.reaction_id: rxn for rxn in rxn_result.scalars().all()}

    rc_result = await db.execute(
        select(ReactionCompound).where(ReactionCompound.reaction_id.in_(reaction_ids))
    )
    all_rcs = rc_result.scalars().all()
    displayable_ids = await _fetch_displayable_compound_ids(
        db, {rc.compound_id for rc in all_rcs}
    )
    rxn_compounds: Dict[str, Tuple[List[str], List[str]]] = {}
    for rc in all_rcs:
        if rc.compound_id not in displayable_ids:
            continue
        if rc.reaction_id not in rxn_compounds:
            rxn_compounds[rc.reaction_id] = ([], [])
        if rc.role.value == "substrate":
            rxn_compounds[rc.reaction_id][0].append(rc.compound_id)
        else:
            rxn_compounds[rc.reaction_id][1].append(rc.compound_id)

    edge_records: List[dict] = []
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
            pairs.extend((source_id, target_id) for source_id in substrates for target_id in products if source_id != target_id)
        if direction in DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE:
            pairs.extend((source_id, target_id) for source_id in products for target_id in substrates if source_id != target_id)

        for source_id, target_id in pairs:
            edge_records.append({
                "from_cpd": source_id,
                "to_cpd": target_id,
                "edge_id": ere.edge_id,
                "enzyme_id": ere.enzyme_id,
                "enzyme": enz,
                "reaction_id": reaction.reaction_id,
                "reaction": reaction,
                "direction": direction,
                "source_type": ere.source_type.value if ere.source_type else "swiss_prot",
                "review_status": ere.review_status.value if ere.review_status else "official",
            })

    if keep_pair:
        edge_records = [
            record
            for record in edge_records
            if (record["from_cpd"], record["to_cpd"]) in keep_pair
        ]

    if not edge_records:
        return GraphPayload()

    endpoint_ids = {
        compound_id
        for record in edge_records
        for compound_id in (record["from_cpd"], record["to_cpd"])
    }
    compounds = await _fetch_compounds(db, endpoint_ids)
    cards = [_compound_to_card(compound) for compound in compounds]
    card_map = {card.compound_id: card for card in cards}

    edges, edge_groups = _build_edges_and_groups(edge_records, card_map, gene_names)

    if limit_nodes and len(cards) > limit_nodes:
        cards, edges, edge_groups = _limit_graph_payload(
            cards,
            edges,
            edge_groups,
            center_id,
            limit_nodes,
        )

    return GraphPayload(
        nodes=cards,
        edges=edges,
        edge_groups=edge_groups,
        focus=FocusPoint(node_id=center_id) if center_id else None,
    )


async def build_payload_from_edge_rows(
    db: AsyncSession,
    edge_rows: List[Tuple[EnzymeReactionEdge, Enzyme]],
    gene_names: Dict[str, Optional[str]],
    keep_pair: Optional[Set[Tuple[str, str]]] = None,
) -> GraphPayload:
    """Untrimmed GraphPayload from raw ``(EnzymeReactionEdge, Enzyme)`` rows.

    Thin public wrapper over ``_ere_rows_to_graph_payload`` with node trimming
    and a focus point disabled, so every returned-pathway step pair keeps its
    endpoint nodes. ``keep_pair`` narrows the drawn edges to exactly the given
    ordered compound pairs — used by pathway search to paint the union of the
    returned pathways' step pairs.
    """
    return await _ere_rows_to_graph_payload(
        db,
        edge_rows,
        gene_names,
        limit_nodes=None,
        center_id=None,
        keep_pair=keep_pair,
    )


async def build_pathway_union_payload(
    db: AsyncSession,
    edge_rows: List[Tuple[EnzymeReactionEdge, Enzyme]],
    keep_pair: Optional[Set[Tuple[str, str]]] = None,
) -> GraphPayload:
    """Untrimmed union payload for pathway search step pairs.

    Loads the enzyme gene names internally, then delegates to
    ``build_payload_from_edge_rows``. ``keep_pair`` holds exactly the ordered
    compound pairs some returned pathway steps on, so the resulting payload's
    nodes/edges/edgeGroups are the union of those pathways' elements (each group
    still keeps its full ``items`` for the client-side species/source filters).
    """
    gene_names = await _load_gene_names(
        db, {ere.enzyme_id for ere, _ in edge_rows}
    )
    return await build_payload_from_edge_rows(
        db,
        edge_rows,
        gene_names,
        keep_pair=keep_pair,
    )


async def _bfs_subgraph(
    db: AsyncSession,
    center_id: str,
    depth: int,
    source_types: Optional[List[str]],
    review_statuses: Optional[List[str]],
) -> Tuple[Set[str], List[dict]]:
    """BFS from center_id, returns (set of compound_ids, list of edge records)."""

    visited_compounds: Set[str] = {center_id}
    edge_records: List[dict] = []

    frontier: List[Tuple[str, int]] = [(center_id, 0)]

    while frontier:
        current_id, current_depth = frontier.pop(0)
        if current_depth >= depth:
            continue

        # All reaction_compound rows for this compound
        rc_query = select(ReactionCompound, Reaction).join(
            Reaction, ReactionCompound.reaction_id == Reaction.reaction_id
        ).where(ReactionCompound.compound_id == current_id)

        if source_types:
            rc_query = rc_query.where(Reaction.source_type.in_(source_types))
        if review_statuses:
            rc_query = rc_query.where(Reaction.review_status.in_(review_statuses))

        result = await db.execute(rc_query)
        rc_reaction_pairs = result.all()

        # Collect all reaction IDs to batch-query compounds and edges
        reaction_ids = list({
            rxn.reaction_id for _, rxn in rc_reaction_pairs
        })

        if not reaction_ids:
            continue

        # All compounds in these reactions (batch)
        all_rc_query = select(ReactionCompound).where(
            ReactionCompound.reaction_id.in_(reaction_ids)
        )
        rc_result = await db.execute(all_rc_query)
        all_rcs = rc_result.scalars().all()
        displayable_compound_ids = await _fetch_displayable_compound_ids(
            db, {rc.compound_id for rc in all_rcs}
        )

        # Group by reaction_id
        rxn_compounds: Dict[str, Tuple[List[str], List[str]]] = {}
        for rc in all_rcs:
            if rc.compound_id not in displayable_compound_ids:
                continue
            if rc.reaction_id not in rxn_compounds:
                rxn_compounds[rc.reaction_id] = ([], [])
            if rc.role.value == "substrate":
                rxn_compounds[rc.reaction_id][0].append(rc.compound_id)
            else:
                rxn_compounds[rc.reaction_id][1].append(rc.compound_id)

        # All edges for these reactions (batch)
        edge_query = select(EnzymeReactionEdge, Enzyme).join(
            Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
        ).where(EnzymeReactionEdge.reaction_id.in_(reaction_ids))

        if source_types:
            edge_query = edge_query.where(EnzymeReactionEdge.source_type.in_(source_types))
        if review_statuses:
            edge_query = edge_query.where(EnzymeReactionEdge.review_status.in_(review_statuses))

        edge_result = await db.execute(edge_query)
        edge_rows = edge_result.all()

        # Group edges by reaction_id
        rxn_edges: Dict[str, List[Tuple[EnzymeReactionEdge, Enzyme]]] = {}
        for ere, enz in edge_rows:
            if ere.reaction_id not in rxn_edges:
                rxn_edges[ere.reaction_id] = []
            rxn_edges[ere.reaction_id].append((ere, enz))

        # Determine outgoing edges from current compound
        for rc, reaction in rc_reaction_pairs:
            substrates, products = rxn_compounds.get(reaction.reaction_id, ([], []))
            direction = reaction.direction.value if reaction.direction else "unknown"

            targets = []
            if rc.role.value == "substrate" and direction in DIRECTION_ALLOWS_SUBSTRATE_TO_PRODUCT:
                targets = products
            elif rc.role.value == "product" and direction in DIRECTION_ALLOWS_PRODUCT_TO_SUBSTRATE:
                targets = substrates

            for target_id in targets:
                if target_id not in displayable_compound_ids:
                    continue
                # Record the edge(s)
                for ere, enz in rxn_edges.get(reaction.reaction_id, []):
                    # Avoid duplicate edges for same traversal
                    edge_records.append({
                        "from_cpd": current_id,
                        "to_cpd": target_id,
                        "edge_id": ere.edge_id,
                        "enzyme_id": ere.enzyme_id,
                        "enzyme": enz,
                        "reaction_id": reaction.reaction_id,
                        "reaction": reaction,
                        "direction": direction,
                        "source_type": ere.source_type.value if ere.source_type else "swiss_prot",
                        "review_status": ere.review_status.value if ere.review_status else "official",
                    })

                # Add target to visited and frontier
                if target_id not in visited_compounds:
                    visited_compounds.add(target_id)
                    if current_depth + 1 < depth:
                        frontier.append((target_id, current_depth + 1))

    return visited_compounds, edge_records


async def _fetch_compounds(db: AsyncSession, compound_ids: Set[str]) -> List[Compound]:
    result = await db.execute(
        select(Compound).where(Compound.compound_id.in_(list(compound_ids)))
        .where(*displayable_compound_filters())
    )
    return list(result.scalars().all())


async def _fetch_all_displayable_compounds(db: AsyncSession) -> List[Compound]:
    result = await db.execute(
        select(Compound)
        .where(*displayable_compound_filters())
    )
    return list(result.scalars().all())


async def _fetch_displayable_compound_ids(db: AsyncSession, compound_ids: Set[str]) -> Set[str]:
    if not compound_ids:
        return set()

    result = await db.execute(
        select(Compound.compound_id)
        .where(Compound.compound_id.in_(list(compound_ids)))
        .where(*displayable_compound_filters())
    )
    return {row[0] for row in result.all()}


async def _load_gene_names(db: AsyncSession, enzyme_ids: Set[str]) -> Dict[str, Optional[str]]:
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


def _compound_to_card(c: Compound) -> CompoundCard:
    return CompoundCard(
        compound_id=c.compound_id,
        name=c.name,
        chebi_id=c.chebi_id,
        smiles=c.smiles,
        formula=c.formula,
        charge=float(c.charge) if c.charge else None,
        average_mass=float(c.average_mass) if c.average_mass else None,
        inchi=c.inchi,
        inchi_key=c.inchi_key,
        structure_image_url=c.structure_image_url,
        chebi_url=c.chebi_url,
        description=c.description,
    )


def _build_edges_and_groups(
    edge_records: List[dict],
    card_map: Dict[str, CompoundCard],
    gene_names: Dict[str, Optional[str]],
) -> Tuple[List[ReactionEdge], List[EdgeGroup]]:
    """Group edges by (source, target) to detect overlaps."""

    # Deduplicate edge records by (from_cpd, to_cpd, enzyme_id, reaction_id)
    seen = set()
    unique_records = []
    for rec in edge_records:
        key = (rec["from_cpd"], rec["to_cpd"], rec["enzyme_id"], rec["reaction_id"])
        if key not in seen:
            seen.add(key)
            unique_records.append(rec)

    # Group by (from_cpd, to_cpd)
    groups: Dict[Tuple[str, str], List[dict]] = {}
    for rec in unique_records:
        key = (rec["from_cpd"], rec["to_cpd"])
        if key not in groups:
            groups[key] = []
        groups[key].append(rec)

    edges: List[ReactionEdge] = []
    edge_groups: List[EdgeGroup] = []

    for (from_cpd, to_cpd), recs in groups.items():
        group_id = f"GROUP_{from_cpd}_{to_cpd}"

        if len(recs) == 1:
            # Single edge: put in edges[]
            rec = recs[0]
            enz = rec["enzyme"]
            label = enz.uniprot_id or enz.enzyme_id

            edges.append(ReactionEdge(
                edge_id=rec["edge_id"],
                edge_group_id=group_id,
                reaction_id=rec["reaction_id"],
                enzyme_id=rec["enzyme_id"],
                source_compound_id=from_cpd,
                target_compound_id=to_cpd,
                label=label,
                direction=rec["direction"],
                source_type=rec["source_type"],
                review_status=rec["review_status"],
                card=_make_enzyme_card(
                    rec, from_cpd, to_cpd, rec["direction"],
                    recs[0]["reaction"],
                    gene_names.get(rec["enzyme_id"]),
                ),
            ))
        else:
            # Multiple edges: put in edgeGroups[]
            edge_ids = [r["edge_id"] for r in recs]
            enz_count = len(set(r["enzyme_id"] for r in recs))
            label = f"{enz_count}×enzyme" if enz_count > 1 else recs[0]["enzyme"].uniprot_id or recs[0]["enzyme"].enzyme_id

            edge_groups.append(EdgeGroup(
                edge_group_id=group_id,
                source_compound_id=from_cpd,
                target_compound_id=to_cpd,
                label=label,
                count=len(recs),
                edge_ids=edge_ids,
                items=[_edge_group_item(rec) for rec in recs],
            ))

    return edges, edge_groups


def _edge_group_item(rec: dict) -> EdgeGroupItem:
    enz = rec["enzyme"]
    return EdgeGroupItem(
        edge_id=rec["edge_id"],
        enzyme_id=enz.enzyme_id,
        label=enz.uniprot_id or enz.enzyme_id,
        organism_name=enz.organism_name,
        source_type=rec["source_type"],
        review_status=rec["review_status"],
    )


def _make_enzyme_card(
    rec: dict,
    source_compound_id: str,
    target_compound_id: str,
    direction: str,
    reaction,
    gene_name: Optional[str] = None,
) -> EnzymeCard:
    enz = rec["enzyme"]
    return EnzymeCard(
        edge_id=rec["edge_id"],
        enzyme_id=enz.enzyme_id,
        primary_name=enz.primary_name,
        uniprot_id=enz.uniprot_id,
        database_code=enz.enzyme_id,
        organism_name=enz.organism_name,
        gene_name=gene_name,
        ec_number=reaction.ec_number,
        reaction_id=rec["reaction_id"],
        reaction_equation=reaction.equation,
        reaction_direction=direction,
        source_type=rec["source_type"],
        review_status=rec["review_status"],
    )


async def expand_edge_group(
    db: AsyncSession,
    edge_group_id: str,
) -> List[ReactionEdge]:
    """Expand an edge group: return individual ReactionEdge cards from the edge IDs."""

    parts = edge_group_id.split("_", 2)
    if len(parts) < 3 or parts[0] != "GROUP":
        return []

    from_cpd = parts[1]
    to_cpd = parts[2]
    if {from_cpd, to_cpd} != await _fetch_displayable_compound_ids(db, {from_cpd, to_cpd}):
        return []

    # Find all edges between these two compounds
    # Query: enzyme_reaction_edge where source compound is from_cpd and target is to_cpd
    # (This is the reverse of BFS; we query by compound pair)

    # Get reactions involving from_cpd as substrate and to_cpd as product (or vice versa)
    # We need to find reactions where both from_cpd and to_cpd participate

    query = select(ReactionCompound.reaction_id).where(
        ReactionCompound.compound_id.in_([from_cpd, to_cpd])
    ).group_by(ReactionCompound.reaction_id).having(
        func.count(func.distinct(ReactionCompound.compound_id)) == 2
    )
    result = await db.execute(query)
    reaction_ids = [r[0] for r in result.all()]

    if not reaction_ids:
        return []

    # Get reaction details (with direction)
    rxn_query = select(Reaction).where(Reaction.reaction_id.in_(reaction_ids))
    rxn_result = await db.execute(rxn_query)
    reaction_map = {r.reaction_id: r for r in rxn_result.scalars().all()}

    # Get edges
    edge_query = select(EnzymeReactionEdge, Enzyme).join(
        Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
    ).where(EnzymeReactionEdge.reaction_id.in_(reaction_ids))

    edge_result = await db.execute(edge_query)
    edge_rows = edge_result.all()
    gene_names = await _load_gene_names(db, {ere.enzyme_id for ere, _ in edge_rows})

    # Get reaction_compound data to determine direction
    rc_query = select(ReactionCompound).where(
        ReactionCompound.reaction_id.in_(reaction_ids)
    )
    rc_result = await db.execute(rc_query)
    all_rcs = rc_result.scalars().all()

    # Build compound role lookup per reaction
    rxn_roles: Dict[str, Dict[str, str]] = {}
    for rc in all_rcs:
        if rc.reaction_id not in rxn_roles:
            rxn_roles[rc.reaction_id] = {}
        rxn_roles[rc.reaction_id][rc.compound_id] = rc.role.value

    edges = []
    for ere, enz in edge_rows:
        reaction = reaction_map.get(ere.reaction_id)
        if not reaction:
            continue

        direction = reaction.direction.value if reaction.direction else "unknown"
        label = enz.uniprot_id or enz.enzyme_id

        edges.append(ReactionEdge(
            edge_id=ere.edge_id,
            edge_group_id=edge_group_id,
            reaction_id=ere.reaction_id,
            enzyme_id=ere.enzyme_id,
            source_compound_id=from_cpd,
            target_compound_id=to_cpd,
            label=label,
            direction=direction,
            source_type=ere.source_type.value if ere.source_type else "swiss_prot",
            review_status=ere.review_status.value if ere.review_status else "official",
            card=_make_enzyme_card(
                {
                    "edge_id": ere.edge_id,
                    "enzyme_id": ere.enzyme_id,
                    "enzyme": enz,
                    "reaction_id": ere.reaction_id,
                    "reaction": reaction,
                    "direction": direction,
                    "source_type": ere.source_type.value if ere.source_type else "swiss_prot",
                    "review_status": ere.review_status.value if ere.review_status else "official",
                },
                from_cpd, to_cpd, direction, reaction,
                gene_names.get(ere.enzyme_id),
            ),
        ))

    return edges


# ---------------------------------------------------------------------------
# Map-scope: compound resolution + compound-centred reaction neighbourhood
# ---------------------------------------------------------------------------

_STEREO_PREFIX_RE = re.compile(r"^\([^)]*\)[\s-]*", re.IGNORECASE)


def _base_compound_name(name: str) -> str:
    """Lower-case a compound name with any leading stereo prefix removed.

    "(4R)-limonene" / "(4S)-limonene" / "(±)-limonene" all collapse to the
    shared base name, so one family search matches the generic metabolite and
    its stereoisomers together.
    """
    return _STEREO_PREFIX_RE.sub("", name or "").strip().lower()


async def _fetch_all_displayable_compounds(db: AsyncSession) -> List[Compound]:
    result = await db.execute(
        select(Compound).where(*displayable_compound_filters())
    )
    return list(result.scalars().all())


async def resolve_compound_family(
    db: AsyncSession,
    q: str,
) -> List[Compound]:
    """Resolve a search query to a displayable compound family, or [].

    Matching runs against every displayable compound:
      1. identifiers: ``compound_id == q`` (case-insensitive), or ``q`` is the
         ChEBI id of an existing compound (with or without the ``CHEBI:``
         prefix);
      2. name: whole-name equality (case-insensitive), then the same equality
         applied to the stereo-prefix-stripped base name.
    Every displayable compound sharing a matched base name joins the family, so
    typing ``limonene`` anchors limonene + (4R)/(4S)-limonene at once. Members
    that matched the literal query come first (so the searched stereoisomer is
    preferred when one is shown as the scope label).
    """
    raw = (q or "").strip()
    if not raw:
        return []
    query_lower = raw.lower()
    query_base = _base_compound_name(raw)

    compounds = await _fetch_all_displayable_compounds(db)
    matched_bases: Set[str] = set()
    literal_ids: List[str] = []

    for c in compounds:
        base = _base_compound_name(c.name)
        if base and query_base and base == query_base:
            matched_bases.add(base)
        name_lower = (c.name or "").lower()
        identifier_hit = (
            (c.compound_id and c.compound_id.lower() == query_lower)
            or (c.chebi_id and (
                query_lower == c.chebi_id.split(":")[-1].strip().lower()
                or query_lower == ("CHEBI:" + c.chebi_id.split(":")[-1].strip()).lower()
            ))
        )
        if name_lower == query_lower or identifier_hit:
            literal_ids.append(c.compound_id)
            if base:
                matched_bases.add(base)

    if not matched_bases:
        return []

    literal_set = set(literal_ids)
    family = [
        c for c in compounds
        if c.name and _base_compound_name(c.name) in matched_bases
    ]
    family.sort(key=lambda c: (
        0 if c.compound_id in literal_set else 1,
        len(c.name or ""),
        (c.name or "").lower(),
    ))
    return family


async def suggest_displayable_compounds(
    db: AsyncSession,
    q: str,
    limit: Optional[int] = 12,
) -> List[Compound]:
    """Prefix/contains suggestion over every displayable compound.

    Lower-cased matching (MySQL collation-independent) across ``compound_id``,
    bare/dashed ChEBI numbers and compound names, ranked:

      0. exact identifier or name equality,
      1. identifier prefix (``CHEBI:…`` id, or bare ChEBI digits),
      2. name prefix,
      3. name contains.

    Never returns water/proton/diphosphate or the unnamed placeholder rows, so
    it is safe to feed a pathway composer's autocomplete and the
    ``COMPOUND_NOT_FOUND`` candidate list.
    """
    raw = (q or "").strip().lower()
    if not raw:
        return []
    name_tier_cap = 200  # avoid pathological long tails for 1-char queries

    compounds = await _fetch_all_displayable_compounds(db)
    ranked: List[Tuple[int, int, str, Compound]] = []
    for c in compounds:
        name_lower = (c.name or "").lower()
        cid_lower = (c.compound_id or "").lower()
        chebi_lower = (c.chebi_id or "").lower()
        chebi_digits = chebi_lower.split(":")[-1] if chebi_lower else ""
        name_is_exact = bool(name_lower) and name_lower == raw
        cid_is_exact = bool(cid_lower) and cid_lower == raw
        chebi_is_exact = bool(chebi_digits) and (
            raw == chebi_digits or raw == ("chebi:" + chebi_digits)
        )

        if name_is_exact or cid_is_exact or chebi_is_exact:
            tier = 0
        elif bool(cid_lower) and cid_lower.startswith(raw):
            tier = 1
        elif raw.isdigit() and bool(chebi_digits) and chebi_digits.startswith(raw):
            tier = 1
        elif bool(name_lower) and name_lower.startswith(raw):
            tier = 2
        elif bool(name_lower) and raw in name_lower:
            tier = 3
        else:
            continue
        if tier >= 2 and len(ranked) > name_tier_cap:
            continue
        ranked.append((tier, len(name_lower), name_lower, c))

    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[3] for item in ranked[:limit]]


async def build_compound_scope_payload(
    db: AsyncSession,
    anchor_compounds: List[Compound],
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    limit_reactions: int = 14,
    limit_nodes: int = 90,
) -> Tuple[GraphPayload, List[str], int]:
    """Compound-centred reaction neighbourhood.

    Collects every reaction the anchor family participates in (either side),
    keeps only the ones that can render at least one displayable substrate →
    product pair, ranks them by enzyme-edge weight (official edges first, then
    total, then RHEA id for a stable order) and draws just those reactions'
    displayable endpoints + the edges catalysing them. The anchor stays in the
    middle — unrelated neighbours never fan out.

    Returns ``(payload, present_anchor_ids, reaction_count)``. ``payload`` is
    empty when the family takes part in no renderable catalysed reaction.
    """
    anchor_ids = [c.compound_id for c in anchor_compounds]
    anchor_set = set(anchor_ids)
    if not anchor_set:
        return GraphPayload(), [], 0

    anchor_rc_rows = (
        await db.execute(
            select(ReactionCompound).where(ReactionCompound.compound_id.in_(anchor_ids))
        )
    ).scalars().all()
    anchor_rc: Dict[str, List[ReactionCompound]] = {}
    for rc in anchor_rc_rows:
        anchor_rc.setdefault(rc.reaction_id, []).append(rc)
    candidate_ids = list(anchor_rc)
    if not candidate_ids:
        return GraphPayload(), [], 0

    # Inspect the FULL composition of each candidate reaction before judging it
    # renderable — ReactionCompound stores every participant of a reaction, not
    # just the anchor, so an anchor-only view would wrongly look one-sided.
    full_rows = (
        await db.execute(
            select(ReactionCompound).where(
                ReactionCompound.reaction_id.in_(candidate_ids)
            )
        )
    ).scalars().all()
    full_by_rxn: Dict[str, List[ReactionCompound]] = {}
    for rc in full_rows:
        full_by_rxn.setdefault(rc.reaction_id, []).append(rc)
    rc_compound_ids = {rc.compound_id for rc in full_rows}
    displayable_ids = await _fetch_displayable_compound_ids(db, rc_compound_ids)

    # Drop reactions that cannot render a displayable substrate → product pair,
    # so the anchor is never left dangling and ranking slots are not wasted.
    candidate_ids = [
        rxn_id
        for rxn_id, rcs in full_by_rxn.items()
        if (
            any(
                rc.role.value == "substrate" and rc.compound_id in displayable_ids
                for rc in rcs
            )
            and any(
                rc.role.value == "product" and rc.compound_id in displayable_ids
                for rc in rcs
            )
        )
    ]
    if not candidate_ids:
        return GraphPayload(), [], 0

    # Edge weight per reaction for ranking (official edges first).
    stat_rows = (
        await db.execute(
            select(
                EnzymeReactionEdge.reaction_id,
                EnzymeReactionEdge.review_status,
            ).where(EnzymeReactionEdge.reaction_id.in_(candidate_ids))
        )
    ).all()
    edge_stats: Dict[str, Tuple[int, int]] = {}
    for rxn_id, status in stat_rows:
        official, total = edge_stats.get(rxn_id, (0, 0))
        total += 1
        if getattr(status, "value", None) == "official":
            official += 1
        edge_stats[rxn_id] = (official, total)

    rhea_rows = (
        await db.execute(
            select(Reaction.reaction_id, Reaction.rhea_id).where(
                Reaction.reaction_id.in_(candidate_ids)
            )
        )
    ).all()
    rhea_map = {rxn_id: rhea for rxn_id, rhea in rhea_rows}

    def _rank_key(rxn_id: str):
        official, total = edge_stats.get(rxn_id, (0, 0))
        return (
            -official,
            -total,
            (rhea_map.get(rxn_id) or rxn_id).lower(),
            rxn_id,
        )

    selected_ids = sorted(candidate_ids, key=_rank_key)[:limit_reactions]
    selected_set = set(selected_ids)
    if not selected_set:
        return GraphPayload(), [], 0

    # Centre anchor: the family member that best matches the query wins
    # (``anchor_ids`` is ordered literal-match-first by the resolver, so the
    # member the user actually typed is preferred), falling back to the member
    # appearing in the most selected reactions.
    center_counts: Dict[str, int] = {}
    for rxn_id, rcs in anchor_rc.items():
        if rxn_id not in selected_set:
            continue
        for rc in rcs:
            if rc.compound_id in anchor_set:
                center_counts[rc.compound_id] = center_counts.get(rc.compound_id, 0) + 1
    center_anchor = next(
        (c for c in anchor_ids if center_counts.get(c, 0) > 0),
        None,
    )
    if center_anchor is None and center_counts:
        center_anchor = max(center_counts, key=center_counts.get)

    edge_query = select(EnzymeReactionEdge, Enzyme).join(
        Enzyme, EnzymeReactionEdge.enzyme_id == Enzyme.enzyme_id
    ).where(EnzymeReactionEdge.reaction_id.in_(selected_ids))
    if source_types:
        edge_query = edge_query.where(EnzymeReactionEdge.source_type.in_(source_types))
    if review_statuses:
        edge_query = edge_query.where(EnzymeReactionEdge.review_status.in_(review_statuses))
    edge_rows = (await db.execute(edge_query)).all()
    if not edge_rows:
        return GraphPayload(), [], 0

    gene_names = await _load_gene_names(db, {ere.enzyme_id for ere, _ in edge_rows})
    payload = await _ere_rows_to_graph_payload(
        db,
        edge_rows,
        gene_names,
        limit_nodes=limit_nodes,
        center_id=center_anchor,
    )

    payload_ids = {card.compound_id for card in payload.nodes}
    present_ids = [aid for aid in anchor_ids if aid in payload_ids]
    return payload, present_ids, len(selected_ids)


async def build_map_scope_payload(
    db: AsyncSession,
    q: str,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    limit_reactions: int = 14,
    limit_nodes: int = 90,
) -> dict:
    """Route a map-search query to a compound or enzyme scope.

    A query that resolves to a compound family builds the compound-centred
    neighbourhood; otherwise the enzyme-hit scope applies (implemented in a
    later step — for now such queries report ``kind == "none"``). The returned
    dict mirrors the ``/graph/map-scope`` response body in camelCase so the
    router can pass it straight into ``ApiResponse``.
    """
    raw = (q or "").strip()
    family = await resolve_compound_family(db, raw)
    if family:
        payload, present_ids, reaction_count = await build_compound_scope_payload(
            db,
            family,
            source_types=source_types,
            review_statuses=review_statuses,
            limit_reactions=limit_reactions,
            limit_nodes=limit_nodes,
        )
        if payload.nodes and present_ids:
            label_hit = next(
                (c for c in family if (c.name or "").lower() == raw.lower()),
                None,
            )
            present_set = set(present_ids)
            return {
                "kind": "compound",
                "query": raw,
                "total": len(family),
                "shown": len(present_ids),
                "anchorIds": [c.compound_id for c in family if c.compound_id in present_set],
                "anchorNames": [c.name for c in family if c.compound_id in present_set],
                "anchorLabel": label_hit.name if label_hit else family[0].name,
                "reactionCount": reaction_count,
                "enzymeIds": [],
                "graph": payload.model_dump(by_alias=True),
            }

    # Enzyme-hit scope: whole-enzyme display for the best-matched enzymes.
    enzyme_ids, candidate_count = await select_scope_enzymes(
        db,
        raw,
        source_types=source_types,
        review_statuses=review_statuses,
    )
    if enzyme_ids:
        payload = await build_graph_payload_for_enzymes(
            db,
            enzyme_ids,
            source_types=source_types,
            review_statuses=review_statuses,
            limit_nodes=limit_nodes,
        )
        if payload.nodes:
            return {
                "kind": "enzyme",
                "query": raw,
                "total": candidate_count,
                "shown": len(enzyme_ids),
                "anchorIds": [],
                "anchorNames": [],
                "anchorLabel": None,
                "reactionCount": 0,
                "enzymeIds": enzyme_ids,
                "graph": payload.model_dump(by_alias=True),
            }

    return {
        "kind": "none",
        "query": raw,
        "total": 0,
        "shown": 0,
        "anchorIds": [],
        "anchorNames": [],
        "anchorLabel": None,
        "reactionCount": 0,
        "enzymeIds": [],
        "graph": GraphPayload().model_dump(by_alias=True),
    }


# ---------------------------------------------------------------------------
# Map-scope enzyme hit selection (name-first ranking + homolog dedupe)
# ---------------------------------------------------------------------------

_ENZYME_TRAILING_WORDS = re.compile(
    r"\s+(?:synthases?|cyclases?|enzymes?|oxygenases?|lyases?|"
    r"proteins?|isozymes?|isoforms?|dehydrogenases?|reductases?|"
    r"transferases?|hydrolases?)\s*$",
    re.IGNORECASE,
)


def _strip_enzyme_trailing_words(text: str) -> str:
    """Trim trailing enzyme-family words from a lower-case query.

    "germacrene d synthase" / "limonene synthase" → "germacrene d" / "limonene",
    i.e. the product keyword that should show up on the reaction product side.
    """
    result = (text or "").strip().lower()
    while True:
        stripped = _ENZYME_TRAILING_WORDS.sub("", result)
        if stripped == result:
            return stripped
        result = stripped


def _product_side_text(equation: str) -> str:
    """Lower-case text after the reaction arrow (or the whole equation)."""
    eq = (equation or "").lower()
    match = re.search(r"(?:=>|→|->)", eq)
    return eq[match.end():].strip() if match else eq.strip()


async def select_scope_enzymes(
    db: AsyncSession,
    q: str,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    top_n: int = 12,
) -> Tuple[List[str], int]:
    """Pick the enzymes whose scope a map search should reveal.

    Runs the normal weighted library search (up to 200 candidates), then keeps
    only hits that are genuinely *about* the query, in priority order:

    1. **name matches** — enzymes whose name carries the query (after stripping
       a leading stereo prefix). This is what "germacrene D synthase" or
       "limonene synthase" should return: the actual synthases, nothing else.
    2. If no name match exists, **product matches** — enzymes whose reaction
       makes the compound the query points at.
    3. Only if neither exists do we fall back to the raw search top-N, so the
       scope is never worse than the old behaviour.

    Homologous species copies of the same reaction are collapsed (one
    representative per EC + product-side pair) so a 26-species enzyme family
    does not flood the scope. Returns ``(enzyme_ids, candidate_count)``.
    """
    raw = (q or "").strip()
    if not raw:
        return [], 0

    try:
        cards, _, _ = await search_entries(
            db,
            raw,
            view_mode="table",
            page_size=200,
            source_types=source_types,
            review_statuses=review_statuses,
        )
    except Exception:
        return [], 0
    if not cards:
        return [], 0

    q_lower = raw.lower()
    q_base = _base_compound_name(raw)  # stereo-prefix-stripped query
    product_keyword = _strip_enzyme_trailing_words(q_lower)

    def dedupe(entries: List[EnzymeCard]) -> List[EnzymeCard]:
        """One representative per (EC, product-side) pair, first occurrence wins."""
        seen: Set[Tuple[str, str]] = set()
        out: List[EnzymeCard] = []
        for card in entries:
            key = (
                (card.ec_number or "").strip().upper(),
                re.sub(r"[^a-z0-9 ]+", " ", _product_side_text(card.reaction_equation or "")),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(card)
        return out

    strong: List[EnzymeCard] = []
    weak: List[EnzymeCard] = []
    for card in cards:
        name_lower = (card.primary_name or "").strip().lower()
        name_base = _base_compound_name(name_lower)
        name_hit = bool(
            (q_lower and q_lower in name_base)
            or (q_lower and q_lower in name_lower)
            or (q_base and q_base in name_base)
        )
        if name_hit:
            strong.append(card)
            continue
        if product_keyword and product_keyword in _product_side_text(
            card.reaction_equation or ""
        ):
            weak.append(card)

    if strong:
        # All members name-matched the query; pick representatives deterministically
        # so a homolog tie (same EC + product) always resolves to the same enzyme.
        strong.sort(key=lambda c: (
            len(c.primary_name or ""),
            (c.primary_name or "").lower(),
            (c.ec_number or "").strip().upper(),
        ))
        selected = dedupe(strong)[:top_n]
    elif weak:
        selected = dedupe(weak)[:top_n]
    else:
        selected = dedupe(cards)[:top_n]

    if not selected:
        return [], len(cards)
    return [card.enzyme_id for card in selected], len(cards)
