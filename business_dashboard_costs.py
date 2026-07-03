"""
Đọc giá vốn theo SKU từ 2 file EXPORT của Sapo (Admin -> Sản phẩm -> Xuất file):

1. "products_export*.xlsx" — export SẢN PHẨM THƯỜNG, có cột "Mã SKU" + "Giá vốn"
   (giá vốn combo trong file này luôn = 0, Sapo không tự tính).

2. "combos_export*.xlsx" — export CHI TIẾT COMBO (bill of materials), có cột:
   "Đường dẫn/Alias" (nhóm theo combo), "Mã SKU" (SKU của combo, chỉ có ở 1 dòng/nhóm),
   "SKU tham chiếu" (SKU thành phần), "Số lượng*" (số lượng thành phần trong 1 combo).
   Giá vốn combo = tổng (giá vốn thành phần * số lượng) lấy từ file products_export.

Cả 2 file đặt trong thư mục "product_exports/" (tự tạo, cùng chỗ các file .py).
Mỗi lần giá vốn thay đổi, export lại 2 file này từ Sapo và ghi đè (giữ nguyên tên có
chứa "products_export" / "combos_export").

Ngoài ra có thể tạo file "product_costs_override.csv" (cột: sku, gia_von) để ghi đè
thủ công bất kỳ SKU nào (regular hoặc combo) — ưu tiên cao nhất.
"""

import re
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR / "product_exports"
OVERRIDE_FILE = BASE_DIR / "product_costs_override.csv"


def _parse_cost(val) -> float:
    if pd.isna(val):
        return 0.0
    s = str(val)
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def _all_files(pattern: str):
    """Sapo có thể tách file export thành nhiều phần (giới hạn số dòng/file),
    nên đọc TẤT CẢ file khớp pattern, không chỉ file mới nhất."""
    if not EXPORT_DIR.exists():
        return []
    return sorted(EXPORT_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)


def _load_regular_costs() -> dict:
    """Đọc TẤT CẢ file products_export*.xlsx -> {sku: giá_vốn} (gộp nếu Sapo tách nhiều file)."""
    costs = {}
    for path in _all_files("*products_export*.xlsx"):
        df = pd.read_excel(path)
        if "Mã SKU" in df.columns and "Giá vốn" in df.columns:
            for _, row in df.iterrows():
                sku = str(row.get("Mã SKU") or "").strip()
                if not sku or sku == "nan":
                    continue
                costs[sku] = _parse_cost(row.get("Giá vốn"))
    return costs


def _load_combo_bom() -> dict:
    """Đọc TẤT CẢ file combos_export*.xlsx -> {combo_sku: [(component_sku, qty), ...]}."""
    bom = {}
    for path in _all_files("*combos_export*.xlsx"):
        df = pd.read_excel(path)
        required = {"Đường dẫn/Alias", "Mã SKU", "SKU tham chiếu", "Số lượng*"}
        if not required.issubset(df.columns):
            continue

        df["_combo_sku"] = df.groupby("Đường dẫn/Alias")["Mã SKU"].transform(lambda s: s.ffill().bfill())

        for combo_sku, grp in df.groupby("_combo_sku"):
            combo_sku = str(combo_sku or "").strip()
            if not combo_sku or combo_sku == "nan":
                continue
            items = []
            for _, row in grp.iterrows():
                comp_sku = str(row.get("SKU tham chiếu") or "").strip()
                qty = row.get("Số lượng*") or 0
                if comp_sku and comp_sku != "nan":
                    items.append((comp_sku, float(qty)))
            bom[combo_sku] = items
    return bom


def load_cost_map() -> dict:
    """Trả về {sku: giá_vốn (float)} — gộp sản phẩm thường + combo (tự tính từ BOM)."""
    if Config.DEMO_MODE:
        return {"DEMO001": 120_000, "DEMO002": 95_000, "DEMO003": 210_000, "DEMO004": 60_000, "DEMO005": 175_000}

    costs = _load_regular_costs()

    combo_bom = _load_combo_bom()
    for combo_sku, items in combo_bom.items():
        costs[combo_sku] = sum(costs.get(comp_sku, 0.0) * qty for comp_sku, qty in items)

    # Override thủ công - ưu tiên cao nhất
    if OVERRIDE_FILE.exists():
        odf = pd.read_csv(OVERRIDE_FILE, encoding="utf-8-sig")
        for _, row in odf.iterrows():
            sku = str(row.get("sku") or "").strip()
            if sku:
                costs[sku] = float(row.get("gia_von") or 0)

    return costs
