# 🎨 Artbreeder AI - Tự động tạo ảnh AI

Công cụ tự động tạo ảnh AI trên Artbreeder sử dụng Python với khả năng xử lý đa luồng và quản lý proxy.

## ✨ Tính năng chính

- 🔐 **Xác thực tự động**: Tạo email tạm thời và đăng nhập Artbreeder
- 📧 **Hỗ trợ nhiều provider**: mail.tm, 10minutemail, hunght1890.com
- 🚀 **Xử lý đa luồng**: Tạo nhiều ảnh đồng thời
- 🌐 **Quản lý proxy**: Hỗ trợ proxy rotation
- 📁 **Đa định dạng**: Hỗ trợ .xlsx, .csv, .txt
- ⚙️ **Cấu hình linh hoạt**: Tùy chỉnh tham số tạo ảnh

## 🚀 Cài đặt

### Yêu cầu hệ thống
- Python 3.8+
- Windows/macOS/Linux

### Cài đặt dependencies
```bash
pip install -r requirements.txt
```

## 📋 Cấu hình

### 1. File config.json
```json
{
    "model_version": "flux-dev",
    "job_type": "img2img",
    "seed": 29830303,
    "width": 1600,
    "height": 896,
    "strength": 1.0,
    "guidance_scale": 3.5,
    "num_steps": 30,
    "num_inference_steps": 28
}
```

**Lưu ý**: File `config.json` phải chứa chính xác các field trên. Nếu muốn cấu trúc phức tạp hơn, hãy copy từ `config.example.json` và chỉnh sửa.

### 2. File proxies.txt
```
ip1:port1:user1:pass1
ip2:port2:user2:pass2
```

### 3. File prompts
- **Excel (.xlsx/.xls)**: Cột A = STT, Cột B = Prompt
- **CSV**: Cột đầu = STT, Cột thứ 2 = Prompt  
- **TXT**: Mỗi dòng = 1 prompt (tự động đánh số)

## 🎯 Sử dụng

### Chạy chương trình
```bash
python main.py
```

### Quy trình hoạt động
1. **Xác thực API Key**: Nhập key để sử dụng
2. **Chọn thư mục lưu**: Nơi lưu ảnh đã tạo
3. **Chọn provider mail**: mail.tm, 10minutemail, hoặc hunght1890.com
4. **Chọn file prompts**: File chứa danh sách prompt
5. **Chọn số luồng**: Số luồng xử lý đồng thời (0-10)

## 🏗️ Kiến trúc code

```
ARTBREEDER_AI/
├── main.py              # Main application
├── config.json          # Configuration file
├── requirements.txt     # Dependencies
├── proxies.txt          # Proxy list
├── prompt.xlsx          # Sample prompts
├── accounts/            # Email providers
│   ├── mail_tm.py      # mail.tm integration
│   ├── mail_10p.py     # 10minutemail integration

│   └── mail_hunght.py  # hunght1890.com integration
├── apis/                # API integrations
│   └── artbreeder.py   # Artbreeder API
├── auth/                # Authentication
│   └── auth_guard.py   # API key validation
└── utils.py             # Utility functions
```

## 🔧 Tối ưu hóa đã thực hiện

### 1. **Cấu trúc code**
- Tách logic thành các class riêng biệt
- Sử dụng dataclass cho configuration
- Implement OOP principles

### 2. **Error handling**
- Xử lý lỗi file I/O an toàn
- Retry logic cho API calls
- Graceful degradation

### 3. **Performance**
- Type hints cho tất cả functions
- Lazy loading cho configuration
- Efficient proxy management

### 4. **Maintainability**
- Documentation đầy đủ
- Consistent naming conventions
- Modular architecture

## 📊 Hiệu suất

- **Tốc độ**: Xử lý 10+ prompts đồng thời
- **Độ ổn định**: Auto-retry và session management
- **Tài nguyên**: Tối ưu memory usage

## 🐛 Troubleshooting

### Lỗi thường gặp
1. **Không tạo được session**: Kiểm tra proxy và network
2. **Lỗi magic link**: Đợi lâu hơn hoặc thử lại
3. **Hết credit**: Tự động tạo tài khoản mới
4. **Config loading error**: Kiểm tra format của config.json

### Config errors
```bash
# Lỗi: TypeError: ArtbreederConfig.__init__() got an unexpected keyword argument '_comment'
# Giải pháp: Sử dụng config.json đơn giản hoặc copy từ config.example.json

# Lỗi: Missing required config fields
# Giải pháp: Đảm bảo config.json có đủ các field cần thiết
```

### Debug mode
```python
# Thêm vào main.py để debug
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 🤝 Đóng góp

1. Fork repository
2. Tạo feature branch
3. Commit changes
4. Push to branch
5. Tạo Pull Request

## 📄 License

MIT License - Xem file LICENSE để biết thêm chi tiết.

## 👨‍💻 Developer

- **Author**: @huyit32
- **Email**: qhuy.dev@gmail.com
- **GitHub**: [Profile](https://github.com/quochuy03)

## 🔄 Changelog

### v2.10.0
- Tăng số lần thử gửi magic-link từ 3 lên 10 lần
- Bỏ email và tạo mới nếu không nhận được mail sau 10 lần thử
- Tối ưu hóa delay giữa các lần thử (giảm từ 15s xuống 10s)
- Cải thiện logic retry để tránh lãng phí thời gian

### v2.9.0
- Loại bỏ Mailinator và YOPmail provider (không hoạt động)
- Cập nhật UI chọn provider (3 options)
- Đặt mail.tm làm mặc định (ổn định nhất)

### v2.8.0
- Loại bỏ Guerrilla Mail provider (không hoạt động)
- Cập nhật UI chọn provider (5 options)
- Đặt Mailinator làm mặc định (ổn định nhất)

### v2.7.0
- Loại bỏ temp-mail.org provider (không hoạt động)
- Cập nhật UI chọn provider (6 options)
- Đặt Guerrilla Mail làm mặc định (ổn định nhất)

### v2.6.0
- Thêm 3 provider mail FREE mới: Guerrilla Mail, Mailinator, YOPmail
- Cải thiện UI chọn provider (7 options)
- Đặt Guerrilla Mail làm mặc định (ổn định nhất)
- Tất cả provider mới đều hoàn toàn miễn phí

### v2.5.0
- Cải thiện logging chi tiết cho magic-link flow
- Thêm kiểm tra email hiện có trước và sau khi chờ
- Hiển thị nội dung message để debug
- Log chi tiết quá trình trích xuất magic-link
- Cải thiện proxy health check logging

### v2.4.0
- Sửa lỗi thiếu ảnh do conflict SAVE_DIR
- Cải thiện logic tạo và quản lý thư mục
- Thêm kiểm tra file sau khi download
- Cải thiện logging cho việc lưu ảnh

### v2.3.0
- Cải thiện retry logic cho magic-link requests
- Thêm proxy health checking
- Tối ưu hóa error handling và logging
- Cải thiện session creation với delay thông minh

### v2.2.0
- Thêm hỗ trợ hunght1890.com provider
- Cải thiện UI chọn provider (4 options)
- Tối ưu hóa error handling

### v2.1.0
- Cải thiện UI chọn provider
- Tối ưu hóa error handling

### v2.0.0
- Tái cấu trúc code hoàn toàn
- Thêm type hints
- Cải thiện error handling
- Tối ưu hóa performance

### v1.0.0
- Phiên bản đầu tiên
- Chức năng cơ bản
- Hỗ trợ đa luồng

---

⭐ **Nếu dự án hữu ích, hãy để lại star!** 