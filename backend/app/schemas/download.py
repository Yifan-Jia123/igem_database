from typing import Optional, List

from app.schemas.common import CamelModel


class PathwayStepEnzyme(CamelModel):
    """One enzyme picked for a pathway step in the pathway drawer."""

    enzyme_id: str
    name: Optional[str] = None
    organism_name: Optional[str] = None
    uniprot_id: Optional[str] = None


class PathwayStep(CamelModel):
    """A hand-picked step of a route, as recorded by the download enzyme picker."""

    step: int
    source_id: str
    source_name: str
    target_id: str
    target_name: str
    enzymes: List[PathwayStepEnzyme] = []


class DownloadItem(CamelModel):
    entity_type: str
    entity_id: str
    display_label: Optional[str] = None
    # Pathway routes only. `compound_ids` is the ordered chain; `steps` carries the
    # chosen enzymes when the route went through the picker drawer. A route queued
    # straight from the map sends the chain with no steps, and the service resolves
    # each step against the database instead.
    compound_ids: List[str] = []
    compound_names: List[str] = []
    steps: List[PathwayStep] = []


class DownloadPreviewRequest(CamelModel):
    download_type: str
    items: List[DownloadItem]
    fields: List[str]
    format: str = "csv"
    # The pathway export bundles the enzyme page's table into the same archive, so
    # a pathway download also sends whatever enzymes the queue is holding.
    enzyme_items: List[DownloadItem] = []


class DownloadCreateRequest(CamelModel):
    download_type: str
    items: List[DownloadItem]
    fields: List[str]
    format: str = "csv"
    enzyme_items: List[DownloadItem] = []
