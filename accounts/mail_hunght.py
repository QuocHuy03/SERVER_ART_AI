# accounts/mail_hunght.py
import requests
import random
import string
import time
import re
from typing import Optional, Dict, Any, Tuple
from html import unescape

BASE_API = "http://hunght1890.com"

def _rand(n, alphabet):
    """Tạo chuỗi ngẫu nhiên với độ dài n từ alphabet"""
    return ''.join(random.choices(alphabet, k=n))

def _safe_request(method, url, headers=None, proxies=None, timeout=20, **kwargs):
    """Thực hiện request an toàn với proxy support"""
    headers = headers or {}
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            proxies=proxies,
            timeout=timeout,
            **kwargs
        )
        return resp
    except Exception as e:
        print(f"[hunght1890] Request error: {e}")
        return None

def generate_email_password(domain: str = None) -> Tuple[str, str]:
    """
    Tạo email và password cho hunght1890.com
    Domain mặc định là hunght1890.com
    """
    username = _rand(10, string.ascii_lowercase + string.digits)
    password = _rand(12, string.ascii_letters + string.digits)
    # Sử dụng domain mặc định hoặc domain được chỉ định
    email = f"{username}@{domain or 'hunght1890.com'}"
    return email, password

def get_token(email: str, password: str, proxies=None) -> str:
    """
    Với hunght1890.com, token chính là email
    Không cần authentication phức tạp
    """
    return email

def get_mail_address(email: str, proxies=None) -> Optional[str]:
    """Trả về địa chỉ email (đã có sẵn)"""
    return email

def list_messages(email: str, proxies=None) -> Dict[str, Any]:
    """
    Liệt kê tin nhắn từ hunght1890.com
    Sử dụng API endpoint để lấy danh sách mail
    """
    try:
        # hunght1890.com API endpoint để lấy mail
        api_url = f"{BASE_API}/{email}"
        
        r = _safe_request("GET", api_url, proxies=proxies)
        if r and r.status_code == 200:
            try:
                data = r.json()
                # Chuẩn hóa format để tương thích với main.py
                if isinstance(data, list) and len(data) > 0:
                    # Lấy mail mới nhất
                    latest_mail = data[0]
                    return {
                        "mail_list": [{
                            "mail_id": "latest",
                            "id": "latest",
                            "from": latest_mail.get("from", ""),
                            "subject": latest_mail.get("subject", ""),
                            "body": latest_mail.get("body", ""),
                            "html": [latest_mail.get("body", "")],
                            "text": latest_mail.get("body", "")
                        }]
                    }
                else:
                    return {"mail_list": []}
            except Exception as e:
                print(f"[hunght1890] Parse JSON error: {e}")
                return {"mail_list": []}
        else:
            print(f"[hunght1890] API error: {r.status_code if r else 'No response'}")
            return {"mail_list": []}
    except Exception as e:
        print(f"[hunght1890] list_messages error: {e}")
        return {"mail_list": []}

def get_message(email: str, mail_id: str, proxies=None) -> Optional[Dict[str, Any]]:
    """
    Lấy nội dung tin nhắn cụ thể từ hunght1890.com
    """
    try:
        api_url = f"{BASE_API}/{email}"
        
        r = _safe_request("GET", api_url, proxies=proxies)
        if r and r.status_code == 200:
            try:
                data = r.json()
                if isinstance(data, list) and len(data) > 0:
                    # Lấy mail mới nhất
                    latest_mail = data[0]
                    # Chuẩn hóa format để tương thích với main.py
                    message_data = {
                        "id": mail_id,
                        "mail_id": mail_id,
                        "from": latest_mail.get("from", ""),
                        "subject": latest_mail.get("subject", ""),
                        "body": latest_mail.get("body", ""),
                        "html": [latest_mail.get("body", "")],
                        "text": latest_mail.get("body", ""),
                        "mail_text": latest_mail.get("body", "")
                    }
                    return message_data
                else:
                    return None
            except Exception as e:
                print(f"[hunght1890] Parse message JSON error: {e}")
                return None
        else:
            print(f"[hunght1890] Get message API error: {r.status_code if r else 'No response'}")
            return None
    except Exception as e:
        print(f"[hunght1890] get_message error: {e}")
        return None

def wait_for_message(
    email: str,
    sender_contains: Optional[str] = None,
    subject_contains: Optional[str] = None,
    timeout_seconds: int = 180,
    poll_interval: int = 5,
    proxies=None
) -> Optional[Dict[str, Any]]:
    """
    Chờ tin nhắn phù hợp từ hunght1890.com
    """
    deadline = time.time() + timeout_seconds
    seen_ids = set()

    print(f"[hunght1890] Bắt đầu chờ mail cho {email}, timeout={timeout_seconds}s")

    while time.time() < deadline:
        data = list_messages(email, proxies=proxies)
        msgs = data.get("mail_list", []) or []

        for m in msgs:
            mid = m.get("mail_id") or m.get("id")
            frm = m.get("from", "")
            subj = m.get("subject", "")

            print(f"[hunght1890] -> mail_id={mid}, from={frm}, subject={subj}")

            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)

            # Lọc theo sender/subject nếu có
            ok = True
            if sender_contains:
                ok = ok and (sender_contains.lower() in frm.lower())
            if subject_contains:
                ok = ok and (subject_contains.lower() in subj.lower())

            if ok:
                full = get_message(email, mid, proxies=proxies)
                if full:
                    full["id"] = mid
                    return full

        time.sleep(poll_interval)

    print(f"[hunght1890] ⏳ Hết thời gian chờ, không nhận được mail phù hợp")
    return None

def get_message_by_id(email: str, message_id: str, proxies=None):
    """Giữ API tương thích với mail_tm.py"""
    if not message_id:
        return None
    return get_message(email, message_id, proxies=proxies)

def extract_magic_link_from_message(msg: Dict[str, Any]) -> Optional[str]:
    """
    Trích xuất magic-link Artbreeder từ message của hunght1890.com
    """
    # Hợp nhất nguồn nội dung
    html_field = msg.get("html")
    html_parts = []
    if isinstance(html_field, list):
        html_parts = [h for h in html_field if isinstance(h, str)]
    elif isinstance(html_field, str):
        html_parts = [html_field]

    text = msg.get("text") or msg.get("mail_text") or msg.get("body") or ""

    blob = unescape("\n".join(html_parts + ([text] if text else [])))

    # 1) Bắt trực tiếp href chứa artbreeder
    m = re.search(r'href="([^"]*artbreeder[^"]+)"', blob, flags=re.IGNORECASE)
    if m:
        return m.group(1)

    # 2) Các mẫu URL phổ biến
    patterns = [
        r'https?://(?:www\.)?artbreeder\.com/login-with-magic-link\?token=[A-Za-z0-9._\-]+',
        r'https?://(?:www\.)?artbreeder\.com/login\?(?:[^"\s]*)(?:loginToken|token)=[A-Za-z0-9._\-]+',
        r'https?://(?:www\.)?artbreeder\.com/[^\s"]*magic-link[^\s"]*',
        r'https?://[^\s"]*awstrack[^\s"]+',
        r'https?://(?:www\.)?artbreeder\.com/[^\s"]+',
    ]
    for p in patterns:
        m = re.search(p, blob, flags=re.IGNORECASE)
        if m:
            return m.group(0)

    return None

def create_account(email: str, password: str, proxies=None) -> bool:
    """
    hunght1890.com không cần tạo tài khoản trước
    Email được tạo tự động khi sử dụng
    """
    return True 