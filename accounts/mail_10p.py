# accounts/mail_10p.py
import requests
import random
import string
import time
from typing import Optional, Dict, Any
import re
from html import unescape

BASE_API = "https://10minutemail.net"

def _rand(n, alphabet):
    return ''.join(random.choices(alphabet, k=n))

def generate_email_password(domain: str = None):
    """10minutemail không cần password, nhưng để tương thích API."""
    session_id = _rand(26, string.ascii_letters + string.digits)
    return session_id, None

def get_token(session_id: str, proxies=None) -> str:
    """Với 10minutemail, token chính là session_id."""
    return session_id

def get_mail_address(session_id: str, proxies=None) -> Optional[str]:
    api = f"{BASE_API}/address.api.php?sessionid={session_id}"
    try:
        r = requests.get(api, proxies=proxies, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get("mail_get_mail")
    except Exception as e:
        print("[10minutemail] get_mail_address error:", e)
    return None

def list_messages(session_id: str, proxies=None) -> Dict[str, Any]:
    api = f"{BASE_API}/address.api.php?sessionid={session_id}"
    try:
        r = requests.get(api, proxies=proxies, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print("[10minutemail] list_messages error:", e)
    return {"mail_list": []}

def get_message(session_id: str, mail_id: str, proxies=None) -> Optional[Dict[str, Any]]:
    api = f"{BASE_API}/mail.api.php?mailid={mail_id}&sessionid={session_id}"
    try:
        r = requests.get(api, proxies=proxies, timeout=15)
        if r.status_code == 200:
            data = r.json()
            # ✅ chuẩn hóa id để main dùng đồng nhất
            if isinstance(data, dict):
                data["id"] = mail_id
            return data
    except Exception as e:
        print("[10minutemail] get_message error:", e)
    return None

def wait_for_message(
    session_id: str,
    sender_contains: Optional[str] = None,
    subject_contains: Optional[str] = None,
    timeout_seconds: int = 180,
    poll_interval: int = 5,
    proxies=None
) -> Optional[Dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    seen_ids = set()

    print(f"[10MM] Bắt đầu chờ mail, timeout={timeout_seconds}s")

    while time.time() < deadline:
        data = list_messages(session_id, proxies=proxies)
        # print(f"[10MM] 📥 Raw data từ list_messages:\n{json.dumps(data, indent=2, ensure_ascii=False)}")

        msgs = data.get("mail_list", []) or []
        # print(f"[10MM] Số mail hiện có: {len(msgs)}")

        for m in msgs:
            # print(f"[10MM] 📧 Raw mail item:\n{json.dumps(m, indent=2, ensure_ascii=False)}")

            mid = m.get("mail_id")
            frm = m.get("from", "")
            subj = m.get("subject", "")

            print(f"[10MM] -> mail_id={mid}, from={frm}, subject={subj}")

            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)

            ok = True
            if sender_contains:
                ok = ok and (sender_contains.lower() in frm.lower())
            if subject_contains:
                ok = ok and (subject_contains.lower() in subj.lower())

            if ok:
                full = get_message(session_id, mid, proxies=proxies)
                if full:
                    full["id"] = mid
                    return full

        time.sleep(poll_interval)

    print(f"[10MM] ⏳ Hết thời gian chờ, không nhận được mail phù hợp")
    return None
''

def get_message_by_id(token: str, message_id: str, proxies=None):
    """Giữ API tương thích với mail_tm.py (token chính là session_id)."""
    if not message_id:
        return None
    return get_message(token, message_id, proxies=proxies)

def extract_magic_link_from_message(msg: Dict[str, Any]) -> Optional[str]:
    """
    Trích magic-link Artbreeder từ message của 10MinuteMail.
    - 10MM thường trả 'html' là list chuỗi; 'text' có thể None.
    - Ưu tiên bắt href để chắc kèo.
    """
    

    # Hợp nhất nguồn nội dung
    html_field = msg.get("html")
    html_parts = []
    if isinstance(html_field, list):
        html_parts = [h for h in html_field if isinstance(h, str)]
    elif isinstance(html_field, str):
        html_parts = [html_field]

    text = msg.get("text") or msg.get("mail_text") or ""

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
