"""
Gộp dữ liệu từ Sapo (orders + cost) + Meta (ads spend) + Settlement (fees)
thành 1 bảng tổng hợp theo từng kênh bán hàng.

Công thức:
  gross_revenue      = tổng total_price các order theo kênh
  cogs               = tổng (cost_price * quantity) theo kênh
  total_fee          = tổng phí sàn + ship + voucher + aff + đồng tài trợ (từ file đối soát)
  net_revenue        = gross_revenue - total_fee
  gross_margin_amount= net_revenue - cogs
  gross_margin_pct   = gross_margin_amount / net_revenue * 100
  ads_spend          = chi phí quảng cáo phân bổ theo kênh (ước lượng theo tên campaign)
"""

import pandas as pd


def build_summary(orders: list[dict], product_costs: dict, ads_data: dict, settlement_df: pd.DataFrame) -> pd.DataFrame:
    orders_df = pd.DataFrame(orders)
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(product_costs.get(li["product_id"], 0) * li["quantity"] for li in items)
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

    # Phân bổ ads spend theo kênh dựa trên tên campaign (chứa tên kênh)
    ads_by_channel = {ch: 0.0 for ch in summary["channel"]}
    for c in ads_data.get("campaigns", []):
        for ch in ads_by_channel:
            if ch.lower().split()[0] in (c.get("name") or "").lower():
                ads_by_channel[ch] += c.get("spend", 0)
    summary["ads_spend"] = summary["channel"].map(ads_by_channel).fillna(0)

    summary["net_revenue"] = summary["gross_revenue"] - summary["total_fee"]
    summary["gross_margin_amount"] = summary["net_revenue"] - summary["cogs"]
    summary["gross_margin_pct"] = (summary["gross_margin_amount"] / summary["net_revenue"] * 100).round(1)
    summary["net_profit_after_ads"] = summary["gross_margin_amount"] - summary["ads_spend"]

    cols = ["channel", "orders", "gross_revenue", "total_fee", "net_revenue",
            "cogs", "gross_margin_amount", "gross_margin_pct", "ads_spend", "net_profit_after_ads"]
    return summary[cols].sort_values("gross_revenue", ascending=False).reset_index(drop=True)
