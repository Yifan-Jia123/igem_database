from pathlib import Path

from pydantic_settings import BaseSettings


BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "igem_terpene"
    blast_bin: str = "blastp"
    blast_db: str = "blast_db/igem_enzymes"

    @property
    def db_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?charset=utf8mb4"
        )

    class Config:
        env_file = BACKEND_DIR / ".env"
        env_prefix = "IGEM_"


settings = Settings()
