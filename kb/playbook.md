# Playbook điều tra gian lận thẻ (bản nháp của nhóm, kb_version: draft-0)

> Bản nháp để chạy thử. Khi có Common Investigation Playbook chính thức của môn học, thay file này
> (giữ định dạng `## PB-xx Tiêu đề`) và đổi KB_VERSION trong .env.

## PB-01 Nguyên tắc chung khi xem hồ sơ
- Đọc điểm rủi ro, mức phân luồng và các yếu tố làm tăng rủi ro trước khi xem chi tiết.
- Không kết luận gian lận chỉ dựa vào điểm của model; luôn đối chiếu với lịch sử giao dịch của thẻ.
- Ghi lại nhận định và căn cứ trước khi đóng hoặc chuyển cấp hồ sơ.

## PB-02 Số tiền bất thường so với thói quen của thẻ
- So sánh số tiền giao dịch với số tiền trung bình 30 ngày của thẻ.
- Nếu số tiền gấp nhiều lần mức trung bình, kiểm tra loại merchant có phù hợp với mức chi tiêu đó không.
- Xem các giao dịch lớn khác trong 24 giờ gần nhất của cùng thẻ.

## PB-03 Nhiều giao dịch trong thời gian ngắn
- Đếm số giao dịch của thẻ trong 1 giờ và 24 giờ gần nhất.
- Chú ý chuỗi giao dịch nhỏ liên tiếp trước một giao dịch lớn (thử thẻ rồi rút tiền).
- Kiểm tra các giao dịch tiếp theo của thẻ sau thời điểm hồ sơ được tạo.

## PB-04 Giao dịch vào giờ khuya
- Xác định giao dịch có nằm trong khoảng 22h–6h không.
- So sánh với thói quen giao dịch theo giờ của thẻ trong 30 ngày.
- Giao dịch khuya ở loại merchant trực tuyến cần được ưu tiên kiểm tra hơn.

## PB-05 Loại merchant hoặc merchant mới với thẻ
- Kiểm tra thẻ đã từng giao dịch ở loại merchant này trong 30 ngày chưa.
- Kiểm tra thẻ đã từng giao dịch với merchant này chưa.
- Loại merchant mới kết hợp số tiền lớn là dấu hiệu cần xác minh với chủ thẻ.

## PB-06 Khoảng cách địa lý bất thường
- Xem khoảng cách giữa nơi ở của chủ thẻ và merchant.
- So sánh với khoảng cách thường gặp trong lịch sử của thẻ.
- Khoảng cách lớn bất thường cần xác minh chủ thẻ có đang ở xa không.

## PB-07 Giao dịch mua sắm trực tuyến
- Với loại merchant trực tuyến (shopping_net, misc_net), kiểm tra tần suất và số tiền so với lịch sử.
- Chú ý giao dịch trực tuyến số tiền lớn ngay sau khoảng thời gian thẻ không hoạt động.

## PB-08 Xác minh với chủ thẻ
- Liên hệ chủ thẻ qua kênh đã đăng ký để xác nhận giao dịch (quy trình mô phỏng, hệ thống không tự liên hệ).
- Không tiết lộ thông tin thẻ đầy đủ trong quá trình xác minh.
- Ghi lại kết quả xác minh vào nhận định của hồ sơ.

## PB-09 Khi nào chuyển cấp hồ sơ
- Chuyển cấp cho Risk Lead khi hồ sơ có số tiền lớn và chưa xác minh được với chủ thẻ.
- Chuyển cấp khi phát hiện nhiều thẻ có cùng dấu hiệu bất thường trong thời gian ngắn.
- Ghi rõ lý do chuyển cấp.

## PB-10 Đóng hồ sơ
- Chỉ đóng hồ sơ khi đã có nhận định của chuyên viên kèm căn cứ.
- Phân biệt rõ đóng do xác nhận gian lận và đóng do giao dịch hợp lệ.
- Hệ thống không được tự đóng hồ sơ.

## PB-11 Hồ sơ không chấm được điểm
- Hồ sơ UNSCORED là do dữ liệu lỗi hoặc model không phản hồi, không có nghĩa là giao dịch an toàn.
- Đọc mã lỗi kiểm tra dữ liệu hoặc lỗi chấm điểm để biết nguyên nhân.
- Đánh giá thủ công dựa trên thông tin giao dịch còn lại và lịch sử thẻ.

## PB-12 Giới hạn của Case Brief
- Case Brief chỉ tóm tắt dữ liệu có sẵn và các bước gợi ý từ playbook.
- Brief không phải là kết luận; mọi quyết định do chuyên viên đưa ra.
- Nếu brief có nội dung không khớp dữ liệu, bỏ qua brief và báo lại cho nhóm vận hành.

## PB-13 Thẻ có ít lịch sử
- Thẻ có ít giao dịch trong 30 ngày thì các chỉ số so với thói quen kém tin cậy.
- Ưu tiên xác minh trực tiếp với chủ thẻ thay vì dựa vào so sánh lịch sử.

## PB-14 Nhiều tín hiệu cùng lúc
- Khi có từ ba tín hiệu trở lên (số tiền bất thường, giờ khuya, merchant mới, nhiều giao dịch ngắn hạn), ưu tiên xử lý hồ sơ trước.
- Kiểm tra xem các giao dịch khác của thẻ trong cùng ngày có cùng dấu hiệu không.
