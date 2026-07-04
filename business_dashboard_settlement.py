"""
Parser đọc file "Báo cáo chi phí bán hàng" export TRỰC TIẾP từ Sapo (mục "Chi phí"
trong Sapo Admin — KHÔNG PHẢI file đối soát export từ Shopee/TikTok Seller Center).
Sapo tự động tổng hợp các khoản: Phí cố định, Phí dịch vụ, Phí thanh toán,
Thuế sàn thực tế, Phí tiếp thị liên kết (aff), Các phí khác, Hoàn thuế do phát sinh
trả hàng... theo từng ĐƠN HÀNG.

ĐÃ XÁC NHẬN join key qua business_dashboard_debug_fee_match.py (chạy trên dữ liệu thật,
93.1% khớp với nhánh "name", cộng thêm nhánh "order_number" dưới đây để phủ luôn nhóm
ngoại sàn còn thiếu):
  - Đơn SÀN (shopee/tiktokshop/lazada): cột "Mã chứng từ" == order["name"]
    (field "source_identifier" cũng khớp y hệt, dùng "name" cho gọn).
  - Đơn NGOẠI SÀN (facebook/instagram/zalo/zalo-oa/admin/pos/other/web): cột
    "Tham chiếu" có dạng "SON12345" -> phần số (12345) == order["order_number"].
  - "Sổ quỹ": KHÔNG phải chi phí gắn với order (chi phí vận hành chung: nhân công,
    quản lý, viễn thông...) -> loại hẳn khỏi việc join theo order.

Cách dùng:
1. Vào Sapo -> mục "Chi phí" -> Xuất file báo cáo chi phí bán hàng, chọn "Tất cả nguồn"
   (.xls/.xlsx)
2. Bỏ vào thư mục "settlement_files/" (tự tạo cạnh các file .py này). Nếu xuất nhiều lần
   / nhiều khoảng ngày, có thể bỏ nhiều file vào cùng thư mục — tool tự gộp và khử trùng
   lặp ở mức dòng (Ngày ghi nhận + Mã chứng từ + Tên chi phí + Giá trị ghi nhận).
3. Chạy lại chương trình.
"""

import re
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

REQUIRED_COLS = {"Mã chứng từ", "Giá trị ghi nhận"}

MARKETPLACE_SOURCES = {"shopee", "tiktokshop", "lazada"}
NON_MARKETPLACE_SOURCES = {"facebook", "instagram", "zalo", "zalo-oa", "admin", "pos", "other", "web"}
# "Sổ quỹ" và các nguồn khác không nằm trong 2 tập trên -> không join theo order.


def _all_expense_files() -> list[Path]:
    settlement_dir = Config.SETTLEMENT_DIR
    if not settlement_dir.exists():
        settlement_dir.mkdir(parents=True, exist_ok=True)
        return []
    return sorted(list(settlement_dir.glob("*.xls")) + list(settlement_dir.glob("*.xlsx")))


def _read_any_excel(path: Path) -> pd.DataFrame:
    """
    File Sapo export .xls đôi khi là binary Excel thật (cần engine xlrd), đôi khi
    thực chất là bảng HTML đội lốt .xls (cần html5lib) -> thử lần lượt các cách đọc.
    """
    try:
        return pd.read_excel(path, engine="xlrd")
    except Exception:
        pass
    try:
        return pd.read_excel(path)
    except Exception:
        pass
    try:
        dfs = pd.read_html(path)
        if dfs:
            return dfs[0]
    except Exception:
        pass
    raise RuntimeError(f"Không đọc được file {path.name} bằng bất kỳ cách nào (xlrd/openpyxl/html).")


def _digit_suffix(s) -> str:
    m = re.search(r"(\d+)$", str(s))
    return m.group(1) if m else ""


def _load_combined_expense_rows() -> pd.DataFrame:
    """
    Đọc + gộp + khử trùng lặp TẤT CẢ file Chi phí, gắn sẵn "_join_key"/"_join_field" cho
    từng DÒNG (mỗi dòng = 1 loại phí của 1 đơn hàng), loại "Sổ quỹ"/nguồn lạ. Đây là dữ liệu
    gốc dùng chung cho cả load_settlement_fees() (tổng theo order) và
    load_settlement_fee_breakdown() (chi tiết theo TỪNG LOẠI PHÍ - cột "Tên chi phí").
    """
    files = _all_expense_files()
    if not files:
        return pd.DataFrame()

    raw_frames = []
    for f in files:
        try:
            raw = _read_any_excel(f)
        except Exception as e:
            print(f"[Cảnh báo] Bỏ qua file {f.name}: {e}")
            continue

        if not REQUIRED_COLS.issubset(set(raw.columns)):
            print(f"[Cảnh báo] File {f.name} thiếu cột cần thiết {REQUIRED_COLS} "
                  f"(cột hiện có: {list(raw.columns)}), bỏ qua.")
            continue

        total_rows = len(raw)
        df = raw.dropna(subset=["Mã chứng từ"]).copy()
        dropped = total_rows - len(df)
        print(f"[Chi phí] File {f.name}: {total_rows} dòng, giữ lại {len(df)} dòng có Mã chứng từ (bỏ {dropped} dòng).")
        if "Nguồn ghi nhận" in df.columns:
            print(f"  Nguồn ghi nhận trong file này: {df['Nguồn ghi nhận'].value_counts().to_dict()}")

        df["Mã chứng từ"] = df["Mã chứng từ"].astype(str).str.strip()
        df["Giá trị ghi nhận"] = pd.to_numeric(df["Giá trị ghi nhận"], errors="coerce").fillna(0)
        raw_frames.append(df)

    if not raw_frames:
        return pd.DataFrame()

    all_rows = pd.concat(raw_frames, ignore_index=True)

    # Nhiều file export có thể CHỒNG LẤN khoảng ngày -> khử trùng ở mức DÒNG.
    dedup_cols = [c for c in ["Ngày ghi nhận", "Mã chứng từ", "Tên chi phí", "Giá trị ghi nhận"] if c in all_rows.columns]
    before = len(all_rows)
    all_rows = all_rows.drop_duplicates(subset=dedup_cols)
    if before != len(all_rows):
        print(f"[Chi phí] Đã bỏ {before - len(all_rows)} dòng trùng lặp giữa các file export chồng lấn.")

    source_col = all_rows["Nguồn ghi nhận"] if "Nguồn ghi nhận" in all_rows.columns else pd.Series("", index=all_rows.index)

    marketplace_mask = source_col.isin(MARKETPLACE_SOURCES)
    non_marketplace_mask = source_col.isin(NON_MARKETPLACE_SOURCES)
    excluded_count = len(all_rows) - marketplace_mask.sum() - non_marketplace_mask.sum()
    if excluded_count:
        print(f"[Chi phí] Bỏ qua {excluded_count} dòng không thuộc nguồn nào đã biết "
              f"(VD: 'Sổ quỹ' — chi phí vận hành chung, không gắn với 1 order cụ thể).")

    if "Tên chi phí" not in all_rows.columns:
        all_rows["Tên chi phí"] = "Khác"
    all_rows["Tên chi phí"] = all_rows["Tên chi phí"].fillna("Khác").astype(str).str.strip()

    frames = []

    # Nhóm SÀN: join_key = Mã chứng từ -> so khớp order["name"]
    mp = all_rows[marketplace_mask].copy()
    if not mp.empty:
        mp["_join_key"] = mp["Mã chứng từ"]
        mp["_join_field"] = "name"
        frames.append(mp)

    # Nhóm NGOẠI SÀN: join_key = phần số trong "Tham chiếu" (VD: "SON12345" -> "12345")
    # -> so khớp str(order["order_number"])
    nm = all_rows[non_marketplace_mask].copy()
    if not nm.empty and "Tham chiếu" in nm.columns:
        nm["_ref_digits"] = nm["Tham chiếu"].apply(_digit_suffix)
        nm = nm[nm["_ref_digits"] != ""]
        if not nm.empty:
            nm["_join_key"] = nm["_ref_digits"]
            nm["_join_field"] = "order_number"
            frames.append(nm)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def load_settlement_fees() -> pd.DataFrame:
    """
    Trả về DataFrame: join_key, join_field ("name" hoặc "order_number"), channel, total_fee.
    - join_field="name": join_key so khớp với order["name"] (đơn sàn).
    - join_field="order_number": join_key so khớp với str(order["order_number"]) (đơn ngoại sàn).
    """
    if Config.DEMO_MODE:
        return _demo_settlement()

    combined = _load_combined_expense_rows()
    if combined.empty:
        return pd.DataFrame(columns=["join_key", "join_field", "channel", "total_fee"])

    channel_col = "Nguồn ghi nhận" if "Nguồn ghi nhận" in combined.columns else None
    agg_kwargs = {"total_fee": ("Giá trị ghi nhận", "sum")}
    if channel_col:
        agg_kwargs["channel"] = (channel_col, "first")
    g = combined.groupby(["_join_key", "_join_field"]).agg(**agg_kwargs).reset_index()
    if channel_col is None:
        g["channel"] = ""
    g = g.rename(columns={"_join_key": "join_key", "_join_field": "join_field"})
    return g[["join_key", "join_field", "channel", "total_fee"]]


def load_settlement_fee_breakdown() -> pd.DataFrame:
    """
    Trả về DataFrame CHI TIẾT theo TỪNG LOẠI PHÍ: join_key, join_field, fee_name, amount.
    Dùng để hiển thị "list chi tiết từng phần phí" (VD: Phí cố định, Phí dịch vụ, Phí thanh
    toán, Thuế sàn thực tế, Phí tiếp thị liên kết (aff), Phí vận chuyển thực tế, ...) thay vì
    chỉ 1 con số tổng total_fee.
    """
    if Config.DEMO_MODE:
        return pd.DataFrame(columns=["join_key", "join_field", "fee_name", "amount"])

    combined = _load_combined_expense_rows()
    if combined.empty:
        return pd.DataFrame(columns=["join_key", "join_field", "fee_name", "amount"])

    g = combined.groupby(["_join_key", "_join_field", "Tên chi phí"])["Giá trị ghi nhận"].sum().reset_index()
    g.columns = ["join_key", "join_field", "fee_name", "amount"]
    return g


# ---------------------------------------------------------------------------
# DEMO DATA
# ---------------------------------------------------------------------------

def _demo_settlement() -> pd.DataFrame:
    import random
    random.seed(99)
    rows = []
    for i in range(1, 181):
        channel = Config.CHANNELS[i % 2]
        fee = random.randint(10_000, 90_000)
        rows.append({"join_key": f"DEMO-{i}", "join_field": "name", "channel": channel.lower(), "total_fee": fee})
    return pd.DataFrame(rows)
