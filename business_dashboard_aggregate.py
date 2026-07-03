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


def _order_date(o: dict) -> str:
    """Lấy ngày (YYYY-MM-DD) từ created_on của order."""
    created = o.get("created_on") or ""
    return str(created)[:10]


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


def build_product_breakdown(orders: list, variant_sku_map: dict, cost_map: dict) -> pd.DataFrame:
    """
    Bảng chi tiết theo SẢN PHẨM x KÊNH: số lượng bán, doanh thu, giá vốn, gross margin.
    Dùng title trong line_items làm tên sản phẩm hiển thị, sku để join giá vốn.
    """
    rows = []
    for o in orders:
        channel = o.get("source_name")
        for li in o.get("line_items", []):
            variant_id = li.get("variant_id")
            sku = variant_sku_map.get(variant_id, "")
            qty = li.get("quantity", 0)
            revenue = li.get("price", 0) * qty
            cost = cost_map.get(sku, 0.0) * qty
            rows.append({
                "channel": channel,
                "product": li.get("title") or sku or "(không tên)",
                "sku": sku,
                "quantity": qty,
                "revenue": revenue,
                "cogs": cost,
            })

    if not rows:
        return pd.DataFrame(columns=["channel", "product", "sku", "quantity", "revenue", "cogs",
                                      "gross_margin_amount", "gross_margin_pct"])

    df = pd.DataFrame(rows)
    grouped = df.groupby(["channel", "product", "sku"]).agg(
        quantity=("quantity", "sum"),
        revenue=("revenue", "sum"),
        cogs=("cogs", "sum"),
    ).reset_index()
    grouped["gross_margin_amount"] = grouped["revenue"] - grouped["cogs"]
    grouped["gross_margin_pct"] = grouped.apply(
        lambda r: round(r["gross_margin_amount"] / r["revenue"] * 100, 1) if r["revenue"] else 0.0, axis=1
    )
    return grouped.sort_values(["channel", "revenue"], ascending=[True, False]).reset_index(drop=True)


def build_daily_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    settlement_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Giống build_summary nhưng tách thêm theo NGÀY (date) — dùng để dashboard
    lọc theo khoảng thời gian mà không cần gọi lại API mỗi lần đổi filter.
    Trả về: date, channel, orders, gross_revenue, total_fee, net_revenue, cogs,
            gross_margin_amount, gross_margin_pct
    """
    if not orders:
        return pd.DataFrame(columns=["date", "channel", "orders", "gross_revenue", "total_fee",
                                      "net_revenue", "cogs", "gross_margin_amount", "gross_margin_pct"])

    orders_df = pd.DataFrame(orders)
    orders_df["date"] = orders_df.apply(_order_date, axis=1)
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(_line_item_cost(li, variant_sku_map, cost_map) for li in items)
    )
    orders_df["order_id"] = orders_df["id"].astype(str)

    gross = orders_df.groupby(["date", "source_name"]).agg(
        gross_revenue=("total_price", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
    ).rename_axis(["date", "channel"]).reset_index()

    if settlement_df is not None and not settlement_df.empty:
        settlement_df = settlement_df.copy()
        settlement_df["order_id"] = settlement_df["order_id"].astype(str)
        # Nếu settlement có ngày thì join theo (date, channel); hiện tại chưa có nên gộp theo channel
        fees = settlement_df.groupby("channel").agg(total_fee=("total_fee", "sum")).reset_index()
        gross = gross.merge(fees, on="channel", how="left")
        gross["total_fee"] = gross["total_fee"].fillna(0)
    else:
        gross["total_fee"] = 0.0

    gross["net_revenue"] = gross["gross_revenue"] - gross["total_fee"]
    gross["gross_margin_amount"] = gross["net_revenue"] - gross["cogs"]
    gross["gross_margin_pct"] = gross.apply(
        lambda r: round(r["gross_margin_amount"] / r["net_revenue"] * 100, 1) if r["net_revenue"] else 0.0, axis=1
    )

    cols = ["date", "channel", "orders", "gross_revenue", "total_fee", "net_revenue",
            "cogs", "gross_margin_amount", "gross_margin_pct"]
    return gross[cols].sort_values(["date", "channel"]).reset_index(drop=True)
