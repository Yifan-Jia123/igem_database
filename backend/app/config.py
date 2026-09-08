from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = ""
    db_name: str = "igem_terpene"
    # NCBI BLAST+ 二进制所在目录(含 blastp.exe/makeblastdb.exe)。空串 = 依赖系统 PATH。
    blast_bin_dir: str = "blast_bin"
    # BLAST subject 库工作目录(fasta + makeblastdb 产物 + 签名缓存)。
    # 若最终路径含非 ASCII 字符(BLAST+ 的 LMDB 库无法写入),blast_service 会自动改用系统临时目录。
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
