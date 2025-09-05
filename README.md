# Artbreeder AI Server API

Server API để quản lý key và generate ảnh với Artbreeder.

## 🚀 Cài đặt

```bash
pip install -r requirements.txt
```

## 🎯 Chạy server

```bash
python start.py
```

Hoặc:

```bash
python server.py
```

Server chạy tại: `http://localhost:5000`

## 📚 API Endpoints

### Key Management
- `POST /api/key/generate` - Tạo key mới (không cần device_id)
- `POST /api/key/bind` - Bind key với device (1 device = 1 key)
- `POST /api/key/validate` - Validate key
- `POST /api/key/use` - Sử dụng 1 lượt
- `GET /api/key/info` - Thông tin key

### Image Generation
- `POST /api/generate-image` - Generate ảnh
- `GET /api/image-sizes` - Kích thước ảnh
- `GET /api/generated-images` - Ảnh đã tạo

### Admin
- `GET /api/admin/keys` - Liệt kê keys
- `GET /api/admin/usage` - Liệt kê usage

## 🎨 Generate ảnh

```bash
curl -X POST http://localhost:5000/api/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "key": "your_key",
    "device_id": "your_device_id",
    "prompt": "A beautiful sunset over mountains",
    "size_preset": "16:9",
    "server": 1,
    "seed": 12345
  }'
```

### Tham số server:
- `server: 1` - mail.tm (mặc định)
- `server: 2` - 10minutemail

## 📐 Kích thước ảnh

- `16:9` - 1280x720 (Landscape)
- `9:16` - 720x1280 (Portrait)

## 📁 Cấu trúc

```
ARTBREEDER_AI/
├── server.py          # Server chính
├── start.py           # Script khởi động
├── requirements.txt   # Dependencies
├── accounts/          # Mail providers
├── apis/             # Artbreeder API
├── utils.py          # Utilities
├── generated_images/ # Ảnh đã tạo
├── keys.csv          # Backup keys
└── artbreeder_keys.db # Database
```

## 🔧 Workflow sử dụng

### 1. Tạo key (admin)
```bash
curl -X POST http://localhost:5000/api/key/generate \
  -H "Content-Type: application/json" \
  -d '{
    "usage_limit": 100,
    "expiry_days": 30
  }'
```

### 2. Bind key với device (user login lần đầu)
```bash
curl -X POST http://localhost:5000/api/key/bind \
  -H "Content-Type: application/json" \
  -d '{
    "key": "your_key_here",
    "device_id": "device_123"
  }'
```

### 3. Sử dụng key
```bash
curl -X POST http://localhost:5000/api/generate-image \
  -H "Content-Type: application/json" \
  -d '{
    "key": "your_key_here",
    "device_id": "device_123",
    "prompt": "A beautiful sunset",
    "size_preset": "16:9",
    "server": 1
  }'
```

## 📋 Logic mới:
- ✅ **Key** - Tự tạo (không cần device_id)
- ✅ **Device** - Tự động lưu khi user bind key
- ✅ **1 máy = 1 key** - Mỗi device chỉ được 1 key duy nhất