from app.models import Compound


EXCLUDED_COMMON_COMPOUND_IDS = {
    "CHEBI:15377",  # water
    "CHEBI:15378",  # proton
    "CHEBI:33019",  # diphosphate
    #
    # The following are **redox / energy / group-carrier cofactors**. They are the
    # most highly connected compounds in the whole database, and the home map
    # picks nodes by degree (the edge group's `count` -- records, not enzymes)
    # -- so a large share of a 300-point screen was NAD±/SAM/ATP-type molecules
    # that "every terpenoid synthesis route runs through, but which aren't
    # terpenoids themselves", crowding squalene, lycopene and germacrene A off
    # the canvas.
    # Measured: 19 points swapped out after exclusion, all terpenoids moving in;
    # edge groups 227 -> 175 (what is dropped is pairings among the cofactors).
    #
    # ⚠️ Intermediates on the route are deliberately **not** collected --
    # excluding these would erase the reaction itself, not just drop a point:
    #   acetyl-CoA / acetoacetyl-CoA / HMG-CoA / mevalonate  (mevalonate pathway)
    #   IPP / DMAPP / GPP / FPP / GGPP                       (terpenoid precursors)
    #   4-CDP-2-C-methyl-D-erythritol(+2-phosphate)          (MEP pathway)
    #   D-glyceraldehyde 3-phosphate                         (MEP pathway entry)
    # Criterion: "is it part of a terpenoid backbone?" Yes -> keep; just a
    # hitchhiking cofactor -> collect it here.
    "CHEBI:57540",  # NAD(1-)
    "CHEBI:57945",  # NADH(2-)
    "CHEBI:57783",  # NADPH(4-)
    "CHEBI:58437",  # deamido-NAD(2-)  (NAD biosynthesis intermediate)
    "CHEBI:30616",  # ATP(4-)
    "CHEBI:456216",  # ADP(3-)
    "CHEBI:58210",  # FMN(3-)
    "CHEBI:57618",  # FMNH2(2-)
    "CHEBI:87467",  # prenyl-FMNH2(2-)  (cofactor of a few terpenoid synthases)
    "CHEBI:59789",  # S-adenosyl-L-methionine (SAM, methyl donor)
    "CHEBI:57443",  # S-adenosylmethioninaminium  (another protonation state of SAM)
    "CHEBI:58533",  # S-methyl-5-thio-alpha-D-ribose 1-phosphate (MTA, SAM by-product)
    "CHEBI:90779",  # S-substituted glutathione(1-)
    "CHEBI:16240",  # hydrogen peroxide
}


def displayable_compound_filters():
    """SQLAlchemy filters for curated compounds that should appear as graph nodes."""
    return [
        ~Compound.compound_id.in_(EXCLUDED_COMMON_COMPOUND_IDS),
        Compound.name != Compound.compound_id,
    ]
