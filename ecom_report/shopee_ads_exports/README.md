# Shopee Ads — file export thủ công

Bỏ file CSV export từ Shopee Seller Centre ("Dịch vụ Hiển thị Shopee" > "Tải dữ liệu" > "Số liệu thống kê chung") vào đúng folder theo shop:

- `goc_huong_thom/` — shop "Aroma Story - Góc Hương Thơm" (tên đăng nhập: aromastory.official)
- `the_art_of_scent/` — shop "Aroma Story - The Art Of Scent" (tên đăng nhập: aromastory_official)

Tên file KHÔNG quan trọng với script (script chỉ đọc "Tên gian hàng" + "Khoảng thời gian" trong nội dung file, không parse tên file) — nhưng để thư mục gọn và tránh lỗi encoding Unicode tên file tiếng Việt (dấu `/`, `+` khi Windows/Chrome tự đổi), quy ước ĐẶT LẠI TÊN sau khi tải xong:

`YYYY-MM-DD.csv` (VD: `2026-07-04.csv`)

— tức xóa hết phần "Dữ liệu Dịch vụ Hiển thị Shopee-..." gốc, chỉ giữ ngày dạng ISO. Mỗi shop 1 ngày = 1 file duy nhất trong đúng folder của shop đó.

Có thể bỏ nhiều file (nhiều ngày) — script sẽ tự gộp và khử trùng lặp theo (shop, ngày) nếu lỡ có 2 file cùng ngày.

## Quy trình tải nhanh hơn (phát hiện thực tế 05/07/2026)

Tải 1 ngày xong -> **reload lại trang** -> đổi filter sang ngày kế tiếp -> tải tiếp — làm vậy thì KHÔNG bị giới hạn khoảng cách 30s-1p giữa các lần tải như khi tải liên tục không reload trang. Nhờ vậy backfill nhanh hơn nhiều.

Sau mỗi lần tải:
1. Cắt (move, không phải copy) file vừa tải thẳng vào đúng folder theo shop.
2. Kiểm tra ngay: mở file, đọc dòng "Tên gian hàng" (dòng 3) — PHẢI khớp đúng shop của folder chứa nó (Shopee đôi khi vẫn đang ở shop cũ nếu vừa đổi shop mà quên reload, xem vụ file 04/07/2026 bị lẫn shop ngày 05/07/2026).
3. Kiểm tra dòng "Khoảng thời gian" (dòng 6) — phải là 1 ngày (2 vế giống nhau), không phải khoảng nhiều ngày.
4. Kiểm tra không trùng ngày với file đã có trong folder, không thiếu ngày nào trong chuỗi đang tải.
5. Đổi tên file thành `YYYY-MM-DD.csv` (xem quy ước tên file ở trên).
6. Xóa file rác/sai/trùng ngay, không để lẫn vào folder chính (script `business_dashboard_shopee_ads.py` sẽ tự phát hiện phần lớn lỗi này khi chạy, nhưng dọn tay trước vẫn tốt hơn).

## Lưu ý: bash không xóa được file trong folder này

Do folder này đồng bộ qua OneDrive, các lệnh xóa file (`rm`, `os.remove`) chạy từ môi trường Claude thao tác thường bị lỗi "Operation not permitted" — RENAME thì vẫn chạy bình thường. Vì vậy khi dọn file trùng, Claude sẽ chuyển file thừa vào `_duplicates_to_delete/<shop>/` thay vì xóa hẳn — bạn tự xóa tay thư mục này khi tiện.
