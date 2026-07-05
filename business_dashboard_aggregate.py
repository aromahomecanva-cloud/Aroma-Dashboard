"""
Gộp dữ liệu từ Sapo (orders + variant->sku) + file product_costs.csv (giá vốn theo SKU)
+ Meta (ads spend) + Settlement (fees) thành 1 bảng tổng hợp theo từng kênh bán hàng,
tách thêm theo SHOP/PAGE cụ thể (parse từ field "tags" của order).

Công thức:
  gross_revenue      = DOANH THU THUẦN theo đúng công thức báo cáo "Doanh thu theo thời gian"
                       của Sapo (xem business_dashboard_revenue.py):
                       total_line_items_price - total_discounts - giá trị hàng trả lại (refunds)
                       (ĐÃ XÁC NHẬN khớp với file "Báo cáo doanh thu theo thời gian" user xuất
                       trực tiếp từ Sapo — trước đây dùng "total_price" là SAI vì total_price
                       là tổng tiền khách phải trả, không trừ hàng trả lại)
  cogs               = tổng (giá_vốn_theo_sku * quantity) theo kênh (join qua variant_id -> sku)
  total_fee          = tổng phí sàn + ship + voucher + aff + đồng tài trợ (từ file Chi phí Sapo)
  net_revenue        = gross_revenue - total_fee
  gross_margin_amount= net_revenue - cogs
  gross_margin_pct   = gross_margin_amount / net_revenue * 100

QUAN TRỌNG: orders truyền vào các hàm build_*() trong module này là orders GỐC, KHÔNG lọc bỏ
đơn huỷ (xem business_dashboard_revenue.py — đã đối chiếu thực nghiệm với báo cáo Sapo thật
và xác nhận Sapo KHÔNG loại đơn nào theo status/cancelled_on trong báo cáo doanh thu). Ngày
của order (_order_date()) PHẢI tính theo giờ Việt Nam (UTC+7), đã xác nhận qua đối chiếu
business_dashboard_debug_revenue.py.

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
import datetime as dt

import pandas as pd

from business_dashboard_costs import get_combo_bom, get_combo_names
from business_dashboard_revenue import order_revenue_breakdown, refund_events

_SHOP_TAG_RE = re.compile(r"^(?:Shopee|Tiktok|Lazada)_(.+)$", re.IGNORECASE)
_PAGE_TAG_RE = re.compile(r"^page_(?!id_)(.+)$", re.IGNORECASE)

# Cả Facebook VÀ Instagram trên thực tế CHỈ chạy Meta Ads cho ĐÚNG 1 page/shop cụ thể (Instagram
# gắn liền với Facebook Page đó qua Meta Business Suite, không phải 1 tài khoản Instagram tách
# biệt) — dù trong Sapo có thể có nhiều shop_page được gắn tag facebook/instagram (VD page phụ ít
# dùng, hoặc đơn không xác định được page cụ thể -> shop_page rỗng ""). Theo XÁC NHẬN của user:
# tài khoản Meta Ads (cả 2 kênh) hiện tại CHỈ chạy cho "Aroma Story - Nến Thơm Khắc Tên & Thông
# Điệp Ẩn" -> thay vì chia ads_spend theo tỷ lệ doanh thu qua nhiều shop_page trong kênh đó (sai
# lệch, vì các shop_page khác không hề có ads chạy cho chúng), ta CỐ ĐỊNH gán 100% ads_spend của
# MỖI kênh vào đúng page đó. Kênh nào KHÔNG có trong map này vẫn dùng cách phân bổ theo tỷ lệ
# doanh thu như cũ (dự phòng nếu sau này có thêm kênh ads khác không rõ page cụ thể).
FIXED_ADS_SHOP_PAGE_BY_CHANNEL = {
    "facebook": "Aroma Story - Nến Thơm Khắc Tên & Thông Điệp Ẩn",
    "instagram": "Aroma Story - Nến Thơm Khắc Tên & Thông Điệp Ẩn",
}


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


def _order_date(o) -> str:
    """
    Lấy ngày (YYYY-MM-DD) từ created_on của order, THEO GIỜ VIỆT NAM (UTC+7).

    ĐÃ XÁC NHẬN qua business_dashboard_debug_revenue.py (chạy ma trận đối chiếu với báo cáo
    "Doanh thu theo thời gian" thật của Sapo, 30 ngày, ~2537 đơn): dùng trực tiếp 10 ký tự đầu
    của created_on (giờ UTC thô) làm sai lệch NGÀY của rất nhiều đơn (tổng lệch 187 đơn/30 ngày,
    chỉ khớp đúng 2/30 ngày). Sau khi cộng thêm 7 tiếng (giờ VN) trước khi lấy ngày, kết quả
    khớp gần như tuyệt đối: khớp đúng 28/30 ngày, tổng lệch chỉ 2 đơn/2537 đơn.
    """
    created = o.get("created_on") or ""
    s = str(created).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        return str(created)[:10]
    vn_time = parsed + dt.timedelta(hours=7)
    return vn_time.strftime("%Y-%m-%d")


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
    """Tạo DataFrame từ orders, thêm cột shop_page (parse từ tags), total_fee (join 2 nhánh),
    fee_breakdown (dict chi tiết từng loại phí, join 2 nhánh tương tự total_fee), và
    gross_revenue (Doanh thu thuần, TÍNH TỪ orders GỐC — không dùng df.apply() vì hàm trả về
    dict sẽ bị pandas tự "nở" thành nhiều cột, xem ghi chú tương tự ở build_summary())."""
    df = pd.DataFrame(orders)
    if "tags" in df.columns:
        df["shop_page"] = df["tags"].apply(_extract_shop_page)
    else:
        df["shop_page"] = ""

    revenues = [order_revenue_breakdown(o) for o in orders]
    df["gross_revenue"] = [r["net_revenue"] for r in revenues]
    # before_refund = item_revenue - discount (CHƯA trừ refund) — dùng riêng cho
    # build_daily_summary(), vì refund_value phải gắn theo ngày CỦA CHÍNH sự kiện refund,
    # không phải ngày tạo đơn (xem business_dashboard_revenue.refund_events()).
    df["before_refund"] = [r["item_revenue"] - r["discount"] for r in revenues]

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


def _allocate_ads_spend(df: pd.DataFrame, ads_spend_by_channel: dict | None, revenue_col: str = "gross_revenue") -> pd.DataFrame:
    """
    Gán ads_spend (tổng theo KÊNH, từ Meta) vào từng dòng (channel, shop_page) — vì Meta chỉ
    cho biết tổng chi tiêu theo CAMPAIGN/KÊNH (facebook/instagram), không biết chia cho
    shop/page cụ thể nào trong Sapo. Quy ước phân bổ: chia theo TỶ LỆ doanh thu
    (revenue_col) của từng shop/page trong cùng kênh đó (shop/page đóng góp doanh thu nhiều
    hơn thì được cộng nhiều ads_spend hơn — cách phân bổ phổ biến, có thể tinh chỉnh sau khi
    có thêm dữ liệu ví dụ UTM/landing page cụ thể).
    Nếu kênh đó có ads_spend nhưng KHÔNG có dòng nào trong df (VD chưa từng có đơn) -> thêm
    1 dòng mới shop_page="" để không làm mất chi phí này.
    """
    if "ads_spend" not in df.columns:
        df["ads_spend"] = 0.0
    ads_spend_by_channel = ads_spend_by_channel or {}
    extra_rows = []
    for ch, total_spend in ads_spend_by_channel.items():
        if not total_spend:
            continue
        fixed_page = FIXED_ADS_SHOP_PAGE_BY_CHANNEL.get(ch)
        if fixed_page is not None:
            # Kênh này CHỈ chạy ads cho đúng 1 page -> gán thẳng 100%, KHÔNG chia theo doanh
            # thu (xem docstring FIXED_ADS_SHOP_PAGE_BY_CHANNEL).
            mask = (df["channel"] == ch) & (df["shop_page"] == fixed_page)
            if mask.any():
                df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + total_spend
            else:
                extra_rows.append({"channel": ch, "shop_page": fixed_page, "ads_spend": total_spend})
            continue
        mask = df["channel"] == ch
        if not mask.any():
            extra_rows.append({"channel": ch, "shop_page": "", "ads_spend": total_spend})
            continue
        rev_sum = df.loc[mask, revenue_col].sum()
        if rev_sum > 0:
            df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + df.loc[mask, revenue_col] / rev_sum * total_spend
        else:
            n = mask.sum()
            df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + total_spend / n
    if extra_rows:
        df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)
    return df


def _allocate_fixed_ads_spend_rows(df: pd.DataFrame, rows: list | None, date_col: str | None = None) -> pd.DataFrame:
    """
    Gán ads_spend TRỰC TIẾP cho đúng (channel, shop_page[, date]) đã biết CHÍNH XÁC — khác với
    _allocate_ads_spend()/_allocate_ads_spend_daily() (dùng cho Meta: chỉ biết tổng theo kênh,
    phải suy luận/phân bổ theo tỷ lệ doanh thu). Dùng cho Shopee Ads (xem
    business_dashboard_shopee_ads.py): mỗi shop có spend THẬT theo từng ngày, không cần phân bổ.

    rows: list[dict], mỗi dict có "channel", "shop_page", "spend", và thêm "date" nếu
    date_col được truyền (dùng cho build_daily_summary(); không có date thì dùng cho
    build_summary() — tổng lifetime).
    """
    if "ads_spend" not in df.columns:
        df["ads_spend"] = 0.0
    if not rows:
        return df
    extra_rows = []
    for row in rows:
        spend = row.get("spend", 0.0)
        if not spend:
            continue
        ch = row["channel"]
        sp = row["shop_page"]
        if date_col:
            d = row["date"]
            mask = (df[date_col] == d) & (df["channel"] == ch) & (df["shop_page"] == sp)
        else:
            mask = (df["channel"] == ch) & (df["shop_page"] == sp)
        if mask.any():
            df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + spend
        else:
            new_row = {"channel": ch, "shop_page": sp, "ads_spend": spend}
            if date_col:
                new_row[date_col] = d
            extra_rows.append(new_row)
    if extra_rows:
        df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)
    return df


def build_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    ads_data: dict,
    settlement_df: pd.DataFrame,
    fee_breakdown_df: pd.DataFrame | None = None,
    ads_spend_by_channel: dict | None = None,
    ads_spend_fixed_rows: list | None = None,
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
        gross_revenue=("gross_revenue", "sum"),
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

    # Ads spend giờ ĐÃ gán theo kênh facebook/instagram (từ Meta), phân bổ theo tỷ lệ
    # doanh thu cho từng shop/page trong kênh đó — xem _allocate_ads_spend(). Các kênh khác
    # (shopee/tiktokshop/...) không có trong ads_spend_by_channel -> vẫn = 0 như trước.
    summary["ads_spend"] = 0.0
    summary = _allocate_ads_spend(summary, ads_spend_by_channel, revenue_col="gross_revenue")
    summary = _allocate_fixed_ads_spend_rows(summary, ads_spend_fixed_rows)
    summary["fee_breakdown"] = summary["fee_breakdown"].apply(lambda v: v if isinstance(v, dict) else {})
    for col in ["gross_revenue", "cogs", "total_fee"]:
        summary[col] = summary[col].fillna(0)
    summary["orders"] = summary["orders"].fillna(0).astype(int)

    summary["net_revenue"] = summary["gross_revenue"] - summary["total_fee"]
    # Gross margin GIỜ ĐÃ TRỪ Ads spend ngay ở mức từng dòng (trước đây chỉ trừ ở mức tổng
    # trên dashboard) — để nhất quán với banner "Gross margin ở mọi nơi ĐÃ TRỪ Ads spend",
    # giờ ads_spend không còn luôn = 0 nữa (facebook/instagram đã có số thật).
    summary["gross_margin_amount"] = summary["net_revenue"] - summary["cogs"] - summary["ads_spend"]
    # LƯU Ý: net_revenue có thể = 0 khi 1 kênh (facebook/instagram) có ads_spend nhưng CHƯA có
    # đơn hàng nào khớp (dòng "extra_rows" từ _allocate_ads_spend) -> chia cho 0 sẽ ra
    # inf/-inf/NaN, mà json.dumps xuất "Infinity"/"-Infinity"/"NaN" là JSON KHÔNG HỢP LỆ, JS
    # JSON.parse() sẽ crash khi dashboard đọc data.json. Phải chặn chia 0 -> trả về 0.0.
    summary["gross_margin_pct"] = summary.apply(
        lambda r: round(r["gross_margin_amount"] / r["net_revenue"] * 100, 1) if r["net_revenue"] else 0.0, axis=1
    )
    summary["net_profit_after_ads"] = summary["gross_margin_amount"]  # giữ cột để tương thích cũ, không trừ thêm nữa

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


def _allocate_ads_spend_daily(df: pd.DataFrame, ads_daily_by_channel: list | None) -> pd.DataFrame:
    """Giống _allocate_ads_spend() nhưng theo TỪNG NGÀY x KÊNH (dùng
    business_dashboard_meta.get_ads_spend_daily_by_channel()). Nếu ngày/kênh đó có ads_spend
    nhưng KHÔNG có dòng đơn hàng nào (VD chạy ads nhưng hôm đó không có đơn) -> vẫn thêm 1
    dòng mới (shop_page="") để không làm mất chi phí, gross_revenue=0 nên gross_margin âm
    đúng bằng ads_spend hôm đó."""
    if "ads_spend" not in df.columns:
        df["ads_spend"] = 0.0
    if not ads_daily_by_channel:
        return df
    agg = {}
    for row in ads_daily_by_channel:
        key = (row["date"], row["channel"])
        agg[key] = agg.get(key, 0.0) + row["spend"]

    extra_rows = []
    for (date, ch), total_spend in agg.items():
        if not total_spend:
            continue
        fixed_page = FIXED_ADS_SHOP_PAGE_BY_CHANNEL.get(ch)
        if fixed_page is not None:
            # Kênh này CHỈ chạy ads cho đúng 1 page -> gán thẳng 100% của NGÀY đó vào đúng
            # page, KHÔNG chia theo doanh thu (xem docstring FIXED_ADS_SHOP_PAGE_BY_CHANNEL).
            mask = (df["date"] == date) & (df["channel"] == ch) & (df["shop_page"] == fixed_page)
            if mask.any():
                df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + total_spend
            else:
                extra_rows.append({"date": date, "channel": ch, "shop_page": fixed_page, "ads_spend": total_spend})
            continue
        mask = (df["date"] == date) & (df["channel"] == ch)
        if not mask.any():
            extra_rows.append({"date": date, "channel": ch, "shop_page": "", "ads_spend": total_spend})
            continue
        rev_sum = df.loc[mask, "gross_revenue"].sum()
        if rev_sum > 0:
            df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + df.loc[mask, "gross_revenue"] / rev_sum * total_spend
        else:
            n = mask.sum()
            df.loc[mask, "ads_spend"] = df.loc[mask, "ads_spend"] + total_spend / n
    if extra_rows:
        df = pd.concat([df, pd.DataFrame(extra_rows)], ignore_index=True)
    return df


def build_daily_summary(
    orders: list,
    variant_sku_map: dict,
    cost_map: dict,
    settlement_df: pd.DataFrame,
    fee_breakdown_df: pd.DataFrame | None = None,
    ads_daily_by_channel: list | None = None,
    ads_daily_fixed_rows: list | None = None,
) -> pd.DataFrame:
    """
    Giống build_summary nhưng tách thêm theo NGÀY (date) — dùng để dashboard
    lọc theo khoảng thời gian mà không cần gọi lại API mỗi lần đổi filter.
    Trả về: date, channel, shop_page, orders, gross_revenue, total_fee, fee_breakdown,
            net_revenue, cogs, gross_margin_amount, gross_margin_pct
    (fee_breakdown = dict {tên loại phí: số tiền}, cộng dồn theo group).

    QUAN TRỌNG: "Tiền hàng trả lại" (refund) được gắn vào NGÀY CỦA CHÍNH SỰ KIỆN REFUND
    (processed_at/created_on của object refund), KHÔNG phải ngày tạo đơn — ĐÃ XÁC NHẬN qua
    business_dashboard_debug_revenue.py._score_refund_date_attribution (lệch tổng chỉ
    35,000đ/78.5 triệu trên 30 ngày, so với lệch 6.3 triệu nếu dồn refund vào ngày tạo đơn).
    Một đơn có thể TẠO ngày X nhưng được HOÀN TIỀN vào ngày Y khác -> phải tách riêng phần
    "before_refund" (item_revenue - discount, gắn theo ngày tạo đơn) và phần "refund" (gắn
    theo ngày của chính sự kiện refund), rồi mới trừ lại với nhau theo (date, channel, shop_page).
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

    # Phần "trước hoàn" (item_revenue - discount) + orders/cogs/total_fee: vẫn gắn theo
    # ngày TẠO ĐƠN như trước (không liên quan tới vấn đề ngày refund).
    gross = orders_df.groupby(["date", "source_name", "shop_page"]).agg(
        revenue_before_refund=("before_refund", "sum"),
        orders=("id", "count"),
        cogs=("cogs", "sum"),
        total_fee=("total_fee", "sum"),
    ).rename_axis(["date", "channel", "shop_page"]).reset_index()

    # Phần "hoàn trả" (refund): gắn theo ngày CỦA CHÍNH sự kiện refund (fallback về ngày tạo
    # đơn nếu không parse được ngày refund), channel/shop_page lấy theo order chứa refund đó.
    refund_rows = []
    for o, order_date, channel, shop_page in zip(
        orders, orders_df["date"], orders_df["source_name"], orders_df["shop_page"]
    ):
        for refund_date, amt in refund_events(o):
            refund_rows.append({
                "date": refund_date or order_date,
                "channel": channel,
                "shop_page": shop_page,
                "refund_amount": amt,
            })
    if refund_rows:
        refund_df = pd.DataFrame(refund_rows).groupby(["date", "channel", "shop_page"]).agg(
            refund_amount=("refund_amount", "sum")
        ).reset_index()
    else:
        refund_df = pd.DataFrame(columns=["date", "channel", "shop_page", "refund_amount"])
        refund_df["refund_amount"] = refund_df["refund_amount"].astype(float)

    # Outer merge: 1 refund có thể rơi vào ngày KHÔNG có đơn nào được tạo (cho channel/shop đó)
    # -> vẫn cần xuất hiện dòng riêng cho ngày đó với gross_revenue âm.
    gross = gross.merge(refund_df, on=["date", "channel", "shop_page"], how="outer")
    gross["revenue_before_refund"] = gross["revenue_before_refund"].fillna(0.0).astype(float)
    gross["refund_amount"] = gross["refund_amount"].fillna(0.0).astype(float)
    gross["orders"] = gross["orders"].fillna(0).astype(int)
    gross["cogs"] = gross["cogs"].fillna(0.0).astype(float)
    gross["total_fee"] = gross["total_fee"].fillna(0.0).astype(float)
    gross["gross_revenue"] = gross["revenue_before_refund"] - gross["refund_amount"]

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

    # Ads spend theo NGÀY x KÊNH (facebook/instagram) từ Meta, phân bổ theo tỷ lệ doanh thu
    # cho shop/page trong cùng kênh + ngày đó — xem _allocate_ads_spend_daily().
    gross["ads_spend"] = 0.0
    gross = _allocate_ads_spend_daily(gross, ads_daily_by_channel)
    gross = _allocate_fixed_ads_spend_rows(gross, ads_daily_fixed_rows, date_col="date")
    gross["fee_breakdown"] = gross["fee_breakdown"].apply(lambda v: v if isinstance(v, dict) else {})
    for col in ["gross_revenue", "cogs", "total_fee"]:
        gross[col] = gross[col].fillna(0)
    gross["orders"] = gross["orders"].fillna(0).astype(int)

    gross["net_revenue"] = gross["gross_revenue"] - gross["total_fee"]
    # Gross margin GIỜ ĐÃ TRỪ Ads spend ngay ở mức từng dòng (xem ghi chú tương tự trong
    # build_summary()).
    gross["gross_margin_amount"] = gross["net_revenue"] - gross["cogs"] - gross["ads_spend"]
    gross["gross_margin_pct"] = gross.apply(
        lambda r: round(r["gross_margin_amount"] / r["net_revenue"] * 100, 1) if r["net_revenue"] else 0.0, axis=1
    )

    cols = ["date", "channel", "shop_page", "orders", "gross_revenue", "total_fee", "fee_breakdown",
            "net_revenue", "cogs", "gross_margin_amount", "gross_margin_pct", "ads_spend"]
    return gross[cols].sort_values(["date", "channel", "shop_page"]).reset_index(drop=True)
