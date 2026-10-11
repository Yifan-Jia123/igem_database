"""The backend's only 12-field CompoundCard assembler.

It lives in `app/utils/` rather than some service so every service can import it
without a cycle: `entity_service` calls `pathway_service`, and `pathway_service`
calls `graph_service`; if `graph_service` imported `entity_service` back, the
cycle would close. This module depends only on models and schemas, so anyone can
import it safely.

`CompoundCard` was previously assembled independently in 4 places
(routers/enzymes.py, routers/compounds.py, routers/reactions.py,
services/graph_service.py); after consolidation there is only one.
"""
from app.models import Compound
from app.schemas.compound import CompoundCard


def compound_to_card(c: Compound) -> CompoundCard:
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
