# utils.py
import re
import json
import random
from typing import List, Dict, Optional, Union
from datetime import datetime
from urllib.parse import urlparse
from pathlib import Path

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

def mask_proxy(proxy: Optional[Union[str, Dict]]) -> str:
    """
    Trả về chuỗi proxy đã che mật khẩu. Hỗ trợ None | str | dict.
    
    Args:
        proxy: Proxy string, dict, or None
        
    Returns:
        Masked proxy string for logging
    """
    if not proxy:
        return "None"
    
    if isinstance(proxy, dict):
        proxy = proxy.get("http") or proxy.get("https") or ""
    
    try:
        parsed = urlparse(proxy)
        user = parsed.username or ""
        host = parsed.hostname or ""
        port = parsed.port or ""
        
        if user:
            return f"{host}:{port}:{user}:****"
        return f"{host}:{port}"
    except Exception:
        # fallback hiển thị thô nếu parse lỗi
        return str(proxy)

def log(*args, proxy: Optional[Union[str, Dict]] = None, error: Optional[Exception] = None, **kwargs) -> None:
    """
    Print có timestamp, kèm proxy nếu có. Nếu có exception 'error', in thêm stack trace.
    Ép tất cả args thành string để tránh lỗi với dict/object.
    
    Args:
        *args: Arguments to log
        proxy: Proxy information to mask and display
        error: Exception to display details for
        **kwargs: Additional keyword arguments for print
    """
    now = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    prefix = now
    
    if proxy is not None:
        prefix += f" [proxy={mask_proxy(proxy)}]"

    # Ép tất cả args thành chuỗi để tránh lỗi
    safe_args = [str(arg) for arg in args]
    print(prefix, *safe_args, **kwargs)

    if error:
        print(f"{prefix} ⚠️ Chi tiết lỗi:", repr(error))

def load_proxies(path: str = "proxies.txt") -> List[str]:
    """
    Đọc proxies từ file (mỗi dòng ip:port:user:pass).
    Trả về list chuỗi dạng http://user:pass@ip:port
    
    Args:
        path: Path to proxies file
        
    Returns:
        List of formatted proxy strings
        
    Raises:
        FileNotFoundError: If proxies file doesn't exist
        IOError: If there's an error reading the file
    """
    try:
        proxies = []
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                parts = line.split(":")
                if len(parts) == 4:
                    ip, port, user, pwd = parts
                    proxies.append(f"http://{user}:{pwd}@{ip}:{port}")
                elif len(parts) == 2:
                    ip, port = parts
                    proxies.append(f"http://{ip}:{port}")
                else:
                    log(f"⚠️ Dòng {line_num} có định dạng proxy không hợp lệ: {line}")
        
        return proxies
    except FileNotFoundError:
        log(f"❌ Không tìm thấy file proxies: {path}")
        return []
    except IOError as e:
        log(f"❌ Lỗi đọc file proxies {path}: {e}")
        return []

def random_proxy(path: str = "proxies.txt") -> Optional[Dict[str, str]]:
    """
    Lấy ngẫu nhiên 1 proxy từ file và trả về dict {"http": ..., "https": ...}
    
    Args:
        path: Path to proxies file
        
    Returns:
        Random proxy dict or None if no proxies available
    """
    proxies = load_proxies(path)
    if not proxies:
        return None
    
    proxy = random.choice(proxies)
    return {"http": proxy, "https": proxy}

def sanitize_filename(name: str, max_len: int = 120) -> str:
    """
    Sanitize filename by removing invalid characters and limiting length.
    
    Args:
        name: Original filename
        max_len: Maximum allowed length
        
    Returns:
        Sanitized filename safe for all operating systems
    """
    if not name:
        return "unnamed"
    
    # Loại bỏ ký tự không hợp lệ trên Windows/macOS/Linux
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '_', name)
    
    # Gom khoảng trắng dư
    name = re.sub(r'\s+', ' ', name).strip()
    
    # Cắt độ dài
    if len(name) > max_len:
        name = name[:max_len]
    
    # Đảm bảo không rỗng
    return name or "unnamed"

def build_image_filename(index: int, prompt: str, ext: str = "jpg", max_prompt_len: int = 80) -> str:
    """
    Tạo tên file dạng: '{index}_{prompt}.ext'
    
    Args:
        index: Số thứ tự (1-based)
        prompt: Nội dung prompt sẽ được sanitize
        ext: File extension
        max_prompt_len: Độ dài tối đa của prompt trong tên file
        
    Returns:
        Formatted filename
    """
    base = sanitize_filename(prompt, max_len=max_prompt_len)
    return f"{index}_{base}.{ext}"

def load_config(path: str = "config.json") -> Dict:
    """
    Load configuration from JSON file.
    
    Args:
        path: Path to config file
        
    Returns:
        Configuration dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file has invalid JSON
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log(f"❌ Không tìm thấy file config: {path}")
        raise
    except json.JSONDecodeError as e:
        log(f"❌ File config {path} có định dạng JSON không hợp lệ: {e}")
        raise

def format_proxy(proxy_str: str) -> Optional[Dict[str, str]]:
    """
    Chuyển định dạng proxy từ ip:port:user:pass sang dict dùng cho requests.
    
    Args:
        proxy_str: Proxy string in format ip:port:user:pass or ip:port
        
    Returns:
        Formatted proxy dict for requests or None if invalid format
    """
    if not proxy_str:
        return None
    
    try:
        parts = proxy_str.strip().split(":")
        
        if len(parts) == 4:
            ip, port, user, pwd = parts
            proxy_auth = f"http://{user}:{pwd}@{ip}:{port}"
        elif len(parts) == 2:
            ip, port = parts
            proxy_auth = f"http://{ip}:{port}"
        else:
            log(f"⚠️ Định dạng proxy không hợp lệ: {proxy_str}")
            return None

        return {
            "http": proxy_auth,
            "https": proxy_auth
        }
    except Exception as e:
        log(f"❌ Lỗi format proxy {proxy_str}: {e}")
        return None

def ensure_directory(path: Union[str, Path]) -> None:
    """
    Ensure directory exists, create if it doesn't.
    
    Args:
        path: Directory path
    """
    Path(path).mkdir(parents=True, exist_ok=True)

def get_random_user_agent() -> str:
    """
    Get random user agent from the pool.
    
    Returns:
        Random user agent string
    """
    return random.choice(USER_AGENTS)

def validate_file_path(file_path: str) -> bool:
    """
    Validate if file path exists and is accessible.
    
    Args:
        file_path: Path to validate
        
    Returns:
        True if file exists and is accessible
    """
    try:
        path = Path(file_path)
        return path.is_file() and path.exists()
    except Exception:
        return False

def safe_file_operation(func):
    """
    Decorator for safe file operations with error handling.
    
    Args:
        func: Function to decorate
        
    Returns:
        Decorated function with error handling
    """
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except FileNotFoundError as e:
            log(f"❌ File không tồn tại: {e}")
            return None
        except PermissionError as e:
            log(f"❌ Không có quyền truy cập file: {e}")
            return None
        except IOError as e:
            log(f"❌ Lỗi I/O: {e}")
            return None
        except Exception as e:
            log(f"❌ Lỗi không xác định: {e}")
            return None
    return wrapper

@safe_file_operation
def read_file_safe(path: str, encoding: str = "utf-8") -> Optional[str]:
    """
    Safely read file content with error handling.
    
    Args:
        path: File path
        encoding: File encoding
        
    Returns:
        File content or None if error
    """
    with open(path, "r", encoding=encoding) as f:
        return f.read()

@safe_file_operation
def write_file_safe(path: str, content: str, encoding: str = "utf-8") -> bool:
    """
    Safely write content to file with error handling.
    
    Args:
        path: File path
        content: Content to write
        encoding: File encoding
        
    Returns:
        True if successful, False otherwise
    """
    with open(path, "w", encoding=encoding) as f:
        f.write(content)
    return True
