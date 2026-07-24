from typing import Optional, List

from app.schemas.common import CamelModel


class DownloadItem(CamelModel):
    entity_type: str
    entity_id: str
    edge_id: Optional[str] = None
    pathway_id: Optional[str] = None
    display_label: Optional[str] = None


class DownloadPreviewRequest(CamelModel):
    download_type: str
    items: List[DownloadItem]
    fields: List[str]
    format: str = "csv"
    include_external_links: bool = False
    include_graph_image: bool = False


class DownloadPreviewResponse(CamelModel):
    columns: List[str]
    row_count: int
    estimated_file_name: str


class DownloadCreateRequest(CamelModel):
    download_type: str
    items: List[DownloadItem]
    fields: List[str]
    format: str = "csv"
    include_external_links: bool = False
    include_graph_image: bool = False


class DownloadCreateResponse(CamelModel):
    file_url: Optional[str] = None
    status: str = "generating"
