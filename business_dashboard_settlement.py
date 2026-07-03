"""
Parser đọc file đối soát (settlement) export từ Shopee / TikTok Shop Seller Center.

QUAN TRỌNG: mỗi sàn đặt tên cột khác nhau, nên phần map dưới đây là PLACEHOLDER
— cần bạn gửi 1 file mẫu thật để mình chỉnh lại đúng tên cột.

Cách dùng:
1. Export file đối soát từ Shopee / TikTok Shop (định dạng .xlsx)
2. Bỏ vào thư mục "settlement_files/" (tự tạo cạnh các file .py này), đặt tên có chứa
   "shopee" hoặc "tiktok" để tool tự nhận diện kênh, vd: shopee_doi_soat_thang6.xlsx
3. Chạy lại chương trình — tool sẽ tự đọc và cộng dồn các khoản phí theo order_id
"""

import random
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

# Mapping tên cột trong file gốc -> tên chuẩn dùng nội bộ.
# SỬA LẠI phần này theo đúng file đối soát thật của bạn.
COLUMN_MAP = {
    "shopee": {
        "order_id": "Mã đơn hàng",
        "platform_fee": "Phí cố định",          # + phí thanh toán, phí dịch vụ... tuỳ cấu trúc thật
        "shipping_fee": "Phí vận chuyển",
        "voucher": "Voucher người bán",
        "aff_fee": "Phí hoa hồng Affiliate",
        "co_funded_voucher": "Voucher đồng tài trợ",
    },
    "tiktok": {
        "order_id": "Order ID",
        "platform_fee": "Platform Fee",
        "shipping_fee": "Shipping Fee",
        "voucher": "Seller Voucher",
        "aff_fee": "Affiliate Commission",
        "co_funded_voucher": "Co-funded Voucher",
    },
}

FEE_COLUMNS = ["platform_fee", "shipping_fee", "voucher", "aff_fee", "co_funded_voucher"]


def load_settlement_fees() -> pd.DataFrame:
    """
    Trả về DataFrame: order_id, channel, platform_fee, shipping_fee, voucher, aff_fee, co_funded_voucher, total_fee
    """
    if Config.DEMO_MODE:
        return _demo_settlement()

    settlement_dir = Config.SETTLEMENT_DIR
    if not settlement_dir.exists():
        settlement_dir.mkdir(parents=True, exist_ok=True)

    files = list(settlement_dir.glob("*.xlsx"))
    if not files:
        # Chưa có file đối soát nào -> trả về bảng rỗng, dashboard sẽ hiện fee = 0
        return pd.DataFrame(columns=["order_id", "channel"] + FEE_COLUMNS + ["total_fee"])

    frames = []
    for f in files:
        fname = f.name.lower()
        channel_key = "shopee" if "shopee" in fname else ("tiktok" if "tiktok" in fname else None)
        if channel_key is None:
            continue  # bỏ qua file không nhận diện được kênh
        col_map = COLUMN_MAP[channel_key]
        raw = pd.read_excel(f)
        df = pd.DataFrame()
        df["order_id"] = raw[col_map["order_id"]].astype(str)
        df["channel"] = "Shopee" if channel_key == "shopee" else "TikTok Shop"
        for fee_col in FEE_COLUMNS:
            src_col = col_map[fee_col]
            df[fee_col] = pd.to_numeric(raw.get(src_col, 0), errors="coerce").fillna(0)
        df["total_fee"] = df[FEE_COLUMNS].sum(axis=1)
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["order_id", "channel"] + FEE_COLUMNS + ["total_fee"])

    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# DEMO DATA
# ---------------------------------------------------------------------------

def _demo_settlement() -> pd.DataFrame:
    random.seed(99)
    rows = []
    for i in range(1, 181):
        channel = Config.CHANNELS[i % 2]
        platform_fee = random.randint(5_000, 30_000)
        shipping_fee = random.randint(0, 15_000)
        voucher = random.randint(0, 20_000)
        aff_fee = random.randint(0, 25_000)
        co_funded = random.randint(0, 10_000)
        rows.append({
            "order_id": i,
            "channel": channel,
            "platform_fee": platform_fee,
            "shipping_fee": shipping_fee,
            "voucher": voucher,
            "aff_fee": aff_fee,
            "co_funded_voucher": co_funded,
            "total_fee": platform_fee + shipping_fee + voucher + aff_fee + co_funded,
        })
    return pd.DataFrame(rows)
