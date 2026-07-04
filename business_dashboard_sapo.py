"""
Client gọi Sapo Order API + Product API.
Docs: https://support.sapo.vn/gioi-thieu-order-api
Auth: Basic Auth (username = API Key, password = API Secret)
Base URL: https://{store}.mysapo.net/admin/...
"""

import gzip
import json
import random
import datetime as dt
from pathlib import Path

import requests

from business_dashboard_config import Config


def _write_json_gz(path: Path, data: dict) -> None:
    """Ghi JSON đã NÉN GZIP — cache_sapo_orders.json bản KHÔNG nén đã lên tới ~193MB (vượt
    giới hạn 100MB/file của GitHub, bị từ chối push với lỗi 'GH001: Large files detected').
    JSON đơn hàng có rất nhiều field/giá trị lặp lại nên nén rất tốt (thường giảm 5-10 lần),
    và vẫn giữ NGUYÊN VẸN từng order (không cắt bớt field nào) — vì business_dashboard_debug
    _fee_match.py cần FLATTEN toàn bộ order để tự dò field join, cắt field sẽ làm hỏng chẩn
    đoán đó."""
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    with gzip.open(path, "wb", compresslevel=6) as f:
        f.write(raw)


def _read_json_gz(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with gzip.open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, gzip.BadGzipFile, json.JSONDecodeError):
        return {}


def _base_url() -> str:
    return f"https://{Config.SAPO_STORE}.mysapo.net/admin"


def _auth():
    return (Config.SAPO_API_KEY, Config.SAPO_API_SECRET)


def get_orders(days: int | None = None) -> list[dict]:
    """
    Trả về danh sách order. Nếu days=None -> lấy TOÀN BỘ lịch sử đơn hàng (không giới hạn ngày).
    Nếu days=N -> chỉ lấy N ngày gần nhất.
    Mỗi order: {id, created_on, total_price, source_name, line_items:[{product_id, quantity}]}
    """
    if Config.DEMO_MODE:
        return _demo_orders(days or 90)

    params_base = {"page": 1, "limit": 250}
    if days is not None:
        params_base["created_on_min"] = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y-%m-%d")

    orders = []
    page = 1
    while True:
        params = dict(params_base)
        params["page"] = page
        resp = requests.get(f"{_base_url()}/orders.json", auth=_auth(), params=params, timeout=30)
        resp.raise_for_status()
        batch = resp.json().get("orders", [])
        if not batch:
            break
        orders.extend(batch)
        page += 1
        if len(batch) < 250:
            break
    return orders


def get_orders_cached(cache_path: Path | str, incremental_days: int = 60) -> list[dict]:
    """
    Bản CÓ CACHE của get_orders() — tránh phải kéo lại TOÀN BỘ lịch sử đơn hàng mỗi lần
    workflow chạy (mỗi 3 tiếng), vì phần lớn đơn CŨ không hề thay đổi. Theo đề xuất của bạn:
    lần đầu kéo TOÀN BỘ và lưu lại (cache), các lần sau chỉ kéo `incremental_days` ngày gần
    nhất (mặc định 60 — đủ rộng để bắt các đơn được sửa/hoàn tiền muộn) rồi GHI ĐÈ (upsert
    theo order id) lên cache — đơn cũ hơn incremental_days ngày giữ nguyên, không bị đụng tới.

    Cache lưu ở file JSON NÉN GZIP `cache_path` (nên đặt tên đuôi .json.gz), dạng
    {"orders_by_id": {id: order, ...}, "last_updated_at": ...}. File này cần được COMMIT lại
    vào repo (giống data.json) để lần chạy SAU đọc lại được — xem workflow YAML (đã thêm
    cache_*.json.gz vào bước "git add"). Dùng gzip vì bản KHÔNG nén của cửa hàng này đã lên
    tới ~193MB, vượt giới hạn 100MB/file cứng của GitHub (bị từ chối push).

    Muốn ép tải lại TOÀN BỘ (VD nghi ngờ cache lệch, hoặc mới đổi công thức tính) -> XÓA file
    cache_path rồi chạy lại; hàm sẽ tự nhận ra cache rỗng và tự làm full pull như lần đầu.
    """
    if Config.DEMO_MODE:
        return get_orders(days=None)

    cache_path = Path(cache_path)
    cache = _read_json_gz(cache_path)
    orders_by_id = cache.get("orders_by_id", {})

    if not orders_by_id:
        print("[Sapo cache] Không có cache (hoặc cache rỗng) -> kéo TOÀN BỘ lịch sử đơn hàng.")
        fresh = get_orders(days=None)
    else:
        print(f"[Sapo cache] Đã có {len(orders_by_id)} đơn trong cache -> chỉ kéo "
              f"{incremental_days} ngày gần nhất để cập nhật (đơn cũ hơn giữ nguyên).")
        fresh = get_orders(days=incremental_days)

    for o in fresh:
        oid = o.get("id")
        if oid is None:
            continue
        orders_by_id[str(oid)] = o

    cache["orders_by_id"] = orders_by_id
    cache["last_updated_at"] = dt.datetime.now().isoformat()
    _write_json_gz(cache_path, cache)

    print(f"[Sapo cache] Lần này lấy mới/cập nhật {len(fresh)} đơn -> tổng cộng "
          f"{len(orders_by_id)} đơn trong cache.")
    return list(orders_by_id.values())


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
        qty = random.randint(1, 3)
        orders.append({
            "id": i,
            "created_on": (now - dt.timedelta(days=random.randint(0, days))).isoformat(),
            "total_price": total,
            "source_name": channel,
            "status": "closed",
            "cancelled_on": None,
            # Các field dưới đây cần cho business_dashboard_revenue.py tính "Doanh thu thuần"
            # đúng công thức Sapo thật (total_line_items_price - total_discounts - refund_value).
            "total_line_items_price": total,
            "total_discounts": 0,
            "shipping_lines": [],
            "refunds": [],
            "line_items": [{
                "product_id": variant_id, "variant_id": variant_id, "quantity": qty,
                "price": round(total / qty, 0), "title": f"Sản phẩm demo {variant_id}",
            }],
        })
    return orders


def _demo_variant_sku_map() -> dict:
    return {101: "DEMO001", 102: "DEMO002", 103: "DEMO003", 104: "DEMO004", 105: "DEMO005"}
