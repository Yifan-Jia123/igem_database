import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
load_dotenv(BASE_DIR / ".env")

# 仓库实际布局：原始 TSV 数据位于项目根目录下的 for_* 目录中
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
