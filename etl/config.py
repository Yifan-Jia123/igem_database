import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.dirname(BASE_DIR)

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
