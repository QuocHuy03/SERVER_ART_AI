# utils.py
import re
import random
from datetime import datetime
from urllib.parse import urlparse


# ===== User-Agent pool (bổ sung nếu muốn) =====
USER_AGENTS = [
    # Chrome Windows
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    # Chrome macOS
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
    # Edge
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0',
    # Firefox
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
]

def mask_proxy(p):
    """Trả về chuỗi proxy đã che mật khẩu. Hỗ trợ None | str | dict."""
    if not p:
        return "None"
    if isinstance(p, dict):
        p = p.get("http") or p.get("https") or ""
    try:
        u = urlparse(p)
        user = u.username or ""
        host = u.hostname or ""
        port = u.port or ""
        user_part = (user + ":****") if user else ""
        # ip:port:user:****
        if user_part:
            return f"{host}:{port}:{user}:****"
        return f"{host}:{port}"
    except Exception:
        # fallback hiển thị thô nếu parse lỗi
        return str(p)

def log(*args, proxy=None, **kwargs):
    """Print có timestamp, kèm proxy nếu truyền vào."""
    now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    prefix = now
    if proxy is not None:
        prefix += f" [proxy={mask_proxy(proxy)}]"
    print(prefix, *args, **kwargs)

def load_proxies(path="proxies.txt"):
    """
    Đọc proxies từ file (mỗi dòng ip:port:user:pass).
    Trả về list chuỗi dạng http://user:pass@ip:port
    """
    proxies = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(":")
            if len(parts) == 4:
                ip, port, user, pwd = parts
                proxies.append(f"http://{user}:{pwd}@{ip}:{port}")
    return proxies

def random_proxy(path="proxies.txt"):
    """
    Lấy ngẫu nhiên 1 proxy từ file.
    """
    proxies = load_proxies(path)
    return random.choice(proxies) if proxies else None



def sanitize_filename(name: str, max_len: int = 80) -> str:
    """
    Biến prompt thành tên file an toàn.
    - Loại bỏ ký tự cấm: \ / * ? : " < > |
    - Rút gọn về max_len ký tự.
    """
    safe = re.sub('[\\\\/*?:"<>|]', "_", name)
    safe = re.sub(r"\s+", " ", safe).strip()  # gọn khoảng trắng
    return safe[:max_len].strip(" _")

def build_image_filename(index: int, prompt: str, ext: str = "jpg", max_prompt_len: int = 80) -> str:
    """
    Tạo tên file dạng: '{index}_{prompt}.ext'
    - index: số thứ tự (1-based)
    - prompt: nội dung prompt sẽ được sanitize
    """
    base = sanitize_filename(prompt, max_len=max_prompt_len)
    return f"{index}_{base}.{ext}"
