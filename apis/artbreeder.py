# apis/artbreeder.py
import requests
import json
import random
from typing import Optional
from utils import USER_AGENTS

ARTBREEDER_MAGIC_ENDPOINT = "https://www.artbreeder.com/register-or-login-with-magic-link"
ARTBREEDER_JOB_ENDPOINT = "https://www.artbreeder.com/api/realTimeJobs"


def _rand_ua() -> str:
    return random.choice(USER_AGENTS)

def _proxy_kwargs(proxies=None):
    if not proxies:
        return {}
    # proxies: "http://user:pass@host:port" hoặc dict {"http": "...", "https": "..."}
    if isinstance(proxies, str):
        return {"proxies": {"http": proxies, "https": proxies}}
    return {"proxies": proxies}

def request_magic_link(email: str, redirect_after: str = "https://www.artbreeder.com/account", proxies=None) -> bool:
    payload = {
        "email": email,
        "redirectAfterLogin": redirect_after,
        "referral": None
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": _rand_ua(),
        "Origin": "https://www.artbreeder.com",
        "Referer": "https://www.artbreeder.com/"
    }
    try:
        r = requests.post(
            ARTBREEDER_MAGIC_ENDPOINT,
            headers=headers,
            data=json.dumps(payload),
            timeout=20,
            **_proxy_kwargs(proxies)
        )
        # print("[magic-link]", r.status_code, r.text[:200])
        return r.status_code in (200, 201, 202)
    except Exception as e:
        print("[artbreeder] magic link error:", e)
        return False

def follow_magic_link_and_get_cookie(magic_url: str, proxies=None, timeout=30) -> Optional[str]:
    """
    Mở link xác thực => session sẽ có cookie connect.sid nếu thành công.
    """
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": _rand_ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.artbreeder.com/",
    })
    try:
        sess.get(
            magic_url,
            allow_redirects=True,
            timeout=timeout,
            **_proxy_kwargs(proxies)
        )
        cookie = sess.cookies.get("connect.sid")
        return cookie
    except Exception as e:
        print("[artbreeder] open magic link error:", e)
        return None

def submit_realtime_job(
    prompt: str,
    connect_sid: str,
    browser_token: str,
    model_version: str = "flux-dev",
    job_type: str = "img2img",
    width: int = 1252,
    height: int = 832,
    strength: float = 1.0,
    guidance_scale: float = 3.5,
    num_steps: int = 30,
    num_inference_steps: int = 28,
    proxies=None
) -> Optional[dict]:
    """
    Trả về JSON response của Artbreeder (có thể chứa URL ảnh hoặc job info) hoặc None.
    """
    ua = _rand_ua()
    headers = {
        # HTTP/2 pseudo-headers (:authority/:method...) không được requests gửi—giữ để reference.
        "accept": "application/json",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "en-US,en;q=0.9,vi;q=0.8",
        "content-type": "application/json",
        "cookie": f"connect.sid={connect_sid}",
        "origin": "https://www.artbreeder.com",
        "priority": "u=1, i",
        "referer": "https://www.artbreeder.com/tools/composer",
        "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": ua,
    }

    data = {
        "job": {
            "name": model_version,
            "data": {
                "jobType": job_type,
                "model_version": model_version,
                "guidance_scale": guidance_scale,
                "strength": strength,
                "seed": 6231525,
                "width": width,
                "height": height,
                "num_steps": num_steps,
                "num_inference_steps": num_inference_steps,
                "prompt": prompt,
                "output_format": "s3",
                "image_format": "jpeg"
            }
        },
        "environment": None,
        "browserToken": browser_token
    }

    try:
        r = requests.post(
            ARTBREEDER_JOB_ENDPOINT,
            headers=headers,
            data=json.dumps(data),
            timeout=60,
            **_proxy_kwargs(proxies)
        )
        # print("[job]", r.status_code, r.text[:300])
        if r.status_code == 200:
            return r.json()
        else:
            print("[artbreeder] job error:", r.status_code, r.text)
            # Trả về để debug khi cần
            try:
                return {"status": r.status_code, "body": r.json()}
            except Exception:
                return {"status": r.status_code, "body": r.text}
    except Exception as e:
        print("[artbreeder] submit job error:", e)
    return None

def download_image(url: str, save_path: str, proxies=None) -> bool:
    try:
        resp = requests.get(url, timeout=60, **_proxy_kwargs(proxies))
        if resp.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True
        else:
            print("[artbreeder] download status:", resp.status_code)
    except Exception as e:
        print("[artbreeder] download error:", e)
    return False

def get_remaining_credits(connect_sid, proxies=None):
    """
    Lấy số credits còn lại của tài khoản Artbreeder hiện tại.
    """
    url = "https://www.artbreeder.com/beta/api/users/current-user/get-remaining-credits.json"
    headers = {
        "Cookie": f"connect.sid={connect_sid}",
        "Accept": "application/json",
    }

    try:
        resp = requests.post(url, headers=headers, proxies={"http": proxies, "https": proxies} if proxies else None, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                credits = data.get("data", {}).get("remainingCredits")
                return credits
    except Exception as e:
        print("Lỗi khi lấy credits:", e)

    return None