from app.models import Compound


EXCLUDED_COMMON_COMPOUND_IDS = {
    "CHEBI:15377",  # water
    "CHEBI:15378",  # proton
    "CHEBI:33019",  # diphosphate
}


def displayable_compound_filters():
    """SQLAlchemy filters for curated compounds that should appear as graph nodes."""
    return [
        ~Compound.compound_id.in_(EXCLUDED_COMMON_COMPOUND_IDS),
        Compound.name != Compound.compound_id,
    ]
