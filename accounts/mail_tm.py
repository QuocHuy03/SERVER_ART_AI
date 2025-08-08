# accounts/mail_tm.py
import requests
import random
import string
import time
import re
from typing import Optional, Tuple, Dict, Any

MAILTM_API = "https://api.mail.tm"

def _ensure_proxies(proxies):
    return proxies if proxies else [None]

def _safe_request(method, url, headers=None, proxies=None, timeout=20, **kwargs):
    headers = headers or {}
    for proxy in _ensure_proxies(proxies):
        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                # proxies={"http": proxy, "https": proxy} if proxy else None,
                timeout=timeout,
                **kwargs
            )
            # print("[mail.tm]", resp.status_code, resp.text[:300])
            if resp.status_code in [200, 201, 202, 204, 400, 401, 404]:
                return resp
        except Exception as e:
            print("[mail.tm] Request error:", e)
            continue
    return None

def get_first_domain(proxies=None) -> Optional[str]:
    url = f"{MAILTM_API}/domains"
    headers = {"Accept": "application/ld+json"}
    r = _safe_request("GET", url, headers, proxies)
    if r and r.status_code == 200:
        try:
            data = r.json()
            domains = data.get("hydra:member", [])
            if domains:
                return domains[0].get("domain")
        except Exception as e:
            print("[mail.tm] Parse domain error:", e)
    return None

def _rand(n, alphabet):
    return ''.join(random.choices(alphabet, k=n))

def generate_email_password(domain: str) -> Tuple[str, str]:
    username = _rand(10, string.ascii_lowercase + string.digits)
    password = _rand(12, string.ascii_letters + string.digits)
    return f"{username}@{domain}", password

def create_account(email: str, password: str, proxies=None) -> bool:
    url = f"{MAILTM_API}/accounts"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/ld+json"
    }
    payload = {"address": email, "password": password}
    r = _safe_request("POST", url, headers, proxies, json=payload)
    return bool(r and r.status_code == 201)

def get_token(email: str, password: str, proxies=None) -> Optional[str]:
    url = f"{MAILTM_API}/token"
    headers = {"Content-Type": "application/json", "Accept": "application/ld+json"}
    payload = {"address": email, "password": password}
    r = _safe_request("POST", url, headers, proxies, json=payload)
    if r and r.status_code == 200:
        try:
            return r.json().get("token")
        except Exception:
            pass
    return None

def list_messages(token: str, page: int = 1, proxies=None) -> Dict[str, Any]:
    url = f"{MAILTM_API}/messages?page={page}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/ld+json"}
    r = _safe_request("GET", url, headers, proxies)
    if r and r.status_code == 200:
        return r.json()
    return {"hydra:member": []}

def get_message(token: str, msg_id: str, proxies=None) -> Optional[Dict[str, Any]]:
    url = f"{MAILTM_API}/messages/{msg_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/ld+json"}
    r = _safe_request("GET", url, headers, proxies)
    if r and r.status_code == 200:
        return r.json()
    return None

def wait_for_message(
    token: str,
    sender_contains: Optional[str] = None,
    subject_contains: Optional[str] = None,
    timeout_seconds: int = 180,
    poll_interval: int = 5,
    proxies=None
) -> Optional[Dict[str, Any]]:
    """
    Poll hộp thư cho đến khi thấy mail phù hợp hoặc hết thời gian.
    """
    deadline = time.time() + timeout_seconds
    seen_ids = set()

    while time.time() < deadline:
        data = list_messages(token, page=1, proxies=proxies)
        msgs = data.get("hydra:member", [])
        # print("msgs", msgs) hehe huydev
        for m in msgs:
            mid = m.get("id")
            if not mid or mid in seen_ids:
                continue
            seen_ids.add(mid)

            # Lọc theo sender/subject nếu có
            ok = True
            if sender_contains:
                frm = (m.get("from", {}) or {}).get("address", "") or ""
                ok = ok and (sender_contains.lower() in frm.lower())
            if subject_contains:
                subj = m.get("subject") or ""
                ok = ok and (subject_contains.lower() in subj.lower())

            if ok:
                # trả message đầy đủ
                full = get_message(token, mid, proxies=proxies)
                if full:
                    return full

        time.sleep(poll_interval)

    return None


def get_message_by_id(token: str, message_id: str, proxies=None):
    """Compat wrapper cho code đang gọi get_message_by_id."""
    return get_message(token, message_id, proxies=proxies)


def extract_magic_link_from_message(msg: Dict[str, Any]) -> Optional[str]:
    """
    Tìm link magic trong html/text. Ưu tiên artbreeder.com/login-with-magic-link hoặc awstrack.me chuyển tiếp.
    """
    html = msg.get("html", [])
    text = msg.get("text") or ""
    blob = " ".join(html) + "\n" + (text or "")

    # trực tiếp link artbreeder
    m = re.search(r'https?://(?:www\.)?artbreeder\.com/login-with-magic-link\?token=[A-Za-z0-9_-]+', blob)
    if m:
        return m.group(0)

    # link awstrack (SES)
    m2 = re.search(r'https?://[^\s"]*awstrack[^\s"]+', blob)
    if m2:
        return m2.group(0)

    # fallback: bất kì link artbreeder
    m3 = re.search(r'https?://(?:www\.)?artbreeder\.com/[^\s"]+', blob)
    if m3:
        return m3.group(0)

    return None
