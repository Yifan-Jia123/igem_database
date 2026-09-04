import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

# The TSV inputs live at the repository root unless an explicit data directory is supplied.
_data_dir = os.getenv("IGEM_DATA_DIR", "").strip()
DATA_DIR = Path(_data_dir).expanduser() if _data_dir else PROJECT_ROOT

DB_CONFIG = {
    "host": os.getenv("IGEM_DB_HOST", "localhost"),
    "port": int(os.getenv("IGEM_DB_PORT", "3306")),
    "user": os.getenv("IGEM_DB_USER", "root"),
    "password": os.getenv("IGEM_DB_PASSWORD", ""),
    "database": os.getenv("IGEM_DB_NAME", "igem_terpene"),
    "charset": "utf8mb4",
}

DB_URL = (
    f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    f"?charset={DB_CONFIG['charset']}"
)

DIRECTION_MAP = {
    "left-to-right": "forward",
    "right-to-left": "reverse",
    "bidirectional": "reversible",
    "not specified": "unknown",
}
