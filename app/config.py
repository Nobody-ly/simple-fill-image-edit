from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = Path(os.getenv("SIMPLE_FILL_DATA", ROOT / "data"))
    host: str = os.getenv("SIMPLE_FILL_HOST", "127.0.0.1")
    port: int = int(os.getenv("SIMPLE_FILL_PORT", "7862"))

    image_api_base_url: str = os.getenv(
        "IMAGE_API_BASE_URL", "https://api.openai.com/v1"
    ).rstrip("/")
    image_api_key: str = os.getenv("IMAGE_API_KEY", "").strip()
    image_model: str = os.getenv("IMAGE_MODEL", "gpt-image-2")
    image_field: str = os.getenv("IMAGE_FIELD", "image[]")
    image_transport: str = os.getenv("IMAGE_API_TRANSPORT", "multipart").strip().lower()
    image_auth_scheme: str = os.getenv("IMAGE_API_AUTH_SCHEME", "Bearer").strip()
    image_route_header_name: str = os.getenv("IMAGE_ROUTE_HEADER_NAME", "").strip()
    image_route_header_value: str = os.getenv("IMAGE_ROUTE_HEADER_VALUE", "").strip()
    image_timeout_seconds: int = int(os.getenv("IMAGE_TIMEOUT_SECONDS", "600"))

    wavespeed_base_url: str = os.getenv(
        "WAVESPEED_BASE_URL", "https://api.wavespeed.ai/api/v3"
    ).rstrip("/")
    wavespeed_api_key: str = os.getenv("WAVESPEED_API_KEY", "").strip()

    max_upload_mib: int = int(os.getenv("MAX_UPLOAD_MIB", "200"))
    max_image_pixels: int = int(os.getenv("MAX_IMAGE_PIXELS", "120000000"))
    worker_count: int = int(os.getenv("SIMPLE_FILL_WORKERS", "3"))

    @property
    def image_ready(self) -> bool:
        return bool(self.image_api_key)

    @property
    def sam3_ready(self) -> bool:
        return bool(self.wavespeed_api_key)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
