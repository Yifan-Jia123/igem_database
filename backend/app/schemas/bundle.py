"""Schema for the `/bundle/*` aggregation endpoint.

The discriminator key lives on the **outer envelope**'s `kind` (the discriminated
union of `BundleData`), not on the `entity` field's
`Union[EnzymeDetail, CompoundCard, ReactionDetail]` -- the latter also works at
runtime via pydantic's smart union, but it's semantically ambiguous and drops the
`kind` tag.

Note: no existing route declares a `response_model`, and `ApiResponse.data` is
`Any`, so `data` in `/docs` is untyped anyway -- chose here for runtime clarity,
not OpenAPI rendering. To get the shape into the docs you'd have to make
`ApiResponse` generic, which is out of scope for this endpoint.
"""
from typing import Annotated, List, Literal, Optional, Union

from pydantic import Field

from app.schemas.common import CamelModel, ErrorDetail
from app.schemas.compound import CompoundCard
from app.schemas.enzyme import EnzymeDetail
from app.schemas.pathway import PathwayCard
from app.schemas.reaction import ReactionDetail

BUNDLE_KINDS = ("enzyme", "compound", "reaction", "pathway")

#: Max items per batch. A single enzyme issues `~8 + 2·R` queries and does no
#: prefetching, so 50 items is already hundreds of queries; raising it means
#: considering prefetch too.
MAX_BATCH_ITEMS = 50

BundleKind = Literal["enzyme", "compound", "reaction", "pathway"]
ImageSource = Literal["chebi_proxy", "database", "none"]


class CompoundImage(CamelModel):
    compound_id: str
    name: str
    chebi_id: Optional[str] = None
    image_url: Optional[str] = None
    source: ImageSource = "none"


class EnzymeBundle(CamelModel):
    kind: Literal["enzyme"] = "enzyme"
    entity_id: str
    entity: EnzymeDetail
    images: List[CompoundImage] = Field(default_factory=list)


class CompoundBundle(CamelModel):
    kind: Literal["compound"] = "compound"
    entity_id: str
    entity: CompoundCard
    images: List[CompoundImage] = Field(default_factory=list)


class ReactionBundle(CamelModel):
    kind: Literal["reaction"] = "reaction"
    entity_id: str
    entity: ReactionDetail
    images: List[CompoundImage] = Field(default_factory=list)


class PathwayBundle(CamelModel):
    """`entityId` is `PATH_A_B_C` -- a chain of compounds, not a row in a table.

    Note it is **not a stable primary key**: the id points to a reachability, so
    after an ETL re-run removes some edge, the same id returns nothing. Don't
    treat it as the handle of "some particular search" either -- the filter
    conditions are not in the id.
    """

    kind: Literal["pathway"] = "pathway"
    entity_id: str
    entity: PathwayCard
    images: List[CompoundImage] = Field(default_factory=list)


BundleData = Annotated[
    Union[EnzymeBundle, CompoundBundle, ReactionBundle, PathwayBundle],
    Field(discriminator="kind"),
]


class BundleBatchItem(CamelModel):
    # Deliberately str rather than BundleKind: a single illegal kind only makes
    # that one item found:false, it doesn't 422 the whole request.
    kind: str
    entity_id: str


class BundleBatchRequest(CamelModel):
    items: List[BundleBatchItem] = Field(..., min_length=1, max_length=MAX_BATCH_ITEMS)


class BundleBatchResult(CamelModel):
    kind: str
    entity_id: str
    found: bool
    bundle: Optional[BundleData] = None
    error: Optional[ErrorDetail] = None


class BundleBatchData(CamelModel):
    items: List[BundleBatchResult] = Field(default_factory=list)
