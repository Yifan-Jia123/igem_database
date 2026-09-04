from typing import List, Dict, Set, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import (
    Compound, Enzyme, Gene, Reaction,
    ReactionCompound, EnzymeReactionEdge,
)
from app.schemas.graph import GraphPayload, ReactionEdge, EdgeGroup, FocusPoint
from app.schemas.compound import CompoundCard
from app.schemas.enzyme import EnzymeCard
from app.utils.compound_filters import displayable_compound_filters


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
    limited_edge_groups = [
        EdgeGroup(
            edge_group_id=group.edge_group_id,
            source_compound_id=group.source_compound_id,
            target_compound_id=group.target_compound_id,
            label=group.label,
            count=group.count,
            edge_ids=[edge_id for edge_id in group.edge_ids if edge_id in visible_edge_ids] or group.edge_ids,
        )
        for group in edge_groups
        if group.source_compound_id in selected_ids and group.target_compound_id in selected_ids
        and (center_id is not None or (group.source_compound_id, group.target_compound_id) in selected_pair_keys)
    ]

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
            ))

    return edges, edge_groups


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
