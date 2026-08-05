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
   (.xls/.xlsx) — TÊN FILE MẶC ĐỊNH Sapo đặt khi tải về có dạng
   "xuat_file_bao_cao_chi_phi_ban_hang_DD-MM-YYYY_HH-MM.xls" (DD-MM-YYYY_HH-MM = thời điểm
   XUẤT file, KHÔNG phải khoảng ngày dữ liệu bên trong) — GIỮ NGUYÊN tên này khi bỏ vào repo,
   xem _file_export_timestamp() bên dưới để biết vì sao quan trọng.
2. Bỏ vào thư mục "settlement_files/" (tự tạo cạnh các file .py này). Theo quy trình của Huy:
   xuất định kỳ, nối tiếp nhau (VD 1/6-30/6, rồi 1/7-31/7...), KHÔNG xoá file cũ — cứ bỏ thêm
   file mới vào cùng thư mục.
3. Chạy lại chương trình.

QUY TẮC LOẠI TRỪ MỘT SỐ DÒNG PHÍ (shopee/tiktokshop, xác nhận 04/08/2026) — xem
_EXCLUDED_FEE_NAMES_SHOPEE_TIKTOK trong _load_combined_expense_rows(): giảm giá/voucher do
sàn hoặc do shop tài trợ, và phí vận chuyển (thực tế/người mua trả/Shopee trợ giá) KHÔNG được
tính vào "Tổng phí" — không phải chi phí thật của seller hoặc đã bị cấn trừ vào doanh thu rồi.

XỬ LÝ NHIỀU FILE CHỒNG LẤN — 2 tầng, xem _load_combined_expense_rows():
  a) Dòng TRÙNG Y HỆT (cùng Ngày ghi nhận + Mã chứng từ + Tên chi phí + Giá trị ghi nhận) giữa
     2 file export chồng khoảng ngày -> khử trùng lặp bình thường (đây là re-export cùng 1 dòng
     sổ cái, không phải 2 sự kiện khác nhau).
  b) CÙNG 1 Mã chứng từ (đơn hàng) xuất hiện trong NHIỀU file KHÁC NHAU nhưng dữ liệu không
     khớp y hệt (VD đơn tạo cuối tháng 6, được Sapo đưa vào cả export tháng 6 lẫn export tháng 7
     với giá trị/số dòng phí khác nhau do lúc export tháng 6 đơn CHƯA hoàn thành hết) -> theo
     yêu cầu của Huy: ưu tiên TOÀN BỘ dữ liệu của đơn đó từ file có thời điểm XUẤT MỚI NHẤT
     (parse từ tên file, xem _file_export_timestamp), bỏ hẳn dữ liệu đơn đó từ (các) file cũ hơn
     — KHÔNG cộng dồn 2 nguồn cho cùng 1 đơn (tránh đếm trùng phí khi đơn đó "known-later-state"
     đã có ở file mới).
"""

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

REQUIRED_COLS = {"Mã chứng từ", "Giá trị ghi nhận"}

MARKETPLACE_SOURCES = {"shopee", "tiktokshop", "lazada"}
NON_MARKETPLACE_SOURCES = {"facebook", "instagram", "zalo", "zalo-oa", "admin", "pos", "other", "web"}
# "Sổ quỹ" và các nguồn khác không nằm trong 2 tập trên -> không join theo order.

# Phát hiện 04/08/2026: trong file export "Tất cả nguồn" (04-08-2026_16-53), cột "Tên chi phí"
# có 2 "phong cách" đặt tên khác nhau CHO CÙNG 1 loại phí, tuỳ theo đơn hàng rơi vào khoảng
# thời gian nào (Sapo tự chuyển đổi format tên cột phía họ, KHÔNG phải Huy hay Claude sửa gì):
#   - Đa số đơn (VN, ~6.7k đơn, 09/03 -> 31/07): tên tiếng Việt do Sapo dịch, VD "Phí cố định".
#   - 1 dải đơn hẹp (EN, ~1.78k đơn, 21/06 -> 26/07, gối 1 phần vào dải VN): tên RAW field
#     tiếng Anh/snake_case y hệt Shopee trả về, VD "commission_fee" — đây là 1 giai đoạn
#     chuyển tiếp ngắn (gradual cutover thấy rõ qua số dòng/ngày: VN giảm dần 21/06->01/07,
#     EN tăng dần, rồi EN giảm dần 22/07->26/07 quay lại VN) — KHÔNG liên quan tới shop
#     (Góc Hương Thơm / The Art Of Scent đều có cả 2 kiểu tên, tỷ lệ tương đương) và KHÔNG
#     phải 2 loại phí khác nhau bị cộng trùng cho cùng 1 đơn (đã kiểm chứng: 0% đơn hàng nào
#     có cả 2 tên cho cùng 1 loại phí -> Tổng phí (total_fee) KHÔNG bị sai/trùng, chỉ có phần
#     hiển thị "chi tiết theo loại phí" (load_settlement_fee_breakdown) bị xé lẻ thành 2 dòng
#     tưởng như trùng lặp).
# -> chuẩn hoá về 1 tên tiếng Việt duy nhất cho các cặp đã xác định rõ ràng (dựa trên tên
# field + so sánh độ lớn/số đơn của 2 phía) để phần "chi tiết theo loại phí" gộp đúng nhóm.
# Cặp nào KHÔNG rõ ràng thì giữ nguyên tên gốc (không đoán bừa).
FEE_NAME_NORMALIZE = {
    # Phí lớn nhất, phát sinh ở hầu hết mọi đơn — khớp theo cùng độ phổ biến (~6.4-6.5k đơn VN
    # <-> ~1.75k đơn EN, đều là phí bắt buộc/áp dụng gần như mọi đơn):
    "commission_fee": "Phí cố định",
    "seller_transaction_fee": "Phí thanh toán",
    # Tên field trùng nghĩa trực tiếp:
    "service_fee": "Phí dịch vụ",
    "actual_shipping_fee": "Phí vận chuyển thực tế",
    "actual_shipping_fee_amount": "Phí vận chuyển thực tế",
    # Voucher/giảm giá:
    "voucher_from_shopee": "Giàm giá Shopee",
    "shopee_discount": "Giàm giá Shopee",
    "voucher_from_seller": "Mã ưu đãi do Người Bán chịu",
    "seller_discount_amount": "Khuyến mãi của người bán",
    # Vận chuyển (trợ giá/người mua trả/chiết khấu) — cùng dấu âm (khoản được trừ ngược):
    "shopee_shipping_rebate": "Phí vận chuyển được trợ giá từ Shopee",
    "buyer_paid_shipping_fee": "Phí vận chuyển do người mua trả",
    "customer_paid_shipping_fee_amount": "Phí vận chuyển do người mua trả",
    "shipping_fee_discount_amount": "Chiết khấu phí vận chuyển nền tảng",
    "reverse_shipping_fee": "Phí vận chuyển trả hàng (đơn Trả hàng/hoàn tiền)",
    "final_return_to_seller_shipping_fee": "Phí vận chuyển trả hàng do người bán trả",
    # Thuế:
    "withholding_pit_tax": "Thuế TNCN",
    "pit_amount": "Thuế TNCN",
    "withholding_tax_pit": "Thuế TNCN",
    "withholding_vat_tax": "Thuế GTGT",
    "vat_amount": "Thuế GTGT",
    # Hoa hồng / tiếp thị liên kết:
    "commission": "Phí hoa hồng",
    "platform_commission_amount": "Phí hoa hồng nền tảng",
    "affiliate_commission_amount": "Phí hoa hồng liên kết",
    "affiliate_ads_commission_amount": "Phí hoa hồng quảng cáo",
    "order_ams_commission_fee": "Phí tiếp thị liên kết",
    # Khác:
    "vn_fix_infrastructure_fee": "Phí hạ tầng cố định",
    "coins": "Shopee Xu",
    "other_fee": "Phí khác",
    "transaction_fee_amount": "Phí thanh toán",
    "payment_fee": "Phí thanh toán",
}

# Tên file mặc định Sapo đặt: "xuat_file_bao_cao_chi_phi_ban_hang_04-08-2026_16-53.xls"
# -> nhóm (\d{2})-(\d{2})-(\d{4})_(\d{2})-(\d{2}) = ngày-tháng-năm_giờ-phút XUẤT file.
_FILENAME_TS_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})_(\d{2})-(\d{2})")


def _file_export_timestamp(path: Path) -> datetime:
    """Thời điểm file được XUẤT từ Sapo — ưu tiên parse từ tên file (đúng thời điểm export
    thật), fallback về mtime trên đĩa nếu tên file bị đổi/không khớp pattern mặc định của Sapo
    (VD Huy đổi tên file thủ công) — mtime kém tin cậy hơn (có thể đổi khi git clone/checkout)
    nhưng vẫn tốt hơn là không có gì để so sánh."""
    m = _FILENAME_TS_RE.search(path.stem)
    if m:
        dd, mm, yyyy, hh, mi = m.groups()
        try:
            return datetime(int(yyyy), int(mm), int(dd), int(hh), int(mi))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


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
        file_ts = _file_export_timestamp(f)
        print(f"[Chi phí] File {f.name} (xuất lúc {file_ts}): {total_rows} dòng, "
              f"giữ lại {len(df)} dòng có Mã chứng từ (bỏ {dropped} dòng).")
        if "Nguồn ghi nhận" in df.columns:
            print(f"  Nguồn ghi nhận trong file này: {df['Nguồn ghi nhận'].value_counts().to_dict()}")

        df["Mã chứng từ"] = df["Mã chứng từ"].astype(str).str.strip()
        df["Giá trị ghi nhận"] = pd.to_numeric(df["Giá trị ghi nhận"], errors="coerce").fillna(0)
        df["_file_ts"] = file_ts
        df["_file_name"] = f.name
        raw_frames.append(df)

    if not raw_frames:
        return pd.DataFrame()

    all_rows = pd.concat(raw_frames, ignore_index=True)

    # Nhiều file export có thể CHỒNG LẤN khoảng ngày -> khử trùng ở mức DÒNG (cùng Ngày ghi
    # nhận + Mã chứng từ + Tên chi phí + Giá trị ghi nhận = re-export cùng 1 dòng sổ cái thật).
    dedup_cols = [c for c in ["Ngày ghi nhận", "Mã chứng từ", "Tên chi phí", "Giá trị ghi nhận"] if c in all_rows.columns]
    before = len(all_rows)
    all_rows = all_rows.drop_duplicates(subset=dedup_cols)
    if before != len(all_rows):
        print(f"[Chi phí] Đã bỏ {before - len(all_rows)} dòng trùng lặp giữa các file export chồng lấn.")

    # CÙNG 1 đơn hàng (Mã chứng từ) xuất hiện ở NHIỀU file KHÁC NHAU (nguồn) với dữ liệu KHÔNG
    # khớp y hệt (VD đơn cuối tháng 6 vừa lọt vào export tháng 6 lúc chưa hoàn thành hết, vừa
    # lọt vào export tháng 7 với đầy đủ hơn) -> theo yêu cầu của Huy: chỉ giữ TOÀN BỘ dòng của
    # đơn đó từ file có thời điểm XUẤT MỚI NHẤT, bỏ hẳn dòng của (các) file cũ hơn cho đúng
    # đơn đó (không cộng dồn 2 nguồn cho cùng 1 đơn -> tránh đếm trùng phí).
    order_file_counts = all_rows.groupby("Mã chứng từ")["_file_ts"].nunique()
    conflicted_orders = order_file_counts[order_file_counts > 1].index
    if len(conflicted_orders):
        latest_ts_per_order = all_rows.groupby("Mã chứng từ")["_file_ts"].transform("max")
        is_conflicted = all_rows["Mã chứng từ"].isin(conflicted_orders)
        keep_mask = (~is_conflicted) | (all_rows["_file_ts"] == latest_ts_per_order)
        dropped_rows = (~keep_mask).sum()
        print(f"[Chi phí] {len(conflicted_orders)} đơn hàng xuất hiện ở >1 file export khác thời "
              f"điểm với dữ liệu không khớp y hệt -> giữ dữ liệu từ file MỚI NHẤT cho các đơn "
              f"này, bỏ {dropped_rows} dòng từ file cũ hơn (tránh cộng trùng).")
        all_rows = all_rows[keep_mask]

    all_rows = all_rows.drop(columns=["_file_ts", "_file_name"])

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
    all_rows["Tên chi phí"] = all_rows["Tên chi phí"].replace(FEE_NAME_NORMALIZE)

    # Theo xác nhận của Huy (04/08/2026), CHỈ áp dụng cho nguồn shopee/tiktokshop (KHÔNG áp
    # dụng cho lazada — Huy không nhắc tới lazada nên giữ nguyên logic cũ cho nguồn này):
    #   - Giảm giá/voucher DO SÀN (Shopee/TikTok) tài trợ -> seller vẫn nhận đủ phần này, sàn tự
    #     chịu, không phải chi phí thật của seller -> loại khỏi "chi phí".
    #   - Giảm giá/voucher DO SHOP tự tài trợ -> đã bị cấn trừ thẳng vào doanh thu thuần rồi ->
    #     tính thêm vào chi phí sẽ bị đếm trùng (double-count) -> loại khỏi "chi phí".
    #   - Phí vận chuyển (Phí vận chuyển thực tế + phần người mua trả + phần Shopee trợ giá):
    #     3 mảnh này cộng lại là toàn bộ chi phí ship, được người mua + Shopee gánh hết ->
    #     seller không thực trả -> loại cả 3 khỏi "chi phí" (không phải chỉ loại phần trợ giá).
    #   - Shopee Xu (đánh dấu X trực tiếp trên ảnh chụp màn hình 04/08/2026): Shopee tự bỏ tiền
    #     tài trợ Xu cho người mua, tương tự "Giảm giá Shopee" -> không phải chi phí thật của
    #     seller -> loại khỏi "chi phí".
    # Loại hẳn khỏi cả total_fee (load_settlement_fees) lẫn breakdown (load_settlement_fee_
    # breakdown) vì đây không phải "chi phí" theo định nghĩa của Huy, không phải chỉ ẩn khỏi
    # hiển thị.
    _EXCLUDED_FEE_NAMES_SHOPEE_TIKTOK = {
        "Giàm giá Shopee",
        "Trợ giá từ Tiktok",
        "Mã ưu đãi do Người Bán chịu",
        "Khuyến mãi của người bán",
        "Hoàn lại khuyến mãi của người bán",
        "Phí vận chuyển do người mua trả",
        "Phí vận chuyển được trợ giá từ Shopee",
        "Shopee Xu",
        "Phí vận chuyển thực tế",
    }
    exclude_mask = (
        all_rows["Tên chi phí"].isin(_EXCLUDED_FEE_NAMES_SHOPEE_TIKTOK)
        & source_col.isin({"shopee", "tiktokshop"})
    )
    if exclude_mask.any():
        print(f"[Chi phí] Loại {exclude_mask.sum()} dòng (nguồn shopee/tiktokshop) khỏi Tổng phí "
              f"theo xác nhận của Huy: giảm giá/voucher do sàn hoặc shop tài trợ, và phí vận "
              f"chuyển (thực tế/người mua trả/Shopee trợ giá) — không phải chi phí thật của seller.")
        all_rows = all_rows[~exclude_mask]

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
