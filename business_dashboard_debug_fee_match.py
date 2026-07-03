"""
Script chẩn đoán: TỰ ĐỘNG tìm field nào trong order Sapo thật sự khớp với "Mã chứng từ"
(mã vận đơn) và "Tham chiếu" (SON+số, cho đơn ngoại sàn) trong file "Chi phí".

Bối cảnh: user xác nhận "Mã chứng từ" = mã vận đơn (với sàn: mã vận đơn của sàn; với đơn
ngoại sàn: mã vận đơn khi đẩy qua đơn vị vận chuyển). Với đơn ngoại sàn, "Tham chiếu" có
dạng "SON12345" — khả năng cao khớp với order["number"]/order["order_number"]/order["reference"]
sau khi bỏ tiền tố "SON". Với sàn (shopee/tiktokshop/lazada), "Tham chiếu" LẶP LẠI giữa nhiều
Mã chứng từ khác nhau (giống 1 mã batch/đối soát chung) nên KHÔNG dùng để join theo order được
— phải dùng "Mã chứng từ" (mã vận đơn) join vào field vận đơn trong order (có thể nằm trong
order["fulfillments"] hoặc order["shipping_lines"]).

Vì Claude không gọi được API Sapo trực tiếp (sandbox bị chặn mạng), script này chạy trong
GitHub Actions rồi GHI KẾT QUẢ vào data.json (mục debug_fee_match) để Claude tự đọc qua
`git clone`, thay vì phải copy log tay.

Thay vì đoán tên field, script FLATTEN toàn bộ order (đệ quy) rồi so khớp GIÁ TRỊ với các
tập "Mã chứng từ" / "Tham chiếu (dạng số sau SON)" đã biết từ file Chi phí — field nào khớp
nhiều nhất chính là field cần dùng để join.
"""

import re
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

NON_MARKETPLACE_SOURCES = {"facebook", "instagram", "zalo", "zalo-oa", "admin", "pos", "other", "web"}
MARKETPLACE_SOURCES = {"shopee", "tiktokshop", "lazada"}
EXCLUDE_SOURCES = {"Sổ quỹ"}  # chi phí vận hành chung, KHÔNG gắn với order cụ thể


def _all_expense_files():
    settlement_dir = Config.SETTLEMENT_DIR
    if not settlement_dir.exists():
        return []
    return sorted(list(settlement_dir.glob("*.xls")) + list(settlement_dir.glob("*.xlsx")))


def _read_any_excel(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, engine="xlrd")
    except Exception:
        pass
    try:
        return pd.read_excel(path)
    except Exception:
        pass
    dfs = pd.read_html(path)
    return dfs[0]


def _load_raw_expense_rows() -> pd.DataFrame:
    frames = []
    for f in _all_expense_files():
        try:
            raw = _read_any_excel(f)
        except Exception as e:
            print(f"[Cảnh báo] Bỏ qua file {f.name}: {e}")
            continue
        if "Mã chứng từ" not in raw.columns:
            continue
        frames.append(raw.dropna(subset=["Mã chứng từ"]))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _flatten(obj, path=""):
    """Sinh ra (path, value) cho mọi giá trị lá trong dict/list lồng nhau."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        if obj is not None and obj != "":
            yield path, obj


def _digit_suffix(s: str) -> str:
    m = re.search(r"(\d+)$", str(s))
    return m.group(1) if m else ""


def run_diagnostics(orders: list) -> dict:
    raw = _load_raw_expense_rows()
    if raw.empty:
        return {"error": "Không đọc được file Chi phí nào trong settlement_files/."}

    # Tách theo loại nguồn
    marketplace_rows = raw[raw["Nguồn ghi nhận"].isin(MARKETPLACE_SOURCES)]
    non_marketplace_rows = raw[raw["Nguồn ghi nhận"].isin(NON_MARKETPLACE_SOURCES)]

    waybill_values = set(marketplace_rows["Mã chứng từ"].astype(str).str.strip())

    son_values = set()
    if "Tham chiếu" in non_marketplace_rows.columns:
        for v in non_marketplace_rows["Tham chiếu"].dropna().astype(str):
            d = _digit_suffix(v)
            if d:
                son_values.add(d)
                son_values.add(str(int(d)))  # bỏ số 0 đứng đầu nếu có

    print(f"Số mã vận đơn (marketplace) cần khớp: {len(waybill_values)}")
    print(f"Số mã SON (ngoại sàn, đã tách số) cần khớp: {len(son_values)}")

    waybill_field_counts = {}
    son_field_counts = {}

    for o in orders:
        for path, val in _flatten(o):
            sval = str(val).strip()
            if sval in waybill_values:
                waybill_field_counts[path] = waybill_field_counts.get(path, 0) + 1
            if sval in son_values:
                son_field_counts[path] = son_field_counts.get(path, 0) + 1
            # thử cả dạng số nguyên (bỏ .0 nếu là float, bỏ số 0 đầu)
            try:
                ival = str(int(float(val)))
                if ival in son_values:
                    son_field_counts[path] = son_field_counts.get(path, 0) + 1
            except (ValueError, TypeError):
                pass

    top_waybill = sorted(waybill_field_counts.items(), key=lambda kv: -kv[1])[:20]
    top_son = sorted(son_field_counts.items(), key=lambda kv: -kv[1])[:20]

    return {
        "total_orders_scanned": len(orders),
        "waybill_values_count": len(waybill_values),
        "son_values_count": len(son_values),
        "top_matching_fields_for_waybill_mã_chứng_từ": top_waybill,
        "top_matching_fields_for_SON_tham_chiếu": top_son,
        "sample_waybill_values": sorted(waybill_values)[:10],
        "sample_son_values": sorted(son_values)[:10],
    }


if __name__ == "__main__":
    from business_dashboard_sapo import get_orders
    orders = get_orders(days=None)
    result = run_diagnostics(orders)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
