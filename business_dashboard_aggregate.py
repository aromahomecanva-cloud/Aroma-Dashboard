"""
Gộp dữ liệu từ Sapo (orders + variant->sku) + file product_costs.csv (giá vốn theo SKU)
+ Meta (ads spend) + Settlement (fees) thành 1 bảng tổng hợp theo từng kênh bán hàng.

Công thức:
  gross_revenue      = tổng total_price các order theo kênh
  cogs               = tổng (giá_vốn_theo_sku * quantity) theo kênh (join qua variant_id -> sku)
  total_fee          = tổng phí sàn + ship + voucher + aff + đồng tài trợ (từ file đối soát)
  net_revenue        = gross_revenue - total_fee
  gross_margin_amount= net_revenue - cogs
  gross_margin_pct   = gross_margin_amount / net_revenue * 100

Lưu ý về ads_spend: Meta Ads (Facebook/Instagram) không nhất thiết chạy cho từng kênh bán
hàng cụ thể (Shopee/TikTok Shop có nền tảng ads riêng của họ). Vì vậy ads_spend KHÔNG được
tự động gán vào 1 kênh cụ thể nào — hiển thị như 1 tổng riêng (xem total_ads_spend trả về
cùng payload khi xuất data.json, lấy trực tiếp từ ads_data['total_spend']).
"""

import pandas as pd


def _line_item_cost(li: dict, variant_sku_map: dict, cost_map: dict) -> float:
    variant_id = li.get("variant_id")
    sku = variant_sku_map.get(variant_id)
    if sku is None:
        return 0.0
    return cost_map.get(sku, 0.0) * li.get("quantity", 0)


def build_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    ads_data: dict,
    settlement_df: pd.DataFrame,
) -> pd.DataFrame:
    orders_df = pd.DataFrame(orders)
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(_line_item_cost(li, variant_sku_map, cost_map) for li in items)
    )
    orders_df["order_id"] = orders_df["id"].astype(str)

    gross = orders_df.groupby("source_name").agg(
        gross_revenue=("total_price", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
    ).rename_axis("channel").reset_index()

    if settlement_df is not None and not settlement_df.empty:
        settlement_df = settlement_df.copy()
        settlement_df["order_id"] = settlement_df["order_id"].astype(str)
        fees = settlement_df.groupby("channel").agg(total_fee=("total_fee", "sum")).reset_index()
    else:
        fees = pd.DataFrame({"channel": gross["channel"], "total_fee": 0})

    summary = gross.merge(fees, on="channel", how="left")
    summary["total_fee"] = summary["total_fee"].fillna(0)

    # Ads spend KHÔNG gán theo kênh (xem lý do ở docstring) -> để 0 ở đây,
    # tổng ads spend thật lấy riêng từ ads_data["total_spend"] khi xuất data.json.
    summary["ads_spend"] = 0.0

    summary["net_revenue"] = summary["gross_revenue"] - summary["total_fee"]
    summary["gross_margin_amount"] = summary["net_revenue"] - summary["cogs"]
    summary["gross_margin_pct"] = (summary["gross_margin_amount"] / summary["net_revenue"] * 100).round(1)
    summary["net_profit_after_ads"] = summary["gross_margin_amount"] - summary["ads_spend"]

    cols = ["channel", "orders", "gross_revenue", "total_fee", "net_revenue",
            "cogs", "gross_margin_amount", "gross_margin_pct", "ads_spend", "net_profit_after_ads"]
    return summary[cols].sort_values("gross_revenue", ascending=False).reset_index(drop=True)
