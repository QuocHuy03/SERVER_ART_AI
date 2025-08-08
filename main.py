import os
import time
import threading
from accounts.mail_tm import (
    get_first_domain, generate_email_password, create_account,
    get_token, wait_for_message, extract_magic_link_from_message, get_message_by_id
)
from apis.artbreeder import (
    request_magic_link, follow_magic_link_and_get_cookie,
    submit_realtime_job, download_image, get_remaining_credits
)
from utils import build_image_filename, format_proxy, log, load_config
from auth.auth_guard import check_key_online, get_device_id
import sys

# === CONFIG ===
PROMPTS_FILE = "data.txt"
SAVE_DIR = "downloaded_images"
BROWSER_TOKEN = "MTXFyddUTWQW5TGcdb9K"
# PROXIES từng thread tự random, nên không khai báo global ở đây nữa

# Lọc mail Artbreeder (không bắt buộc)
SENDER_CONTAINS = "noreply@artbreeder.com"
SUBJECT_CONTAINS = "Welcome to Artbreeder"  # hoặc "Verify" / "Magic" tùy mail template

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
    # 🟡 Bỏ proxies tại đây
    domain = get_first_domain(None)
    if not domain:
        log("❌ Không lấy được domain mail.tm", proxy=proxies)
        return None

    email, password = generate_email_password(domain)
    log("📧 Email mới:", email, proxy=proxies)

    # 🟡 Bỏ proxies tại đây
    if not create_account(email, password, None):
        log("❌ Tạo tài khoản mail.tm thất bại", proxy=proxies)
        return None

    token = get_token(email, password, None)
    if not token:
        log("❌ Lấy token mail.tm thất bại", proxy=proxies)
        return None

    # ✅ Với Artbreeder vẫn dùng proxy (nếu muốn ẩn IP)
    if not request_magic_link(email, proxies=proxies):
        log("❌ Gửi magic-link đến Artbreeder thất bại", proxy=proxies)
        return None

    log("⏳ Đã yêu cầu magic-link, chờ mail về...", proxy=proxies)

    msg = wait_for_message(
        token,
        sender_contains=SENDER_CONTAINS,
        subject_contains=SUBJECT_CONTAINS,
        timeout_seconds=240,
        poll_interval=5,
        proxies=None
    )
    if not msg:
        log("❌ Không nhận được email magic-link trong thời gian chờ", proxy=proxies)
        return None

    # 🟡 bỏ proxy ở đây
    full = get_message_by_id(token, msg.get("id"), proxies=None) or msg

    magic_link = extract_magic_link_from_message(full)
    if not magic_link:
        log("❌ Không trích xuất được magic-link từ mail", proxy=proxies)
        return None

    log("🔗 Magic link:", magic_link, proxy=proxies)

    # Artbreeder nên dùng proxy để ẩn IP thật
    connect_sid = follow_magic_link_and_get_cookie(magic_link, proxies=proxies)
    if not connect_sid:
        log("❌ Không lấy được connect.sid sau khi mở magic-link", proxy=proxies)
        return None

    log("✅ Login cookies OK :.", connect_sid[:12] + "...", proxy=proxies)
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


def process_prompt(thread_id, index, prompt, connect_sid, proxies):
    attempt = 0
    while attempt < MAX_JOB_RETRIES:
        attempt += 1
        config = load_config()
        job_resp = submit_realtime_job(
            prompt=prompt,
            connect_sid=connect_sid,
            browser_token=BROWSER_TOKEN,
            model_version=config["model_version"],
            job_type=config["job_type"],
            width=config["width"],
            height=config["height"],
            strength=config["strength"],
            guidance_scale=config["guidance_scale"],
            num_steps=config["num_steps"],
            num_inference_steps=config["num_inference_steps"],
            proxies=proxies
        )

        if is_image_url_present(job_resp):
            image_url = job_resp["url"]
            filename = build_image_filename(index, prompt, ext="jpg", max_prompt_len=80)
            save_path = os.path.join(SAVE_DIR, filename)
            if download_image(image_url, save_path, proxies):
                log(f"[Thread {thread_id}] ✓ Đã tải: {filename}", proxy=proxies)
            else:
                log(f"[Thread {thread_id}] — Tải ảnh thất bại.", proxy=proxies)
            return True

        log(f"[Thread {thread_id}] — Không thấy image URL trong response:", job_resp, proxy=proxies)

        if need_relogin(job_resp):
            code = job_resp.get("status")
            log(f"[Thread {thread_id}] 🔁 Gặp lỗi {code}, cần re-login.", proxy=proxies)
            return False  # báo cần tạo session mới

        time.sleep(2.0)
        if attempt < MAX_JOB_RETRIES:
            log(f"[Thread {thread_id}] ↻ Thử lại...", proxy=proxies)
        else:
            log(f"[Thread {thread_id}] ⏭️ Bỏ qua prompt do lỗi dai dẳng.", proxy=proxies)
    return True


def thread_worker(thread_id, prompts_slice, proxies):
    max_session_retries = 10

    def try_create_session():
        for attempt in range(1, max_session_retries + 1):
            connect_sid = new_artbreeder_session(proxies)
            if connect_sid:
                return connect_sid
            log(f"[Thread {thread_id}] ❌ Lỗi tạo session mail.tm, thử lại lần {attempt}/{max_session_retries}", proxy=proxies)
            time.sleep(3)
        return None

    connect_sid = try_create_session()
    if not connect_sid:
        log(f"[Thread {thread_id}] ❌ Không tạo được session sau {max_session_retries} lần, dừng thread.", proxy=proxies)
        return

    total = len(prompts_slice)
    for idx_global, prompt in prompts_slice:
        credits = get_remaining_credits(connect_sid, proxies=proxies)
        log(f"[Thread {thread_id}] Credits nhận được: {credits}", proxy=proxies)

        if credits is None:
            log(f"[Thread {thread_id}] ⚠️ Không lấy được credits.", proxy=proxies)
        elif isinstance(credits, (int, float)) and credits <= 0:
            log(f"[Thread {thread_id}] 💸 Hết credit, tạo tài khoản mới...", proxy=proxies)
            connect_sid = try_create_session()
            if not connect_sid:
                log(f"[Thread {thread_id}] ❌ Không tạo được session mới sau {max_session_retries} lần, dừng thread.", proxy=proxies)
                break
        else:
            if not isinstance(credits, (int, float)):
                log(f"[Thread {thread_id}] ⚠️ Credits không phải số hợp lệ: {credits}", proxy=proxies)


        log(f"[Thread {thread_id}] [{idx_global}/{total}] Gửi req job...", proxy=proxies)
        success = process_prompt(thread_id, idx_global, prompt, connect_sid, proxies)

        if not success:
            connect_sid = try_create_session()
            if not connect_sid:
                log(f"[Thread {thread_id}] ❌ Không tạo được session mới, bỏ prompt.", proxy=proxies)
                continue
            success = process_prompt(thread_id, idx_global, prompt, connect_sid, proxies)
            if not success:
                log(f"[Thread {thread_id}] ❌ Thất bại sau khi tạo tài khoản mới, bỏ prompt.", proxy=proxies)

        time.sleep(1.2)



def chunk_list_with_index(lst, n):
    indexed = list(enumerate(lst, start=1))  # [(1, prompt1), (2, prompt2), ...]
    k, m = divmod(len(indexed), n)
    return (indexed[i*k + min(i, m):(i+1)*k + min(i+1, m)] for i in range(n))


def chunk_list(lst, n):
    k, m = divmod(len(lst), n)
    return (lst[i*k + min(i, m):(i+1)*k + min(i+1, m)] for i in range(n))


def load_proxies(path):
    with open(path, "r") as f:
        return [line.strip() for line in f if line.strip()]


def main_with_threads(num_threads=4):
    save_dir = input("📁 Nhập tên folder để lưu ảnh (mặc định: downloaded_images): ").strip()
    global SAVE_DIR
    SAVE_DIR = save_dir or "downloaded_images"


    ensure_dir(SAVE_DIR)
    prompts = read_prompts(PROMPTS_FILE)
    
    if not prompts:
        log("⚠️ Không có prompt nào trong data.txt")
        return

    proxies_list = load_proxies("proxies.txt")
    if not proxies_list:
        log("⚠️ Không có proxy nào trong proxies.txt")
        return

    # Giới hạn số luồng theo số proxy và prompt
    num_threads = min(num_threads, len(prompts), len(proxies_list))

    chunks = list(chunk_list_with_index(prompts, num_threads))
    threads = []
    for i in range(num_threads):
        proxy_str = proxies_list[i]
        formatted_proxy = format_proxy(proxy_str)
        if not formatted_proxy:
            log(f"⚠️ Proxy sai định dạng: {proxy_str}")
            continue

        t = threading.Thread(target=thread_worker, args=(i+1, chunks[i], formatted_proxy))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()



API_URL = "http://62.171.131.164:5000"


def center_line(text, width=70):
    return text.center(width)


def print_box(info):
    box_width = 70
    print("╔" + "═" * (box_width - 2) + "╗")
    print("║" + center_line("🔐 XÁC THỰC KEY THÀNH CÔNG", box_width - 2) + "║")
    print("╠" + "═" * (box_width - 2) + "╣")
    print("║" + center_line(f"🔑 KEY       : {info.get('key')}", box_width - 2) + "║")
    print("║" + center_line(f"📅 Hết hạn    : {info.get('expires')}", box_width - 2) + "║")
    print("║" + center_line(f"🔁 Số lượt    : {info.get('remaining')}", box_width - 2) + "║")
    print("╠" + "═" * (box_width - 2) + "╣")
    print("║" + center_line("🧠 Info dev @huyit32", box_width - 2) + "║")
    print("║" + center_line("📧 qhuy.dev@gmail.com", box_width - 2) + "║")
    print("╚" + "═" * (box_width - 2) + "╝")


if __name__ == "__main__":
    API_AUTH = f"{API_URL}/api/make_video_ai/auth"
    MAX_RETRIES = 5

    print("\n📌 XÁC THỰC KEY ĐỂ SỬ DỤNG CÔNG CỤ\n")

    for attempt in range(1, MAX_RETRIES + 1):
        key = input(f"🔑 Nhập API Key (Lần {attempt}/{MAX_RETRIES}): ").strip()
        success, message, info = check_key_online(key, API_AUTH)

        if success:
            print("\n" + message + "\n")
            print_box(info)
            print()

            run_now = input("▶️  Bạn có muốn chạy chương trình ngay bây giờ không? (Y/n): ").strip().lower()
            if run_now in ("", "y", "yes"):
                while True:
                    try:
                        num_threads = int(input("▶️ Nhập số luồng muốn chạy (0-10): ").strip())
                        if num_threads < 0 or num_threads > 10:
                            print("⚠️ Số luồng phải nằm trong khoảng từ 0 đến 10.")
                            continue
                        break
                    except ValueError:
                        print("⚠️ Vui lòng nhập số nguyên hợp lệ.")

                main_with_threads(num_threads)

            else:
                print("✋ Bạn đã chọn không chạy chương trình. Thoát.")
            break
        else:
            print(f"\n❌ {message}")
            if attempt < MAX_RETRIES:
                print("↩️  Vui lòng thử lại...\n")
                time.sleep(1)
            else:
                print("\n🚫 Đã nhập sai quá 5 lần. Thoát chương trình.")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                print("🧠 Info dev @huyit32 | 📧 qhuy.dev@gmail.com")
                print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                sys.exit(1)
