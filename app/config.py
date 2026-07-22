import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    navidrome_url: str
    navidrome_user: str
    navidrome_pass: str

    @property
    def is_configured(self) -> bool:
        return bool(self.navidrome_url and self.navidrome_user and self.navidrome_pass)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        navidrome_url=os.environ.get("NAVIDROME_URL", "").strip().rstrip("/"),
        navidrome_user=os.environ.get("NAVIDROME_USER", "").strip(),
        navidrome_pass=os.environ.get("NAVIDROME_PASS", "").strip(),
    )
