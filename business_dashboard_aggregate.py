"""
Gộp dữ liệu từ Sapo (orders + variant->sku) + file product_costs.csv (giá vốn theo SKU)
+ Meta (ads spend) + Settlement (fees) thành 1 bảng tổng hợp theo từng kênh bán hàng,
tách thêm theo SHOP/PAGE cụ thể (parse từ field "tags" của order).

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

Lưu ý về total_fee khi tách theo shop/page: file đối soát (settlement) hiện chỉ có granularity
theo channel (platform), CHƯA có theo từng shop/page riêng. Nên total_fee sẽ bị lặp lại giống
nhau cho mọi shop/page trong cùng 1 channel cho tới khi có file đối soát chi tiết hơn.

Cách nhận diện SHOP/PAGE: Sapo lưu thông tin này trong field "tags" của order, dạng:
  - Shopee/TikTok/Lazada: "Shopee Channel1, Shopee_<Tên shop>" -> lấy phần sau "Shopee_"
  - Facebook: "..., page_<Tên page>, page_id_<id>, ..." -> lấy phần sau "page_" (không phải "page_id_")
Nếu không tìm thấy pattern nào phù hợp (VD: pos, admin, zalo, web) -> shop_page để rỗng "".
"""

import re

import pandas as pd

_SHOP_TAG_RE = re.compile(r"^(?:Shopee|Tiktok|Lazada)_(.+)$", re.IGNORECASE)
_PAGE_TAG_RE = re.compile(r"^page_(?!id_)(.+)$", re.IGNORECASE)


def _extract_shop_page(tags) -> str:
    """Parse tên shop/page cụ thể từ field 'tags' của order Sapo. Xem docstring module."""
    if not tags or not isinstance(tags, str):
        return ""
    parts = [p.strip() for p in tags.split(",")]
    for p in parts:
        m = _SHOP_TAG_RE.match(p)
        if m:
            return m.group(1).strip()
    for p in parts:
        m = _PAGE_TAG_RE.match(p)
        if m:
            return m.group(1).strip()
    return ""


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


def _prep_orders_df(orders: list) -> pd.DataFrame:
    """Tạo DataFrame từ orders, thêm cột shop_page (parse từ tags)."""
    df = pd.DataFrame(orders)
    if "tags" in df.columns:
        df["shop_page"] = df["tags"].apply(_extract_shop_page)
    else:
        df["shop_page"] = ""
    return df


def build_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    ads_data: dict,
    settlement_df: pd.DataFrame,
) -> pd.DataFrame:
    orders_df = _prep_orders_df(orders)
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(_line_item_cost(li, variant_sku_map, cost_map) for li in items)
    )
    orders_df["order_id"] = orders_df["id"].astype(str)

    gross = orders_df.groupby(["source_name", "shop_page"]).agg(
        gross_revenue=("total_price", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
    ).rename_axis(["channel", "shop_page"]).reset_index()

    if settlement_df is not None and not settlement_df.empty:
        settlement_df = settlement_df.copy()
        settlement_df["order_id"] = settlement_df["order_id"].astype(str)
        fees = settlement_df.groupby("channel").agg(total_fee=("total_fee", "sum")).reset_index()
        gross = gross.merge(fees, on="channel", how="left")
        gross["total_fee"] = gross["total_fee"].fillna(0)
    else:
        gross["total_fee"] = 0.0

    summary = gross
    # Ads spend KHÔNG gán theo kênh (xem lý do ở docstring) -> để 0 ở đây,
    # tổng ads spend thật lấy riêng từ ads_data["total_spend"] khi xuất data.json.
    summary["ads_spend"] = 0.0

    summary["net_revenue"] = summary["gross_revenue"] - summary["total_fee"]
    summary["gross_margin_amount"] = summary["net_revenue"] - summary["cogs"]
    summary["gross_margin_pct"] = (summary["gross_margin_amount"] / summary["net_revenue"] * 100).round(1)
    summary["net_profit_after_ads"] = summary["gross_margin_amount"] - summary["ads_spend"]

    cols = ["channel", "shop_page", "orders", "gross_revenue", "total_fee", "net_revenue",
            "cogs", "gross_margin_amount", "gross_margin_pct", "ads_spend", "net_profit_after_ads"]
    return summary[cols].sort_values("gross_revenue", ascending=False).reset_index(drop=True)


def build_product_breakdown(orders: list, variant_sku_map: dict, cost_map: dict) -> pd.DataFrame:
    """
    Bảng chi tiết theo SẢN PHẨM x KÊNH x SHOP/PAGE: số lượng bán, doanh thu, giá vốn, gross margin.
    Dùng title trong line_items làm tên sản phẩm hiển thị, sku để join giá vốn.
    """
    rows = []
    for o in orders:
        channel = o.get("source_name")
        shop_page = _extract_shop_page(o.get("tags"))
        for li in o.get("line_items", []):
            variant_id = li.get("variant_id")
            sku = variant_sku_map.get(variant_id, "")
            qty = li.get("quantity", 0)
            revenue = li.get("price", 0) * qty
            cost = cost_map.get(sku, 0.0) * qty
            rows.append({
                "channel": channel,
                "shop_page": shop_page,
                "product": li.get("title") or sku or "(không tên)",
                "sku": sku,
                "quantity": qty,
                "revenue": revenue,
                "cogs": cost,
            })

    if not rows:
        return pd.DataFrame(columns=["channel", "shop_page", "product", "sku", "quantity", "revenue", "cogs",
                                      "gross_margin_amount", "gross_margin_pct"])

    df = pd.DataFrame(rows)
    grouped = df.groupby(["channel", "shop_page", "product", "sku"]).agg(
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
    Trả về: date, channel, shop_page, orders, gross_revenue, total_fee, net_revenue, cogs,
            gross_margin_amount, gross_margin_pct
    """
    if not orders:
        return pd.DataFrame(columns=["date", "channel", "shop_page", "orders", "gross_revenue", "total_fee",
                                      "net_revenue", "cogs", "gross_margin_amount", "gross_margin_pct"])

    orders_df = _prep_orders_df(orders)
    orders_df["date"] = orders_df.apply(_order_date, axis=1)
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(_line_item_cost(li, variant_sku_map, cost_map) for li in items)
    )
    orders_df["order_id"] = orders_df["id"].astype(str)

    gross = orders_df.groupby(["date", "source_name", "shop_page"]).agg(
        gross_revenue=("total_price", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
    ).rename_axis(["date", "channel", "shop_page"]).reset_index()

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

    cols = ["date", "channel", "shop_page", "orders", "gross_revenue", "total_fee", "net_revenue",
            "cogs", "gross_margin_amount", "gross_margin_pct"]
    return gross[cols].sort_values(["date", "channel", "shop_page"]).reset_index(drop=True)
