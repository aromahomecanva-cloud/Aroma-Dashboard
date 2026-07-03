"""
Client gọi Sapo Order API + Product API.
Docs: https://support.sapo.vn/gioi-thieu-order-api
Auth: Basic Auth (username = API Key, password = API Secret)
Base URL: https://{store}.mysapo.net/admin/...
"""

import random
import datetime as dt

import requests

from business_dashboard_config import Config


def _base_url() -> str:
    return f"https://{Config.SAPO_STORE}.mysapo.net/admin"


def _auth():
    return (Config.SAPO_API_KEY, Config.SAPO_API_SECRET)


def get_orders(days: int = 30) -> list[dict]:
    """
    Trả về danh sách order trong N ngày gần nhất.
    Mỗi order: {id, created_on, total_price, source_name, line_items:[{product_id, quantity}]}
    """
    if Config.DEMO_MODE:
        return _demo_orders(days)

    date_min = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    orders = []
    page = 1
    while True:
        resp = requests.get(
            f"{_base_url()}/orders.json",
            auth=_auth(),
            params={"created_on_min": date_min, "page": page, "limit": 250},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("orders", [])
        if not batch:
            break
        orders.extend(batch)
        page += 1
        if len(batch) < 250:
            break
    return orders


def get_variant_sku_map() -> dict:
    """
    Sapo API KHÔNG trả giá vốn (đã xác nhận quét 501 sản phẩm không có field này,
    kể cả combo). Nên thay vì lấy giá vốn trực tiếp, hàm này chỉ lấy mapping
    {variant_id: sku} để join với file product_costs.csv (do bạn tự duy trì).
    """
    if Config.DEMO_MODE:
        return _demo_variant_sku_map()

    mapping = {}
    page = 1
    while True:
        resp = requests.get(
            f"{_base_url()}/products.json",
            auth=_auth(),
            params={"page": page, "limit": 250},
            timeout=30,
        )
        resp.raise_for_status()
        batch = resp.json().get("products", [])
        if not batch:
            break
        for p in batch:
            for v in p.get("variants", []):
                if v.get("id") is not None:
                    mapping[v["id"]] = v.get("sku") or ""
        page += 1
        if len(batch) < 250:
            break
    return mapping


# ---------------------------------------------------------------------------
# DEMO DATA — dùng khi chưa điền API key thật, để bạn xem trước giao diện
# ---------------------------------------------------------------------------

def _demo_orders(days: int) -> list[dict]:
    random.seed(42)
    channels = Config.CHANNELS
    orders = []
    now = dt.datetime.now()
    for i in range(1, 181):
        channel = random.choice(channels)
        variant_id = random.randint(101, 105)
        total = random.randint(150_000, 950_000)
        orders.append({
            "id": i,
            "created_on": (now - dt.timedelta(days=random.randint(0, days))).isoformat(),
            "total_price": total,
            "source_name": channel,
            "line_items": [{"product_id": variant_id, "variant_id": variant_id, "quantity": random.randint(1, 3)}],
        })
    return orders


def _demo_variant_sku_map() -> dict:
    return {101: "DEMO001", 102: "DEMO002", 103: "DEMO003", 104: "DEMO004", 105: "DEMO005"}
