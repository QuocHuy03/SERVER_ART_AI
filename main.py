import os
import time
from accounts.mail_tm import (
    get_first_domain, generate_email_password, create_account,
    get_token, wait_for_message, extract_magic_link_from_message, get_message_by_id
)
from apis.artbreeder import (
    request_magic_link, follow_magic_link_and_get_cookie,
    submit_realtime_job, download_image
)
from utils import build_image_filename, random_proxy, log

# === CONFIG ===
PROMPTS_FILE = "data.txt"        
SAVE_DIR = "downloaded_images"   
BROWSER_TOKEN = "MTXFyddUTWQW5TGcdb9K"  
PROXIES = random_proxy("proxies.txt")                

# Tùy chọn lọc mail đến từ Artbreeder (không bắt buộc)
SENDER_CONTAINS = "noreply@artbreeder.com"
SUBJECT_CONTAINS = "Welcome to Artbreeder"  # hoặc "Verify" / "Magic" tùy mail template


# Retry logic
MAX_JOB_RETRIES = 3
RELOGIN_ON_ERRORS = {401, 402, 403}

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def read_prompts(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]

def new_artbreeder_session(proxies=None):
    """
    Tạo email mail.tm + đăng nhập bằng magic-link Artbreeder.
    Trả về connect.sid (str) hoặc None nếu lỗi.
    """
    domain = get_first_domain(proxies)
    if not domain:
        log("❌ Không lấy được domain mail.tm", proxy=PROXIES)
        return None

    email, password = generate_email_password(domain)
    log("📧 Email mới:", email, proxy=PROXIES)

    if not create_account(email, password, proxies):
        log("❌ Tạo tài khoản mail.tm thất bại")
        return None

    token = get_token(email, password, proxies)
    if not token:
        log("❌ Lấy token mail.tm thất bại")
        return None

    if not request_magic_link(email, proxies=proxies):
        log("❌ Gửi magic-link đến Artbreeder thất bại", proxy=PROXIES)
        return None

    log("⏳ Đã yêu cầu magic-link, chờ mail về...", proxy=PROXIES)
    msg = wait_for_message(
        token,
        sender_contains=SENDER_CONTAINS,
        subject_contains=SUBJECT_CONTAINS,
        timeout_seconds=240,
        poll_interval=5,
        proxies=proxies
    )
    if not msg:
        log("❌ Không nhận được email magic-link trong thời gian chờ", proxy=PROXIES)
        return None

    # ensure lấy full message (có html/text)
    full = get_message_by_id(token, msg.get("id"), proxies=proxies) or msg

    magic_link = extract_magic_link_from_message(full)
    if not magic_link:
        log("❌ Không trích xuất được magic-link từ mail", proxy=PROXIES)
        return None

    log("🔗 Magic link:", magic_link, proxy=PROXIES)
    connect_sid = follow_magic_link_and_get_cookie(magic_link, proxies=proxies)
    if not connect_sid:
        log("❌ Không lấy được connect.sid sau khi mở magic-link", proxy=PROXIES)
        return None

    log("✅ Đăng nhập OK. connect.sid =", connect_sid[:12] + "...", proxy=PROXIES)
    return connect_sid

def is_image_url_present(resp_json):
    return bool(isinstance(resp_json, dict) and resp_json.get("url"))

def need_relogin(resp_json):
    """
    Trả về True nếu response thể hiện lỗi cần re-login (401/402/403).
    """
    if not isinstance(resp_json, dict):
        return False
    code = resp_json.get("status")
    return code in RELOGIN_ON_ERRORS

def main():
    global PROXIES
    ensure_dir(SAVE_DIR)

    # Tạo phiên Artbreeder ban đầu
    connect_sid = new_artbreeder_session(PROXIES)
    if not connect_sid:
        return

    prompts = read_prompts(PROMPTS_FILE)
    total = len(prompts)
    if not total:
        log("⚠️ Không có prompt nào trong data.txt", proxy=PROXIES)
        return

    for i, prompt in enumerate(prompts, 1):
        log(f"[{i}/{total}] Gửi req job...", proxy=PROXIES)
        attempt = 0

        while attempt < MAX_JOB_RETRIES:
            attempt += 1

            job_resp = submit_realtime_job(
                prompt=prompt,
                connect_sid=connect_sid,
                browser_token=BROWSER_TOKEN,
                model_version="flux-dev",
                job_type="img2img",   # đổi "txt2img" nếu cần
                width=1252,
                height=832,
                strength=1.0,
                guidance_scale=3.5,
                num_steps=30,
                num_inference_steps=28,
                proxies=PROXIES
            )

            # Thành công: có URL ảnh
            if is_image_url_present(job_resp):
                image_url = job_resp["url"]
                filename = build_image_filename(i, prompt, ext="jpg", max_prompt_len=80)
                save_path = os.path.join(SAVE_DIR, filename)
                if download_image(image_url, save_path, PROXIES):
                    log(f"✓ Đã tải: {filename}")
                else:
                    log("— Tải ảnh thất bại.")
                break  # xong prompt này

            # In lỗi chi tiết
            log("— Không thấy image URL trong response:", job_resp)

            # Lỗi cần re-login → tạo account mới & retry lại prompt
            if need_relogin(job_resp):
                code = job_resp.get("status")
                # đổi proxy mới
                new_proxy = random_proxy("proxies.txt")
                log(f"🔁 Gặp lỗi {code} → đổi proxy & tạo tài khoản mới...", proxy=new_proxy)
                PROXIES = new_proxy
                connect_sid = new_artbreeder_session(PROXIES)
                if not connect_sid:
                    log("❌ Re-login thất bại. Bỏ qua prompt này.", proxy=PROXIES)
                    break
                continue

            # Không phải lỗi re-login → delay rồi thử lại
            time.sleep(2.0)
            if attempt < MAX_JOB_RETRIES:
                log("↻ Thử lại...")
                continue
            else:
                log("⏭️ Bỏ qua prompt do lỗi dai dẳng.")
                break

        # Nghỉ nhẹ tránh rate-limit
        time.sleep(1.2)

if __name__ == "__main__":
    main()