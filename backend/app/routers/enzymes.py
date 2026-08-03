from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError, OperationalError

from app.deps import get_db
from app.models import Enzyme, Gene, GeneSequenceLink, Evidence, EnzymeReactionEdge, Reaction, ReactionCompound, Compound
from app.schemas.common import ApiResponse
from app.schemas.enzyme import EnzymeDetail, EnzymeReactionItem, ExternalLink
from app.schemas.gene import GeneSummary, SequenceLink
from app.schemas.evidence import EvidenceItem
from app.schemas.compound import CompoundCard
from app.utils.compound_filters import displayable_compound_filters

router = APIRouter()


@router.get("/enzymes/{enzyme_id}")
async def get_enzyme_detail(
    enzyme_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enzyme).where(Enzyme.enzyme_id == enzyme_id)
    )
    enz = result.scalar()
    if not enz:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Enzyme {enzyme_id} not found"})
    enzyme_values = {
        "enzyme_id": enz.enzyme_id,
        "primary_name": enz.primary_name,
        "secondary_names": enz.secondary_names or [],
        "uniprot_id": enz.uniprot_id,
        "organism_name": enz.organism_name,
        "sequence": enz.sequence,
        "length": enz.length,
        "mass": float(enz.mass) if enz.mass is not None else None,
    }

    # Gene
    gene_result = await db.execute(select(Gene).where(Gene.enzyme_id == enzyme_values["enzyme_id"]))
    gene = gene_result.scalar()
    gene_summary = None
    gene_ncbi_url = None
    if gene:
        gene_ncbi_url = gene.ncbi_url
        gene_summary = GeneSummary(
            gene_name=gene.gene_name,
            gene_record_id=str(gene.gene_id),
            genbank_id=gene.genbank_id,
            ncbi_url=gene.ncbi_url,
            ena_accession=gene.ena_accession,
            protein_accession=gene.protein_accession,
        )

    # Sequence links
    sequence_links = []
    if await _sequence_link_table_exists(db):
        seq_link_result = await db.execute(
            select(GeneSequenceLink)
            .where(GeneSequenceLink.enzyme_id == enzyme_values["enzyme_id"])
            .order_by(GeneSequenceLink.link_category, GeneSequenceLink.sequence_link_id)
        )
        sequence_links = [
            SequenceLink(
                category=link.link_category,
                accession=link.accession,
                url=link.url,
                related_accession=link.related_accession,
                related_url=link.related_url,
            )
            for link in seq_link_result.scalars()
        ]

    # Evidence
    ev_result = await db.execute(select(Evidence).where(Evidence.enzyme_id == enzyme_values["enzyme_id"]))
    evidences = [
        EvidenceItem(
            doi=e.doi,
            pubmed_id=e.pubmed_id,
            source_description=e.source_description,
            review_status=e.review_status.value,
        ) for e in ev_result.scalars()
    ]

    # Reactions
    edge_result = await db.execute(
        select(EnzymeReactionEdge).where(EnzymeReactionEdge.enzyme_id == enzyme_values["enzyme_id"])
    )
    edges = edge_result.scalars().all()
    reaction_items = []
    for edge in edges:
        rxn_result = await db.execute(
            select(Reaction).where(Reaction.reaction_id == edge.reaction_id)
        )
        rxn = rxn_result.scalar()
        if not rxn:
            continue

        rc_result = await db.execute(
            select(ReactionCompound, Compound)
            .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
            .where(ReactionCompound.reaction_id == rxn.reaction_id)
            .where(*displayable_compound_filters())
        )
        substrates, products = [], []
        for rc, cpd in rc_result.all():
            card = CompoundCard(
                compound_id=cpd.compound_id,
                name=cpd.name,
                chebi_id=cpd.chebi_id,
                smiles=cpd.smiles,
                average_mass=cpd.average_mass,
                chebi_url=cpd.chebi_url,
                structure_image_url=cpd.structure_image_url,
            )
            if rc.role.value == "substrate":
                substrates.append(card)
            else:
                products.append(card)

        reaction_items.append(EnzymeReactionItem(
            reaction_id=rxn.reaction_id,
            rhea_id=rxn.rhea_id,
            rhea_url=rxn.rhea_url,
            equation=rxn.equation,
            direction=rxn.direction.value,
            ec_number=rxn.ec_number,
            smiles=rxn.smiles,
            atom_map_image_url=rxn.atom_map_image_url,
            substrates=substrates,
            products=products,
            source_type=rxn.source_type.value if rxn.source_type else "swiss_prot",
            review_status=rxn.review_status.value if rxn.review_status else "official",
        ))

    # External links
    links = []
    if enzyme_values["uniprot_id"]:
        links.append(ExternalLink(label="UniProt", url=f"https://www.uniprot.org/uniprotkb/{enzyme_values['uniprot_id']}"))
    if gene_ncbi_url:
        links.append(ExternalLink(label="NCBI", url=gene_ncbi_url))

    detail = EnzymeDetail(
        enzyme_id=enzyme_values["enzyme_id"],
        database_code=enzyme_values["enzyme_id"],
        primary_name=enzyme_values["primary_name"],
        secondary_names=enzyme_values["secondary_names"],
        uniprot_id=enzyme_values["uniprot_id"],
        uniprot_url=f"https://www.uniprot.org/uniprotkb/{enzyme_values['uniprot_id']}" if enzyme_values["uniprot_id"] else None,
        organism_name=enzyme_values["organism_name"],
        sequence=enzyme_values["sequence"],
        length=enzyme_values["length"],
        mass=enzyme_values["mass"],
        gene=gene_summary,
        sequence_links=sequence_links,
        reactions=reaction_items,
        evidence=evidences,
        links=links,
    )

    return ApiResponse(data=detail.model_dump(by_alias=True))


async def _sequence_link_table_exists(db: AsyncSession) -> bool:
    try:
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :table_name"
            ),
            {"table_name": "gene_sequence_link"},
        )
        return bool(result.scalar())
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return False
