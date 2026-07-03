"""
Cấu hình chung cho Business Dashboard.

Cách dùng:
1. Copy file "env.example.txt" thành ".env" trong cùng thư mục, điền thông tin thật.
   HOẶC đơn giản hơn: sửa trực tiếp các giá trị mặc định bên dưới (biến trong class Config).
2. Nếu để trống hết -> DEMO_MODE tự bật -> chương trình dùng dữ liệu mẫu để bạn xem thử giao diện.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"


def _load_env_file(path: Path) -> dict:
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


_env_from_file = _load_env_file(ENV_FILE)


def _get(key: str, default: str = "") -> str:
    # Ưu tiên biến môi trường hệ thống, sau đó tới file .env, cuối cùng là default
    return os.environ.get(key) or _env_from_file.get(key) or default


class Config:
    # --- Sapo ---
    SAPO_STORE = _get("SAPO_STORE")
    SAPO_API_KEY = _get("SAPO_API_KEY")
    SAPO_API_SECRET = _get("SAPO_API_SECRET")

    # --- Meta Marketing API ---
    META_ACCESS_TOKEN = _get("META_ACCESS_TOKEN")
    META_AD_ACCOUNT_ID = _get("META_AD_ACCOUNT_ID")
    META_API_VERSION = "v21.0"

    # --- Chế độ chạy ---
    # Demo mode bật tự động nếu thiếu bất kỳ thông tin bắt buộc nào
    DEMO_MODE = not all([SAPO_STORE, SAPO_API_KEY, SAPO_API_SECRET, META_ACCESS_TOKEN, META_AD_ACCOUNT_ID])

    # Danh sách kênh bán hàng hiện có (để mapping nguồn đơn hàng trong Sapo)
    CHANNELS = ["Shopee", "TikTok Shop"]

    # Thư mục chứa file đối soát (settlement) export từ Shopee / TikTok Shop
    SETTLEMENT_DIR = BASE_DIR / "settlement_files"

    # File output
    OUTPUT_DASHBOARD_HTML = BASE_DIR / "dashboard.html"


if __name__ == "__main__":
    print("DEMO_MODE:", Config.DEMO_MODE)
    print("SAPO_STORE:", Config.SAPO_STORE or "(chưa điền)")
    print("META_AD_ACCOUNT_ID:", Config.META_AD_ACCOUNT_ID or "(chưa điền)")
