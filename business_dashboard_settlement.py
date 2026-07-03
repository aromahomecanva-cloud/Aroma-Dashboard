"""
Parser đọc file "Báo cáo chi phí bán hàng" export TRỰC TIẾP từ Sapo (mục "Chi phí"
trong Sapo Admin — KHÔNG PHẢI file đối soát export từ Shopee/TikTok Seller Center).
Sapo tự động tổng hợp các khoản: Phí cố định, Phí dịch vụ, Phí thanh toán,
Thuế sàn thực tế, Phí tiếp thị liên kết (aff), Các phí khác, Hoàn thuế do phát sinh
trả hàng... theo từng ĐƠN HÀNG.

QUAN TRỌNG về join key: cột "Mã chứng từ" trong file này có CÙNG ĐỊNH DẠNG với field
"name" của order trong Sapo Order API (VD: "260630EKV6WRY0", "260704Q53C8HS1") —
KHÔNG PHẢI order["id"] (số). Nên total_fee được join vào order qua order["name"],
không phải order["id"]. Nếu tỷ lệ khớp thấp (xem log "match rate" khi chạy
export_json.py), có thể giả thuyết join-key này cần điều chỉnh lại.

Cách dùng:
1. Vào Sapo -> mục "Chi phí" -> Xuất file báo cáo chi phí bán hàng (.xls/.xlsx)
2. Bỏ vào thư mục "settlement_files/" (tự tạo cạnh các file .py này). Có thể xuất
   nhiều lần / nhiều khoảng ngày khác nhau rồi bỏ TẤT CẢ vào cùng thư mục — tool tự
   gộp hết, không bị trùng lặp (cộng dồn theo order_name nếu có xuất hiện ở nhiều file).
3. Chạy lại chương trình — tool tự cộng "Giá trị ghi nhận" theo "Mã chứng từ" = total_fee.

Về đơn "ngoại sàn" (Facebook/Zalo/POS/Website...): các đơn này thường CHỈ có 1 khoản
"Phí vận chuyển thực tế" thay vì đủ bộ phí như Shopee (Phí cố định/dịch vụ/thanh toán/
thuế sàn/aff). Code này KHÔNG cần chỉnh gì thêm cho việc đó — vì total_fee được tính
bằng cách CỘNG TẤT CẢ dòng "Giá trị ghi nhận" theo từng "Mã chứng từ", bất kể "Tên chi
phí" hay "Nguồn ghi nhận" là gì. Nghĩa là 1 đơn Shopee có 4-5 dòng phí sẽ được cộng
đủ 4-5 dòng, còn 1 đơn ngoại sàn chỉ có 1 dòng "Phí vận chuyển thực tế" thì total_fee
của đơn đó = đúng giá trị dòng đó. Không cần phân biệt loại phí hay nguồn khi tính tổng.
"""

import random
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

REQUIRED_COLS = {"Mã chứng từ", "Giá trị ghi nhận"}


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


def load_settlement_fees() -> pd.DataFrame:
    """
    Trả về DataFrame: order_name, channel, total_fee
    order_name dùng để join với order["name"] (Sapo Order API) — xem docstring module.
    """
    if Config.DEMO_MODE:
        return _demo_settlement()

    files = _all_expense_files()
    if not files:
        return pd.DataFrame(columns=["order_name", "channel", "total_fee"])

    frames = []
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
        print(f"[Chi phí] File {f.name}: {total_rows} dòng, giữ lại {len(df)} dòng có Mã chứng từ "
              f"(bỏ {dropped} dòng — thường là 1 dòng 'Tổng' ở cuối; nếu > 1 dòng bị bỏ, kiểm tra "
              f"lại xem có đơn nào (đặc biệt đơn ngoại sàn) thiếu Mã chứng từ không).")
        if "Nguồn ghi nhận" in df.columns:
            print(f"  Nguồn ghi nhận trong file này: {df['Nguồn ghi nhận'].value_counts().to_dict()}")
        if "Tên chi phí" in df.columns:
            print(f"  Loại phí trong file này: {df['Tên chi phí'].value_counts().to_dict()}")

        df["Mã chứng từ"] = df["Mã chứng từ"].astype(str).str.strip()
        df["Giá trị ghi nhận"] = pd.to_numeric(df["Giá trị ghi nhận"], errors="coerce").fillna(0)

        if "Nguồn ghi nhận" in df.columns:
            channel_series = df.groupby("Mã chứng từ")["Nguồn ghi nhận"].first()
        else:
            channel_series = pd.Series("", index=df["Mã chứng từ"].unique())

        fee_series = df.groupby("Mã chứng từ")["Giá trị ghi nhận"].sum()
        grouped = pd.DataFrame({
            "order_name": fee_series.index,
            "total_fee": fee_series.values,
        })
        grouped["channel"] = grouped["order_name"].map(channel_series).fillna("")
        frames.append(grouped)

    if not frames:
        return pd.DataFrame(columns=["order_name", "channel", "total_fee"])

    result = pd.concat(frames, ignore_index=True)
    # Nếu 1 order_name xuất hiện ở nhiều file (export chồng lấn khoảng ngày) -> cộng dồn.
    result = result.groupby(["order_name", "channel"], as_index=False)["total_fee"].sum()
    return result


# ---------------------------------------------------------------------------
# DEMO DATA
# ---------------------------------------------------------------------------

def _demo_settlement() -> pd.DataFrame:
    random.seed(99)
    rows = []
    for i in range(1, 181):
        channel = Config.CHANNELS[i % 2]
        fee = random.randint(10_000, 90_000)
        rows.append({"order_name": f"DEMO-{i}", "channel": channel.lower(), "total_fee": fee})
    return pd.DataFrame(rows)
