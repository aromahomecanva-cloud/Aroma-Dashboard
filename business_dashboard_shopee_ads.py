"""
Parser đọc file CSV export THỦ CÔNG từ Shopee Seller Centre (Dịch Vụ Hiển Thị Shopee
> "Tải dữ liệu" > "Số liệu thống kê chung"), mỗi file = ĐÚNG 1 NGÀY của ĐÚNG 1 shop
— xem shopee_ads_exports/README.md để biết quy trình xuất file.

Vì sao cần file thủ công thay vì gọi API: user chưa có Shopee Open Platform API access
(mới tạo tài khoản dev), nên tạm thời thao tác export tay qua Chrome rồi đọc file — khi
nào có API chính thức sẽ thay thế module này bằng client gọi API trực tiếp (không đổi
phần còn lại của pipeline vì output format load_shopee_ads_daily_by_channel() giữ nguyên).

Mỗi file export có phần HEADER (7 dòng đầu) chứa sẵn thông tin đáng tin cậy hơn cả tên
file (tên file có thể bị Windows/Chrome đổi dấu "/" thành "-" hoặc "_"):
    Tên gian hàng,Aroma Story - Góc Hương Thơm
    Khoảng thời gian,01/04/2026 - 01/04/2026
-> lấy TÊN SHOP và NGÀY trực tiếp từ đây, không parse tên file.

Nếu "Khoảng thời gian" là 1 khoảng NHIỀU NGÀY (2 vế khác nhau) -> BỎ QUA file đó kèm cảnh
báo, vì loại export này gộp toàn bộ campaign theo cả khoảng, không có breakdown theo ngày
(đã kiểm chứng thực tế — xem ghi chú trong shopee_ads_exports/README.md).

Cột "Chi phí" (trong bảng dữ liệu, mỗi dòng = 1 chiến dịch "Dịch Vụ Hiển Thị") = chi phí
ads THUẦN (CHƯA gồm VAT) phát sinh cho chiến dịch đó trong đúng ngày export -> cộng dồn mọi
dòng trong 1 file = tổng chi phí THUẦN Shopee Ads ngày đó của shop đó.

QUAN TRỌNG — VAT: số "Chi phí" trên CHƯA gồm VAT. Số tiền THỰC TẾ Shopee trừ vào ví/phải
thanh toán = Chi phí * (1 + Config.SHOPEE_ADS_VAT_RATE) (VAT Shopee Ads = 8%, xác nhận từ
user 05/07/2026). Mọi hàm load_shopee_ads_*() trong module này trả về "spend" ĐÃ CỘNG VAT
(số thực chi), để khớp với dòng tiền thật khi đưa vào build_summary()/build_daily_summary().
Số thuần trước VAT vẫn giữ lại ở key "spend_before_vat" trong load_shopee_ads_daily() để
tiện đối chiếu/debug nếu cần.

Tên gian hàng lấy từ file KHỚP CHÍNH XÁC với shop_page mà
business_dashboard_aggregate._extract_shop_page() parse ra từ tags Sapo (dạng
"Shopee_<Tên shop>" -> "<Tên shop>"), VD "Aroma Story - Góc Hương Thơm",
"Aroma Story - The Art Of Scent" -> nhờ vậy có thể gán ads_spend TRỰC TIẾP vào đúng
shop_page, không cần suy luận/phân bổ theo tỷ lệ doanh thu như Meta Ads (Facebook/Instagram).
"""

import re
import datetime as dt
from pathlib import Path

import pandas as pd

from business_dashboard_config import Config

_HEADER_SHOP_RE = re.compile(r"^Tên gian hàng,(.+)$")
_HEADER_RANGE_RE = re.compile(r"^Khoảng thời gian,(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})$")
_HEADER_ROW_PREFIX = "Thứ tự,"

REQUIRED_COL = "Chi phí"
# Tên chiến dịch "Dịch Vụ Hiển thị" -- THƯỜNG đặt theo tên sản phẩm (VD "NẾN TOP 1 - V1",
# "Nước Hoa Nam - 12.8") nên dùng được để phân loại theo NHÓM NGÀNH HÀNG (categorizeProduct() ở
# dashboard_template/tail.html) khi so sánh hiệu quả ads theo cate -- xem load_shopee_ads_
# campaign_daily(). Không phải mã sản phẩm chuẩn (cột "Mã sản phẩm" hầu như luôn rỗng "-" trong
# thực tế), nên đây là cách suy luận GẦN ĐÚNG theo tên, không phải join chính xác theo SKU.
NAME_COL = "Tên Dịch vụ Hiển thị"
# "Doanh số" = doanh thu Shopee TỰ GHI NHẬN do quảng cáo mang lại (gồm cả chuyển đổi gián tiếp,
# KHÁC "Doanh số trực tiếp" chỉ tính chuyển đổi trực tiếp từ quảng cáo) -- dùng để tính CIR Ads
# (= Chi phí / Doanh thu do ads mang lại, theo yêu cầu Huy 25/09/2026, về bản chất giống cột
# "ACOS" Shopee đã có sẵn trong file, nhưng ta tự tính lại để cộng dồn được theo khoảng ngày Huy
# chọn trên dashboard thay vì chỉ xem được đúng 1 ngày/file). Cột này KHÔNG BẮT BUỘC phải có
# (file cũ/định dạng khác có thể thiếu) -- nếu thiếu thì ads_revenue = 0, KHÔNG làm fail cả file.
REVENUE_COL = "Doanh số"


def _dmy_to_iso(s: str) -> str:
    d, m, y = s.split("/")
    return f"{y}-{m}-{d}"


def _parse_report_header(lines: list) -> tuple | None:
    """Đọc ~10 dòng đầu file, trả về (shop_name, date_from_iso, date_to_iso) hoặc None."""
    shop_name = None
    date_from = date_to = None
    for line in lines[:10]:
        line = line.strip()
        m = _HEADER_SHOP_RE.match(line)
        if m:
            shop_name = m.group(1).strip()
            continue
        m2 = _HEADER_RANGE_RE.match(line)
        if m2:
            date_from = _dmy_to_iso(m2.group(1))
            date_to = _dmy_to_iso(m2.group(2))
    if shop_name is None or date_from is None:
        return None
    return shop_name, date_from, date_to


def _find_header_row_index(lines: list) -> int | None:
    for i, line in enumerate(lines):
        if line.startswith(_HEADER_ROW_PREFIX):
            return i
    return None


def _read_one_export(path: Path) -> dict | None:
    """Đọc 1 file export. Trả về {"shop_name", "date", "spend", "spend_before_vat", "rows",
    "file"} hoặc None nếu bỏ qua (lỗi định dạng / file range nhiều ngày / thiếu cột Chi phí).
    "spend" = spend_before_vat * (1 + Config.SHOPEE_ADS_VAT_RATE) — xem docstring module."""
    try:
        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        print(f"[Shopee Ads] Bỏ qua {path.name}: không đọc được file ({e}).")
        return None

    lines = raw_text.splitlines()
    header_info = _parse_report_header(lines)
    if header_info is None:
        print(f"[Shopee Ads] Bỏ qua {path.name}: không tìm thấy dòng 'Tên gian hàng'/"
              f"'Khoảng thời gian' hợp lệ trong header — có thể không phải file export gốc.")
        return None
    shop_name, date_from, date_to = header_info

    if date_from != date_to:
        print(f"[Shopee Ads] Bỏ qua {path.name}: export GỘP NHIỀU NGÀY ({date_from} -> "
              f"{date_to}) — loại này không có breakdown theo ngày, cần export TỪNG NGÀY riêng.")
        return None

    header_row_idx = _find_header_row_index(lines)
    if header_row_idx is None:
        print(f"[Shopee Ads] Bỏ qua {path.name}: không tìm thấy dòng tiêu đề cột ('{_HEADER_ROW_PREFIX}...').")
        return None

    try:
        df = pd.read_csv(path, encoding="utf-8-sig", skiprows=header_row_idx)
    except Exception as e:
        print(f"[Shopee Ads] Bỏ qua {path.name}: lỗi đọc bảng dữ liệu ({e}).")
        return None

    if REQUIRED_COL not in df.columns:
        print(f"[Shopee Ads] Bỏ qua {path.name}: thiếu cột '{REQUIRED_COL}' "
              f"(cột hiện có: {list(df.columns)}).")
        return None

    spend_before_vat = float(pd.to_numeric(df[REQUIRED_COL], errors="coerce").fillna(0).sum())
    spend = spend_before_vat * (1 + Config.SHOPEE_ADS_VAT_RATE)

    # ads_revenue = doanh thu do Shopee Ads mang lại (cột "Doanh số") -- không nhân VAT (VAT chỉ
    # áp cho phí quảng cáo, không áp cho doanh thu bán hàng).
    if REVENUE_COL in df.columns:
        ads_revenue = float(pd.to_numeric(df[REVENUE_COL], errors="coerce").fillna(0).sum())
    else:
        ads_revenue = 0.0

    # Breakdown theo TỪNG DÒNG (~1 "Dịch Vụ Hiển thị" = gần giống 1 campaign/sản phẩm quảng cáo)
    # -- dùng cho so sánh hiệu quả ads theo NHÓM NGÀNH HÀNG (yêu cầu Huy 25/09/2026). Gộp theo
    # TÊN (nhiều dòng có thể trùng tên nếu Shopee tách theo vị trí hiển thị) để mỗi sản phẩm chỉ
    # có 1 dòng/file.
    campaigns = []
    if NAME_COL in df.columns:
        tmp = df[[NAME_COL, REQUIRED_COL] + ([REVENUE_COL] if REVENUE_COL in df.columns else [])].copy()
        tmp[REQUIRED_COL] = pd.to_numeric(tmp[REQUIRED_COL], errors="coerce").fillna(0)
        if REVENUE_COL in df.columns:
            tmp[REVENUE_COL] = pd.to_numeric(tmp[REVENUE_COL], errors="coerce").fillna(0)
        else:
            tmp[REVENUE_COL] = 0.0
        grouped = tmp.groupby(NAME_COL, as_index=False).sum(numeric_only=True)
        for _, row in grouped.iterrows():
            campaigns.append({
                "name": str(row[NAME_COL]),
                "spend": float(row[REQUIRED_COL]) * (1 + Config.SHOPEE_ADS_VAT_RATE),
                "ads_revenue": float(row[REVENUE_COL]),
            })

    return {
        "shop_name": shop_name,
        "date": date_from,
        "spend": spend,
        "spend_before_vat": spend_before_vat,
        "ads_revenue": ads_revenue,
        "campaigns": campaigns,
        "rows": len(df),
        "file": path.name,
    }


def load_shopee_ads_daily() -> pd.DataFrame:
    """
    Đọc TẤT CẢ file *.csv trong shopee_ads_exports/ (kể cả trong sub-folder theo shop, hoặc
    thả thẳng ở root), gộp + khử trùng lặp theo (shop_name, date).

    Nếu 1 (shop_name, date) xuất hiện ở NHIỀU file (tải trùng/tải lại) -> CẢNH BÁO và lấy
    giá trị Chi phí LỚN NHẤT trong số các bản trùng (đề phòng bản export sớm hơn được tải khi
    ngày CHƯA kết thúc nên chi phí bị thiếu so với bản tải sau).

    Trả về DataFrame: date (YYYY-MM-DD), shop_name, spend (ĐÃ CỘNG VAT — xem docstring module),
    spend_before_vat (số thuần, tiện đối chiếu/debug).
    """
    root = Config.SHOPEE_ADS_DIR
    cols = ["date", "shop_name", "spend", "spend_before_vat", "ads_revenue", "campaigns"]
    if not root.exists():
        return pd.DataFrame(columns=cols)

    files = sorted(set(root.glob("*.csv")) | set(root.glob("*/*.csv")))
    if not files:
        return pd.DataFrame(columns=cols)

    records = [r for r in (_read_one_export(f) for f in files) if r]
    if not records:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(records)

    dup_mask = df.duplicated(subset=["shop_name", "date"], keep=False)
    if dup_mask.any():
        for (shop, date), grp in df[dup_mask].groupby(["shop_name", "date"]):
            print(f"[Shopee Ads] Trùng ngày {date} - {shop}: {len(grp)} file "
                  f"({', '.join(grp['file'])}) -> lấy Chi phí LỚN NHẤT trong số đó.")

    # spend, spend_before_vat và ads_revenue đều lấy từ CÙNG 1 file (bản export có Chi phí lớn
    # nhất trong số các bản trùng) -- dùng idxmax trên "spend" rồi lấy đúng dòng đó thay vì "max"
    # riêng lẻ từng cột, để 3 số này LUÔN cùng 1 nguồn, không bị lệch cặp (VD lấy nhầm spend của
    # file A nhưng ads_revenue của file B).
    idx = df.groupby(["shop_name", "date"])["spend"].idxmax()
    agg = df.loc[idx, ["shop_name", "date", "spend", "spend_before_vat", "ads_revenue", "campaigns"]]
    return agg.sort_values(["shop_name", "date"]).reset_index(drop=True)


def load_shopee_ads_daily_by_channel() -> list:
    """
    Trả về [{"date", "channel": "shopee", "shop_page", "spend"}] — dùng làm
    ads_daily_fixed_rows truyền vào business_dashboard_aggregate.build_daily_summary().
    Khác với Meta (chỉ biết tổng theo kênh, phải suy luận/phân bổ theo tỷ lệ doanh thu),
    ở đây shop_page đã CHÍNH XÁC 100% (lấy trực tiếp từ file Shopee) -> gán thẳng.
    """
    daily = load_shopee_ads_daily()
    if daily.empty:
        return []
    return [
        {"date": r["date"], "channel": "shopee", "shop_page": r["shop_name"], "spend": r["spend"]}
        for _, r in daily.iterrows()
    ]


def load_shopee_ads_revenue_daily() -> list:
    """
    Trả về [{"date", "shop_name", "ads_revenue"}] -- doanh thu Shopee TỰ GHI NHẬN do quảng cáo
    mang lại (cột "Doanh số"), theo NGÀY x SHOP. Dùng để tính CIR Ads (= ads_spend / ads_revenue)
    ở dashboard (xem computeCirForRange() trong dashboard_template/tail.html) -- TÁCH RIÊNG khỏi
    load_shopee_ads_daily_by_channel() (chỉ trả spend, dùng cho build_daily_summary()) vì
    ads_revenue KHÔNG phải là 1 field mà pipeline doanh thu chính (business_dashboard_aggregate)
    hiểu/dùng tới -- nó CHỈ phục vụ riêng cho việc so sánh hiệu quả ads giữa các shop.
    """
    daily = load_shopee_ads_daily()
    if daily.empty:
        return []
    return [
        {"date": r["date"], "shop_name": r["shop_name"], "ads_revenue": r["ads_revenue"]}
        for _, r in daily.iterrows()
    ]


def load_shopee_ads_campaign_daily() -> list:
    """
    Trả về [{"date", "shop_name", "campaign_name", "spend", "ads_revenue"}] -- breakdown theo
    TỪNG "Dịch Vụ Hiển thị" (~1 dòng = 1 sản phẩm/campaign quảng cáo, xem NAME_COL) theo ngày x
    shop. Dùng để so sánh hiệu quả ads theo NHÓM NGÀNH HÀNG ở dashboard (categorizeProduct() ở
    dashboard_template/tail.html suy luận nhóm hàng từ campaign_name, vì tên "Dịch Vụ Hiển thị"
    Huy đặt thường trùng/gần tên sản phẩm) -- yêu cầu Huy 25/09/2026. Granular hơn
    load_shopee_ads_revenue_daily() (chỉ có tổng/ngày/shop, không tách theo sản phẩm).
    """
    daily = load_shopee_ads_daily()
    if daily.empty:
        return []
    out = []
    for _, r in daily.iterrows():
        for c in (r["campaigns"] or []):
            out.append({
                "date": r["date"],
                "shop_name": r["shop_name"],
                "campaign_name": c["name"],
                "spend": c["spend"],
                "ads_revenue": c["ads_revenue"],
            })
    return out


def load_shopee_ads_total_by_shop() -> list:
    """
    Trả về [{"channel": "shopee", "shop_page", "spend"}] — tổng LIFETIME (cộng dồn mọi ngày
    đã có dữ liệu) theo từng shop, dùng làm ads_spend_fixed_rows truyền vào
    business_dashboard_aggregate.build_summary().
    """
    daily = load_shopee_ads_daily()
    if daily.empty:
        return []
    totals = daily.groupby("shop_name")["spend"].sum().reset_index()
    return [
        {"channel": "shopee", "shop_page": r["shop_name"], "spend": float(r["spend"])}
        for _, r in totals.iterrows()
    ]


def gap_check(expected_start: str = "2026-04-01", expected_end: str | None = None) -> dict:
    """
    Kiểm tra NGÀY THIẾU cho từng shop trong khoảng [expected_start, expected_end] (mặc định
    end = hôm nay, giờ Việt Nam). Trả về {shop_name: [ngày thiếu dạng YYYY-MM-DD, ...]}.
    Dùng để tự kiểm tra tiến độ backfill — chạy trực tiếp: `python business_dashboard_shopee_ads.py`.
    """
    daily = load_shopee_ads_daily()
    end = expected_end or dt.date.today().isoformat()
    all_dates = pd.date_range(expected_start, end).strftime("%Y-%m-%d").tolist()

    result = {}
    shops = sorted(daily["shop_name"].unique()) if not daily.empty else []
    for shop in shops:
        have = set(daily.loc[daily["shop_name"] == shop, "date"])
        missing = [d for d in all_dates if d not in have]
        result[shop] = missing
    return result


if __name__ == "__main__":
    daily = load_shopee_ads_daily()
    print(f"Tổng số dòng (shop x ngày) đọc được: {len(daily)}")
    print(f"(VAT Shopee Ads áp dụng: {Config.SHOPEE_ADS_VAT_RATE:.0%})")
    if not daily.empty:
        print(daily.groupby("shop_name").agg(
            so_ngay=("spend", "count"),
            tong_chi_phi_truoc_vat=("spend_before_vat", "sum"),
            tong_chi_phi_sau_vat=("spend", "sum"),
        ))
    print()
    print("=== Kiểm tra ngày thiếu (mặc định từ 2026-04-01 đến hôm nay) ===")
    gaps = gap_check()
    if not gaps:
        print("(Chưa có shop nào có dữ liệu — kiểm tra lại thư mục shopee_ads_exports/.)")
    for shop, missing in gaps.items():
        if missing:
            preview = ", ".join(missing[:8])
            more = f" ... (+{len(missing) - 8} ngày nữa)" if len(missing) > 8 else ""
            print(f"- {shop}: THIẾU {len(missing)} ngày -> {preview}{more}")
        else:
            print(f"- {shop}: ĐỦ, không thiếu ngày nào trong khoảng kiểm tra.")
