import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# IGEM_DATA_DIR 用于沙箱: 让 ETL 读另一份 for_* (如 _sandbox/), 而不是真实数据目录。
# 没有它就无法在动真库之前验证「幂等 / 编号不变」这些不变量。
DATA_DIR = os.getenv("IGEM_DATA_DIR") or os.path.dirname(BASE_DIR)

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
