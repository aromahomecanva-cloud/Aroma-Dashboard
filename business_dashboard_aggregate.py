"""
Gộp dữ liệu từ Sapo (orders + variant->sku) + file product_costs.csv (giá vốn theo SKU)
+ Meta (ads spend) + Settlement (fees) thành 1 bảng tổng hợp theo từng kênh bán hàng,
tách thêm theo SHOP/PAGE cụ thể (parse từ field "tags" của order).

Công thức:
  gross_revenue      = tổng total_price các order theo kênh
  cogs               = tổng (giá_vốn_theo_sku * quantity) theo kênh (join qua variant_id -> sku)
  total_fee          = tổng phí sàn + ship + voucher + aff + đồng tài trợ (từ file Chi phí Sapo)
  net_revenue        = gross_revenue - total_fee
  gross_margin_amount= net_revenue - cogs
  gross_margin_pct   = gross_margin_amount / net_revenue * 100

Lưu ý về ads_spend: Meta Ads (Facebook/Instagram) không nhất thiết chạy cho từng kênh bán
hàng cụ thể (Shopee/TikTok Shop có nền tảng ads riêng của họ). Vì vậy ads_spend KHÔNG được
tự động gán vào 1 kênh cụ thể nào — hiển thị như 1 tổng riêng (xem total_ads_spend trả về
cùng payload khi xuất data.json, lấy trực tiếp từ ads_data['total_spend']).

Cách nhận diện SHOP/PAGE: Sapo lưu thông tin này trong field "tags" của order, dạng:
  - Shopee/TikTok/Lazada: "Shopee Channel1, Shopee_<Tên shop>" -> lấy phần sau "Shopee_"
  - Facebook: "..., page_<Tên page>, page_id_<id>, ..." -> lấy phần sau "page_" (không phải "page_id_")
Nếu không tìm thấy pattern nào phù hợp (VD: pos, admin, zalo, web) -> shop_page để rỗng "".

Cách join total_fee (từ file "Chi phí" export của Sapo, xem business_dashboard_settlement.py) —
ĐÃ XÁC NHẬN qua business_dashboard_debug_fee_match.py chạy trên dữ liệu thật:
  - Đơn SÀN (shopee/tiktokshop/lazada): "Mã chứng từ" == order["name"]
  - Đơn NGOẠI SÀN (facebook/instagram/zalo/...): phần số trong "Tham chiếu" (VD "SON12345"
    -> "12345") == str(order["order_number"])
  settlement_df trả về 2 nhóm dòng (join_field="name" hoặc "order_number"), mỗi order được
  join total_fee bằng CẢ HAI map cộng lại (trên thực tế 1 order chỉ khớp đúng 1 trong 2, nên
  cộng lại tương đương "hoặc cái này hoặc cái kia").

Về SẢN PHẨM COMBO trong build_product_breakdown(): Sapo tách 1 dòng combo trong order
thành NHIỀU line_items riêng theo từng sản phẩm THÀNH TỐ (component), làm mất view
"đã bán bao nhiêu combo". Hàm build_product_breakdown() dùng BOM (business_dashboard_costs
.get_combo_bom()) để GHÉP LẠI: với mỗi order, nếu số lượng các SKU thành tố có mặt đủ theo
đúng tỉ lệ của 1 combo đã biết (số lượng là bội số nguyên của tỉ lệ BOM), gộp N phần đó
thành N dòng combo (doanh thu = tổng doanh thu đã phân bổ theo tỉ lệ dùng, giá vốn =
cost_map[combo_sku] * N), phần dư (nếu component còn thừa ngoài combo) vẫn giữ nguyên là
dòng sản phẩm thường.
"""

import re

import pandas as pd

from business_dashboard_costs import get_combo_bom, get_combo_names

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


def _build_fee_maps(settlement_df: pd.DataFrame) -> tuple[dict, dict]:
    """
    Trả về (fee_map_by_name, fee_map_by_order_number) từ file Chi phí Sapo.
    Xem business_dashboard_settlement.py — mỗi dòng settlement_df có join_field
    là "name" (đơn sàn) hoặc "order_number" (đơn ngoại sàn).
    """
    if settlement_df is None or settlement_df.empty:
        return {}, {}
    by_name = settlement_df[settlement_df["join_field"] == "name"]
    by_number = settlement_df[settlement_df["join_field"] == "order_number"]
    fee_map_by_name = dict(zip(by_name["join_key"].astype(str), by_name["total_fee"]))
    fee_map_by_order_number = dict(zip(by_number["join_key"].astype(str), by_number["total_fee"]))
    return fee_map_by_name, fee_map_by_order_number


def _build_fee_breakdown_maps(fee_breakdown_df: pd.DataFrame) -> tuple[dict, dict]:
    """
    Trả về (breakdown_map_by_name, breakdown_map_by_order_number): mỗi map là
    {join_key: {fee_name: amount, ...}} — chi tiết TỪNG LOẠI PHÍ (Phí cố định, Phí dịch vụ,
    Phí thanh toán, Thuế sàn thực tế, Phí tiếp thị liên kết (aff), ...) thay vì chỉ 1 tổng.
    Xem business_dashboard_settlement.load_settlement_fee_breakdown().
    """
    if fee_breakdown_df is None or fee_breakdown_df.empty:
        return {}, {}
    by_name = fee_breakdown_df[fee_breakdown_df["join_field"] == "name"]
    by_number = fee_breakdown_df[fee_breakdown_df["join_field"] == "order_number"]

    breakdown_map_by_name = {
        str(key): dict(zip(grp["fee_name"], grp["amount"]))
        for key, grp in by_name.groupby("join_key")
    }
    breakdown_map_by_order_number = {
        str(key): dict(zip(grp["fee_name"], grp["amount"]))
        for key, grp in by_number.groupby("join_key")
    }
    return breakdown_map_by_name, breakdown_map_by_order_number


def _merge_fee_breakdown(a: dict, b: dict) -> dict:
    """Cộng dồn 2 dict {fee_name: amount} (1 order chỉ khớp đúng 1 trong 2 nhánh join
    trên thực tế, nhưng cộng lại cho an toàn — tương đương "hoặc cái này hoặc cái kia")."""
    if not a and not b:
        return {}
    merged = dict(a)
    for k, v in b.items():
        merged[k] = merged.get(k, 0) + v
    return merged


def _sum_fee_breakdowns(dicts) -> dict:
    """Cộng dồn nhiều dict {fee_name: amount} lại thành 1 (dùng khi groupby nhiều order)."""
    out = {}
    for d in dicts:
        for k, v in d.items():
            out[k] = out.get(k, 0) + v
    return out


def fee_join_diagnostics(orders: list, settlement_df: pd.DataFrame) -> dict:
    """
    Thống kê tỷ lệ khớp giữa order thật và file Chi phí, tách riêng cho từng nhánh
    join (name / order_number), để phát hiện sớm nếu 1 trong 2 giả thuyết join-key sai.
    """
    if settlement_df is None or settlement_df.empty:
        return {"settlement_rows": 0, "matched": 0, "match_rate": None,
                "by_name": {}, "by_order_number": {}}

    order_names = {str(o.get("name")) for o in orders if o.get("name")}
    order_numbers = {str(o.get("order_number")) for o in orders if o.get("order_number") is not None}

    by_name = settlement_df[settlement_df["join_field"] == "name"]
    by_number = settlement_df[settlement_df["join_field"] == "order_number"]

    name_keys = set(by_name["join_key"].astype(str))
    number_keys = set(by_number["join_key"].astype(str))

    matched_name = len(order_names & name_keys)
    matched_number = len(order_numbers & number_keys)
    total = len(name_keys) + len(number_keys)
    matched = matched_name + matched_number

    return {
        "settlement_rows": total,
        "matched": matched,
        "match_rate": round(matched / total * 100, 1) if total else None,
        "by_name": {"total": len(name_keys), "matched": matched_name},
        "by_order_number": {"total": len(number_keys), "matched": matched_number},
    }


def _prep_orders_df(
    orders: list,
    fee_map_by_name: dict,
    fee_map_by_order_number: dict,
    fee_breakdown_by_name: dict | None = None,
    fee_breakdown_by_order_number: dict | None = None,
) -> pd.DataFrame:
    """Tạo DataFrame từ orders, thêm cột shop_page (parse từ tags), total_fee (join 2 nhánh)
    và fee_breakdown (dict chi tiết từng loại phí, join 2 nhánh tương tự total_fee)."""
    df = pd.DataFrame(orders)
    if "tags" in df.columns:
        df["shop_page"] = df["tags"].apply(_extract_shop_page)
    else:
        df["shop_page"] = ""

    fee_from_name = df["name"].astype(str).map(fee_map_by_name).fillna(0.0) if "name" in df.columns else 0.0
    fee_from_number = df["order_number"].astype(str).map(fee_map_by_order_number).fillna(0.0) \
        if "order_number" in df.columns else 0.0
    df["total_fee"] = fee_from_name + fee_from_number

    fee_breakdown_by_name = fee_breakdown_by_name or {}
    fee_breakdown_by_order_number = fee_breakdown_by_order_number or {}
    names = df["name"].astype(str) if "name" in df.columns else pd.Series([""] * len(df))
    numbers = df["order_number"].astype(str) if "order_number" in df.columns else pd.Series([""] * len(df))
    df["fee_breakdown"] = [
        _merge_fee_breakdown(fee_breakdown_by_name.get(n, {}), fee_breakdown_by_order_number.get(on, {}))
        for n, on in zip(names, numbers)
    ]
    return df


def build_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    ads_data: dict,
    settlement_df: pd.DataFrame,
    fee_breakdown_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    fee_map_by_name, fee_map_by_order_number = _build_fee_maps(settlement_df)
    fee_breakdown_by_name, fee_breakdown_by_order_number = _build_fee_breakdown_maps(fee_breakdown_df)
    orders_df = _prep_orders_df(
        orders, fee_map_by_name, fee_map_by_order_number,
        fee_breakdown_by_name, fee_breakdown_by_order_number,
    )
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(_line_item_cost(li, variant_sku_map, cost_map) for li in items)
    )

    summary = orders_df.groupby(["source_name", "shop_page"]).agg(
        gross_revenue=("total_price", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
        total_fee=("total_fee", "sum"),
    ).rename_axis(["channel", "shop_page"]).reset_index()

    # Chi tiết từng loại phí (Phí cố định, Phí dịch vụ, Phí thanh toán, Thuế sàn, aff, ...)
    # theo từng tổ hợp channel + shop_page -> để dashboard hiển thị breakdown thay vì chỉ
    # 1 con số tổng.
    # LƯU Ý QUAN TRỌNG: KHÔNG dùng .groupby(...)["fee_breakdown"].apply(_sum_fee_breakdowns) —
    # khi hàm trả về 1 dict, pandas GroupBy.apply() sẽ TỰ ĐỘNG convert dict đó thành pd.Series
    # (không giữ nguyên dict thường), và summary.apply(axis=1) tương tự cũng "nở" dict/Series
    # thành nhiều cột -> lỗi "Cannot set a DataFrame with multiple columns to the single column
    # fee_breakdown". Dùng vòng lặp for thường (không qua .apply()) để giữ nguyên kiểu dict.
    fee_breakdown_by_group = {}
    for (ch, sp), grp in orders_df.groupby(["source_name", "shop_page"]):
        fee_breakdown_by_group[(ch, sp)] = _sum_fee_breakdowns(grp["fee_breakdown"])
    summary["fee_breakdown"] = pd.Series(
        [fee_breakdown_by_group.get((ch, sp), {}) for ch, sp in zip(summary["channel"], summary["shop_page"])],
        index=summary.index, dtype=object,
    )

    # Ads spend KHÔNG gán theo kênh (xem lý do ở docstring) -> để 0 ở đây,
    # tổng ads spend thật lấy riêng từ ads_data["total_spend"] khi xuất data.json.
    summary["ads_spend"] = 0.0

    summary["net_revenue"] = summary["gross_revenue"] - summary["total_fee"]
    summary["gross_margin_amount"] = summary["net_revenue"] - summary["cogs"]
    summary["gross_margin_pct"] = (summary["gross_margin_amount"] / summary["net_revenue"] * 100).round(1)
    summary["net_profit_after_ads"] = summary["gross_margin_amount"] - summary["ads_spend"]

    cols = ["channel", "shop_page", "orders", "gross_revenue", "total_fee", "fee_breakdown", "net_revenue",
            "cogs", "gross_margin_amount", "gross_margin_pct", "ads_spend", "net_profit_after_ads"]
    return summary[cols].sort_values("gross_revenue", ascending=False).reset_index(drop=True)


def _reconcile_combo_lines(sku_agg: dict, combo_bom_sorted: list) -> list:
    """
    sku_agg: {sku: {"qty": float, "revenue": float, "title": str}} — TỔNG theo sku trong 1 ĐƠN.
    Với mỗi combo (đã biết BOM), tìm N lớn nhất sao cho TẤT CẢ sku thành tố có đủ số lượng
    theo đúng tỉ lệ (bội số nguyên) -> gộp N phần đó thành 1 dòng combo, trừ bớt số lượng/doanh
    thu tương ứng (phân bổ theo tỉ lệ) khỏi các sku thành tố. Phần dư (nếu có, VD mua thêm lẻ)
    vẫn giữ nguyên trong sku_agg để hiển thị như sản phẩm thường.
    Trả về list các dòng combo được ghép (sku, qty, revenue).
    """
    combo_rows = []
    for combo_sku, items in combo_bom_sorted:
        if not items:
            continue
        n_candidates = []
        feasible = True
        for comp_sku, qty_per in items:
            if qty_per <= 0:
                feasible = False
                break
            avail = sku_agg.get(comp_sku, {}).get("qty", 0)
            if avail < qty_per:
                feasible = False
                break
            n_candidates.append(int(avail // qty_per))
        if not feasible or not n_candidates:
            continue
        n = min(n_candidates)
        if n < 1:
            continue

        combo_revenue = 0.0
        for comp_sku, qty_per in items:
            comp = sku_agg[comp_sku]
            used_qty = n * qty_per
            revenue_share = comp["revenue"] * (used_qty / comp["qty"]) if comp["qty"] else 0.0
            comp["revenue"] -= revenue_share
            comp["qty"] -= used_qty
            if comp["qty"] <= 1e-9:
                del sku_agg[comp_sku]
            combo_revenue += revenue_share

        combo_rows.append({"sku": combo_sku, "qty": n, "revenue": combo_revenue})
    return combo_rows


def build_product_breakdown(orders: list, variant_sku_map: dict, cost_map: dict) -> pd.DataFrame:
    """
    Bảng chi tiết theo NGÀY x SẢN PHẨM x KÊNH x SHOP/PAGE: số lượng bán, doanh thu, giá vốn,
    gross margin. Có cột "date" để dashboard lọc theo khoảng thời gian giống bảng "Theo kênh"
    (client tự cộng dồn lại theo khoảng ngày đã chọn, không cần gọi lại API).
    Dùng title trong line_items làm tên sản phẩm hiển thị, sku để join giá vốn.
    (total_fee KHÔNG áp dụng ở mức sản phẩm vì file Chi phí chỉ có granularity theo order.)

    Sapo tách 1 combo trong order thành NHIỀU line_items theo sản phẩm thành tố -> hàm này
    GHÉP LẠI thành 1 dòng combo (xem _reconcile_combo_lines + docstring module) để không mất
    view "đã bán bao nhiêu combo".
    """
    combo_bom = get_combo_bom()
    combo_names = get_combo_names()
    combo_bom_sorted = sorted(combo_bom.items(), key=lambda kv: -len(kv[1]))

    rows = []
    for o in orders:
        channel = o.get("source_name")
        shop_page = _extract_shop_page(o.get("tags"))
        date = _order_date(o)

        sku_agg = {}
        for li in o.get("line_items", []):
            variant_id = li.get("variant_id")
            sku = variant_sku_map.get(variant_id, "")
            qty = li.get("quantity", 0)
            revenue = li.get("price", 0) * qty
            title = li.get("title") or sku or "(không tên)"
            entry = sku_agg.setdefault(sku, {"qty": 0.0, "revenue": 0.0, "title": title})
            entry["qty"] += qty
            entry["revenue"] += revenue

        combo_rows = _reconcile_combo_lines(sku_agg, combo_bom_sorted) if combo_bom_sorted else []

        for cr in combo_rows:
            rows.append({
                "date": date,
                "channel": channel,
                "shop_page": shop_page,
                "product": combo_names.get(cr["sku"], f"Combo {cr['sku']}"),
                "sku": cr["sku"],
                "quantity": cr["qty"],
                "revenue": cr["revenue"],
                "cogs": cost_map.get(cr["sku"], 0.0) * cr["qty"],
            })

        for sku, agg in sku_agg.items():
            if agg["qty"] <= 1e-9:
                continue
            rows.append({
                "date": date,
                "channel": channel,
                "shop_page": shop_page,
                "product": agg["title"],
                "sku": sku,
                "quantity": agg["qty"],
                "revenue": agg["revenue"],
                "cogs": cost_map.get(sku, 0.0) * agg["qty"],
            })

    if not rows:
        return pd.DataFrame(columns=["date", "channel", "shop_page", "product", "sku", "quantity", "revenue", "cogs",
                                      "gross_margin_amount", "gross_margin_pct"])

    df = pd.DataFrame(rows)
    grouped = df.groupby(["date", "channel", "shop_page", "product", "sku"]).agg(
        quantity=("quantity", "sum"),
        revenue=("revenue", "sum"),
        cogs=("cogs", "sum"),
    ).reset_index()
    grouped["gross_margin_amount"] = grouped["revenue"] - grouped["cogs"]
    grouped["gross_margin_pct"] = grouped.apply(
        lambda r: round(r["gross_margin_amount"] / r["revenue"] * 100, 1) if r["revenue"] else 0.0, axis=1
    )
    return grouped.sort_values(["date", "channel", "revenue"], ascending=[True, True, False]).reset_index(drop=True)


def build_daily_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    settlement_df: pd.DataFrame,
    fee_breakdown_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Giống build_summary nhưng tách thêm theo NGÀY (date) — dùng để dashboard
    lọc theo khoảng thời gian mà không cần gọi lại API mỗi lần đổi filter.
    Trả về: date, channel, shop_page, orders, gross_revenue, total_fee, fee_breakdown,
            net_revenue, cogs, gross_margin_amount, gross_margin_pct
    (fee_breakdown = dict {tên loại phí: số tiền}, cộng dồn theo group).
    """
    if not orders:
        return pd.DataFrame(columns=["date", "channel", "shop_page", "orders", "gross_revenue", "total_fee",
                                      "fee_breakdown", "net_revenue", "cogs", "gross_margin_amount", "gross_margin_pct"])

    fee_map_by_name, fee_map_by_order_number = _build_fee_maps(settlement_df)
    fee_breakdown_by_name, fee_breakdown_by_order_number = _build_fee_breakdown_maps(fee_breakdown_df)
    orders_df = _prep_orders_df(
        orders, fee_map_by_name, fee_map_by_order_number,
        fee_breakdown_by_name, fee_breakdown_by_order_number,
    )
    orders_df["date"] = orders_df.apply(_order_date, axis=1)
    orders_df["cogs"] = orders_df["line_items"].apply(
        lambda items: sum(_line_item_cost(li, variant_sku_map, cost_map) for li in items)
    )

    gross = orders_df.groupby(["date", "source_name", "shop_page"]).agg(
        gross_revenue=("total_price", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
        total_fee=("total_fee", "sum"),
    ).rename_axis(["date", "channel", "shop_page"]).reset_index()

    # Xem ghi chú tương tự trong build_summary() — dùng vòng lặp for thường, KHÔNG dùng
    # .groupby(...).apply()/gross.apply(axis=1), vì cả hai đều tự động convert/nở dict trả về
    # thành pd.Series hoặc nhiều cột, làm hỏng dữ liệu hoặc crash khi gán lại vào 1 cột.
    fee_breakdown_by_daily_group = {}
    for (d, ch, sp), grp in orders_df.groupby(["date", "source_name", "shop_page"]):
        fee_breakdown_by_daily_group[(d, ch, sp)] = _sum_fee_breakdowns(grp["fee_breakdown"])
    gross["fee_breakdown"] = pd.Series(
        [
            fee_breakdown_by_daily_group.get((d, ch, sp), {})
            for d, ch, sp in zip(gross["date"], gross["channel"], gross["shop_page"])
        ],
        index=gross.index, dtype=object,
    )

    gross["net_revenue"] = gross["gross_revenue"] - gross["total_fee"]
    gross["gross_margin_amount"] = gross["net_revenue"] - gross["cogs"]
    gross["gross_margin_pct"] = gross.apply(
        lambda r: round(r["gross_margin_amount"] / r["net_revenue"] * 100, 1) if r["net_revenue"] else 0.0, axis=1
    )

    cols = ["date", "channel", "shop_page", "orders", "gross_revenue", "total_fee", "fee_breakdown",
            "net_revenue", "cogs", "gross_margin_amount", "gross_margin_pct"]
    return gross[cols].sort_values(["date", "channel", "shop_page"]).reset_index(drop=True)
