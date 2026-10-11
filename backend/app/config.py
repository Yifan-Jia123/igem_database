from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "igem_terpene"
    # Directory holding the NCBI BLAST+ binaries (with blastp.exe/makeblastdb.exe).
    # Empty string = rely on the system PATH.
    blast_bin_dir: str = "blast_bin"
    # Working directory for the BLAST subject database (fasta + makeblastdb
    # output + signature cache).
    # If the final path contains non-ASCII characters (which BLAST+'s LMDB
    # database cannot write), blast_service automatically falls back to the
    # system temp directory.
    blast_work_dir: str = "blast_work"

    @property
    def db_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
            f"?charset=utf8mb4"
        )

    class Config:
        env_file = ".env"
        env_prefix = "IGEM_"


settings = Settings()
