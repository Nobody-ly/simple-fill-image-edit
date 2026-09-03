from dataclasses import dataclass
from pathlib import Path
import os


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = Path(os.getenv("CATSCO_INPAINT_DATA", ROOT / "data"))
    upstream_dir: Path = Path(os.getenv(
        "INPAINT_ANYTHING_UPSTREAM",
        r"D:\codex_workspace\catsco-inpaint-anything-2026-upstream",
    ))
    wavespeed_key_file: Path = Path(os.getenv(
        "WAVESPEED_API_KEY_FILE",
        ROOT / "data" / ".secrets" / "wavespeed-api-key.txt",
    ))
    wavespeed_base: str = "https://api.wavespeed.ai/api/v3"
    ssh_host: str = os.getenv("CATSCO_IMAGE2_HOST", "203.32.85.223")
    ssh_user: str = os.getenv("CATSCO_IMAGE2_USER", "root")
    ssh_key: Path = Path(os.getenv(
        "CATSCO_IMAGE2_SSH_KEY",
        str(Path.home() / ".ssh" / "worker2_203_32_85_223_ed25519_v2"),
    ))
    remote_root: str = "/srv/catsco-agent"
    remote_run_root: str = "/srv/catsco-agent/work/inpaint-anything-runs"
    remote_node: str = "/srv/catsco-agent/tools/node-v24.18.0/bin/node"
    remote_skill: str = "/srv/catsco-agent/skills/image-asset-generator"
    masked_image2_base_url: str = os.getenv(
        "CATSCO_MASKED_IMAGE2_BASE_URL", "https://api.openai.com/v1",
    ).rstrip("/")
    masked_image2_api_key: str = os.getenv("CATSCO_MASKED_IMAGE2_API_KEY", "").strip()
    masked_image2_api_key_file: Path | None = (
        Path(os.environ["CATSCO_MASKED_IMAGE2_API_KEY_FILE"])
        if os.getenv("CATSCO_MASKED_IMAGE2_API_KEY_FILE") else None
    )
    masked_image2_model: str = os.getenv("CATSCO_MASKED_IMAGE2_MODEL", "gpt-image-2")
    masked_image2_timeout: int = int(os.getenv("CATSCO_MASKED_IMAGE2_TIMEOUT", "600"))
    masked_image2_image_field: str = os.getenv(
        "CATSCO_MASKED_IMAGE2_IMAGE_FIELD", "image[]",
    )
    host: str = os.getenv("CATSCO_INPAINT_HOST", "127.0.0.1")
    port: int = int(os.getenv("CATSCO_INPAINT_PORT", "7862"))

    @property
    def lama_config(self) -> Path:
        return self.upstream_dir / "lama" / "configs" / "prediction" / "default.yaml"

    @property
    def lama_checkpoint(self) -> Path:
        return self.upstream_dir / "pretrained_models" / "big-lama"

    @property
    def masked_image2_key_ready(self) -> bool:
        if self.masked_image2_api_key:
            return True
        return bool(
            self.masked_image2_api_key_file
            and self.masked_image2_api_key_file.is_file()
            and self.masked_image2_api_key_file.stat().st_size > 0
        )


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
