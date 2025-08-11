import os
import time
import threading
import pandas as pd
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from accounts import mail_tm
from accounts import mail_10p as mail_10m
from apis.artbreeder import (
    request_magic_link, follow_magic_link_and_get_cookie,
    submit_realtime_job, download_image, get_remaining_credits
)
from utils import sanitize_filename, format_proxy, log, load_config
from auth.auth_guard import check_key_online
import sys
import json

# === CONSTANTS ===
SAVE_DIR = "downloaded_images"
BROWSER_TOKEN = "MTXFyddUTWQW5TGcdb9K"
SENDER_CONTAINS = "noreply@artbreeder.com"
SUBJECT_CONTAINS = "Welcome to Artbreeder"
MAX_JOB_RETRIES = 5
RELOGIN_ON_ERRORS = {401, 402, 403}
MAX_SESSION_RETRIES = 50
API_URL = "http://62.171.131.164:5000"

@dataclass
class ArtbreederConfig:
    """Configuration class for Artbreeder settings"""
    model_version: str = "flux-dev"
    job_type: str = "img2img"
    seed: int = 29830303
    width: int = 1600
    height: int = 896
    strength: float = 1.0
    guidance_scale: float = 3.5
    num_steps: int = 30
    num_inference_steps: int = 28

class ArtbreederSession:
    """Manages Artbreeder authentication session"""
    
    def __init__(self, proxies: Optional[Dict] = None, provider: str = "mail_tm"):
        self.proxies = proxies
        self.provider = provider
        self.connect_sid: Optional[str] = None
        
    def create_session(self) -> bool:
        """Create new Artbreeder session"""
        for attempt in range(1, MAX_SESSION_RETRIES + 1):
            self.connect_sid = self._new_artbreeder_session()
            if self.connect_sid:
                return True
            log(f"❌ Lỗi tạo session ({self.provider}), thử lại lần {attempt}/{MAX_SESSION_RETRIES}", proxy=self.proxies)
            time.sleep(3)
        return False
    
    def _new_artbreeder_session(self) -> Optional[str]:
        """Create email and login to Artbreeder using magic-link"""
        if self.provider == "mail_tm":
            return self._mail_tm_flow()
        else:
            return self._mail_10m_flow()
    
    def _mail_tm_flow(self) -> Optional[str]:
        """Mail.tm authentication flow"""
        domain = mail_tm.get_first_domain(None)
        if not domain:
            log("❌ Không lấy được domain mail.tm")
            return None

        email, password = mail_tm.generate_email_password(domain)
        log("📧 Email mới:", email)

        if not mail_tm.create_account(email, password):
            log("⚠️ Tạo tài khoản mail.tm thất bại hoặc đã tồn tại, thử login...")

        token = mail_tm.get_token(email, password)
        if not token:
            log("❌ Lấy token mail.tm thất bại")
            return None

        return self._complete_magic_link_flow(email, token, mail_tm)
    
    def _mail_10m_flow(self) -> Optional[str]:
        """10minutemail authentication flow"""
        session_id, _ = mail_10m.generate_email_password()
        
        # Wait for email to be ready
        email = None
        for _ in range(12):
            email = mail_10m.get_mail_address(session_id, proxies=self.proxies)
            if email:
                break
            time.sleep(1.0)

        if not email:
            log("❌ Không lấy được email từ 10minutemail", proxy=self.proxies)
            return None

        log("📧 Email mới (10min):", email, proxy=self.proxies)
        token = mail_10m.get_token(session_id, None)
        
        return self._complete_magic_link_flow(email, token, mail_10m)
    
    def _complete_magic_link_flow(self, email: str, token: str, mail_service) -> Optional[str]:
        """Complete magic link authentication flow"""
        if not request_magic_link(email, proxies=self.proxies):
            log("❌ Gửi magic-link đến Artbreeder thất bại", proxy=self.proxies)
            return None

        log("⏳ Đã yêu cầu magic-link, chờ mail về...", proxy=self.proxies)

        msg = mail_service.wait_for_message(
            token,
            sender_contains=SENDER_CONTAINS,
            subject_contains=SUBJECT_CONTAINS,
            timeout_seconds=600,
            poll_interval=5,
            proxies=self.proxies
        )
        
        if not msg:
            log("❌ Không nhận được email magic-link trong thời gian chờ", proxy=self.proxies)
            return None

        magic_link = self._extract_magic_link(msg, mail_service, token)
        if not magic_link:
            return None

        log("🔗 Magic link:", magic_link, proxy=self.proxies)
        connect_sid = follow_magic_link_and_get_cookie(magic_link, proxies=self.proxies)
        
        if not connect_sid:
            log("❌ Không lấy được connect.sid sau khi mở magic-link", proxy=self.proxies)
            return None

        log("✅ Login cookies OK :.", connect_sid[:12] + "...", proxy=self.proxies)
        return connect_sid
    
    def _extract_magic_link(self, msg: Dict, mail_service, token: str) -> Optional[str]:
        """Extract magic link from email message"""
        mid = msg.get("id") or msg.get("mail_id")
        full = mail_service.get_message_by_id(token, mid, proxies=self.proxies) or msg
        magic_link = mail_service.extract_magic_link_from_message(full)
        
        if not magic_link:
            # Try requesting again
            log("↻ Không trích xuất được magic-link, yêu cầu gửi lại...", proxy=self.proxies)
            if not request_magic_link(msg.get("email", ""), proxies=self.proxies):
                return None
                
            msg2 = mail_service.wait_for_message(
                token,
                sender_contains=SENDER_CONTAINS,
                subject_contains=None,
                timeout_seconds=300,
                poll_interval=5,
                proxies=self.proxies
            )
            
            if msg2:
                mid2 = msg2.get("id") or msg2.get("mail_id")
                full2 = mail_service.get_message_by_id(token, mid2, proxies=self.proxies) or msg2
                magic_link = mail_service.extract_magic_link_from_message(full2)
        
        return magic_link

class PromptProcessor:
    """Handles prompt processing and image generation"""
    
    def __init__(self, config: ArtbreederConfig):
        self.config = config
    
    def process_prompt(self, thread_id: int, stt: Any, prompt: str, 
                      connect_sid: str, proxies: Optional[Dict]) -> bool:
        """Process a single prompt and generate image"""
        for attempt in range(1, MAX_JOB_RETRIES + 1):
            job_resp = self._submit_job(prompt, connect_sid, proxies)
            
            if self._is_image_url_present(job_resp):
                return self._handle_successful_job(thread_id, stt, prompt, job_resp, proxies)
            
            log(f"[Thread {thread_id}] — Không thấy image URL trong response:", job_resp, proxy=proxies)
            
            if self._need_relogin(job_resp):
                code = job_resp.get("status")
                log(f"[Thread {thread_id}] 🔁 Gặp lỗi {code}, cần re-login.", proxy=proxies)
                return False
            
            time.sleep(2.0)
            if attempt < MAX_JOB_RETRIES:
                log(f"[Thread {thread_id}] ↻ Thử lại...", proxy=proxies)
            else:
                log(f"[Thread {thread_id}] ⏭️ Bỏ qua prompt do lỗi dai dẳng.", proxy=proxies)
        
        return True
    
    def _submit_job(self, prompt: str, connect_sid: str, proxies: Optional[Dict]) -> Dict:
        """Submit job to Artbreeder API"""
        return submit_realtime_job(
            prompt=prompt,
            connect_sid=connect_sid,
            browser_token=BROWSER_TOKEN,
            model_version=self.config.model_version,
            job_type=self.config.job_type,
            seed=self.config.seed,
            width=self.config.width,
            height=self.config.height,
            strength=self.config.strength,
            guidance_scale=self.config.guidance_scale,
            num_steps=self.config.num_steps,
            num_inference_steps=self.config.num_inference_steps,
            proxies=proxies
        )
    
    def _is_image_url_present(self, resp_json: Dict) -> bool:
        """Check if response contains image URL"""
        return bool(isinstance(resp_json, dict) and resp_json.get("url"))
    
    def _need_relogin(self, resp_json: Dict) -> bool:
        """Check if response indicates need for re-login"""
        if not isinstance(resp_json, dict):
            return False
        code = resp_json.get("status")
        return code in RELOGIN_ON_ERRORS
    
    def _handle_successful_job(self, thread_id: int, stt: Any, prompt: str, 
                              job_resp: Dict, proxies: Optional[Dict]) -> bool:
        """Handle successful job response"""
        image_url = job_resp["url"]
        try:
            stt_norm = int(float(stt)) if str(stt).replace('.', '', 1).isdigit() else stt
        except Exception:
            stt_norm = stt

        safe_prompt = sanitize_filename(str(prompt), max_len=100)
        filename = f"{stt_norm}_{safe_prompt}.jpg"
        save_path = os.path.join(SAVE_DIR, filename)

        if download_image(image_url, save_path, proxies):
            log(f"[Thread {thread_id}] ✓ Đã tải: {filename}", proxy=proxies)
            return True
        else:
            log(f"[Thread {thread_id}] — Tải ảnh thất bại.", proxy=proxies)
            return False

class ThreadWorker:
    """Manages individual thread execution"""
    
    def __init__(self, thread_id: int, prompts_slice: List[Tuple], 
                 proxies: Dict, provider: str, config: ArtbreederConfig):
        self.thread_id = thread_id
        self.prompts_slice = prompts_slice
        self.proxies = proxies
        self.provider = provider
        self.config = config
        self.processor = PromptProcessor(config)
    
    def run(self):
        """Main thread execution loop"""
        session = ArtbreederSession(self.proxies, self.provider)
        
        if not session.create_session():
            log(f"[Thread {self.thread_id}] ❌ Không tạo được session sau {MAX_SESSION_RETRIES} lần, dừng thread.", proxy=self.proxies)
            return
        
        total = len(self.prompts_slice)
        for pos_in_slice, (stt, prompt) in enumerate(self.prompts_slice, start=1):
            self._process_single_prompt(stt, prompt, pos_in_slice, total, session)
    
    def _process_single_prompt(self, stt: Any, prompt: str, pos_in_slice: int, 
                              total: int, session: ArtbreederSession):
        """Process a single prompt with retry logic"""
        retry_account = 0
        while retry_account < MAX_SESSION_RETRIES:
            if not self._check_credits(session):
                session = self._create_new_session()
                if not session:
                    retry_account += 1
                    continue
            
            log(f"[Thread {self.thread_id}] [{pos_in_slice}/{total}] Gửi req job...", proxy=self.proxies)
            success = self.processor.process_prompt(
                self.thread_id, stt, prompt, session.connect_sid, self.proxies
            )
            
            if success:
                break
            else:
                log(f"[Thread {self.thread_id}] 🔁 Prompt lỗi, tạo tài khoản mới để chạy lại...", proxy=self.proxies)
                session = self._create_new_session()
                retry_account += 1
        
        if retry_account >= MAX_SESSION_RETRIES:
            log(f"[Thread {self.thread_id}] ❌ Bỏ prompt này sau {MAX_SESSION_RETRIES} lần thử.", proxy=self.proxies)
    
    def _check_credits(self, session: ArtbreederSession) -> bool:
        """Check remaining credits and handle low credits"""
        credits = get_remaining_credits(session.connect_sid, proxies=self.proxies)
        log(f"[Thread {self.thread_id}] Credits nhận được: {credits}", proxy=self.proxies)
        
        if credits is None:
            log(f"[Thread {self.thread_id}] ⚠️ Không lấy được credits.", proxy=self.proxies)
            return True
        
        if isinstance(credits, (int, float)) and credits <= 0:
            log(f"[Thread {self.thread_id}] 💸 Hết credit, cần tạo tài khoản mới...", proxy=self.proxies)
            return False
        
        return True
    
    def _create_new_session(self) -> Optional[ArtbreederSession]:
        """Create new session when needed"""
        new_session = ArtbreederSession(self.proxies, self.provider)
        if new_session.create_session():
            return new_session
        return None

class ArtbreederApp:
    """Main application class"""
    
    def __init__(self):
        self.config = self._load_config()
        self.save_dir = SAVE_DIR
    
    def _load_config(self) -> ArtbreederConfig:
        """Load configuration from file"""
        try:
            config_data = load_config()
            
            # Validate required fields
            required_fields = [
                'model_version', 'job_type', 'seed', 'width', 'height',
                'strength', 'guidance_scale', 'num_steps', 'num_inference_steps'
            ]
            
            missing_fields = [field for field in required_fields if field not in config_data]
            if missing_fields:
                raise ValueError(f"Missing required config fields: {missing_fields}")
            
            # Create config object
            return ArtbreederConfig(**config_data)
            
        except FileNotFoundError:
            log("❌ Không tìm thấy file config.json, sử dụng config mặc định")
            return ArtbreederConfig()
        except json.JSONDecodeError as e:
            log(f"❌ Lỗi parse JSON trong config.json: {e}, sử dụng config mặc định")
            return ArtbreederConfig()
        except ValueError as e:
            log(f"❌ Lỗi validation config: {e}, sử dụng config mặc định")
            return ArtbreederConfig()
        except Exception as e:
            log(f"❌ Lỗi không xác định khi load config: {e}, sử dụng config mặc định")
            return ArtbreederConfig()
    
    def setup_directories(self):
        """Setup necessary directories"""
        save_dir = input("📁 Nhập tên folder để lưu ảnh (mặc định: downloaded_images): ").strip()
        self.save_dir = save_dir or "downloaded_images"
        global SAVE_DIR
        SAVE_DIR = self.save_dir
        
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)
    
    def choose_mail_provider(self) -> str:
        """Let user choose email provider"""
        print("\n📧 Chọn nguồn mail dùng để nhận magic-link:")
        print("  1) mail.tm (tạo account + token)")
        print("  2) 10minutemail (session id)")
        
        while True:
            choice = input("➡️  Chọn (1/2, mặc định 1): ").strip()
            if choice in ("", "1", "2"):
                break
            print("⚠️ Vui lòng nhập 1 hoặc 2.")
        
        return "mail_tm" if choice in ("", "1") else "10minutemail"
    
    def get_prompts_file(self) -> str:
        """Get prompts file path from user"""
        print("\n📄 Nhập đường dẫn file prompts (.xlsx/.xls/.csv/.txt)")
        print(f"   Thư mục hiện tại: {os.getcwd()}")
        print("   Gợi ý: có thể kéo-thả file vào cửa sổ CMD để tự điền đường dẫn.")
        
        while True:
            prompts_path = input("➡️  Đường dẫn file: ").strip().strip('"').strip("'")
            if not prompts_path:
                print("⚠️ Vui lòng nhập đường dẫn hợp lệ.")
                continue
            if not os.path.isfile(prompts_path):
                print(f"⚠️ Không tìm thấy file: {prompts_path}")
                continue
            break
        
        return prompts_path
    
    def read_prompts(self, path: str, sheet_name=0) -> List[Tuple[str, str]]:
        """Read prompts from various file formats"""
        ext = os.path.splitext(path)[1].lower()

        def _normalize_stt(x):
            try:
                f = float(x)
                return str(int(f)) if f.is_integer() else str(f)
            except Exception:
                return str(x).strip()

        if ext in [".xlsx", ".xls"]:
            df = pd.read_excel(path, sheet_name=sheet_name, usecols=[0, 1])
            if df.empty:
                return []
            
            pairs = []
            for _, row in df.iterrows():
                stt_raw, prompt_raw = row.iloc[0], row.iloc[1]
                if pd.isna(stt_raw) or pd.isna(prompt_raw):
                    continue
                stt = _normalize_stt(stt_raw)
                prompt = str(prompt_raw).strip()
                if stt and prompt:
                    pairs.append((stt, prompt))
            return pairs

        elif ext == ".csv":
            df = pd.read_csv(path, usecols=[0, 1])
            if df.empty:
                return []
            
            pairs = []
            for _, row in df.iterrows():
                stt_raw, prompt_raw = row[0], row[1]
                if pd.isna(stt_raw) or pd.isna(prompt_raw):
                    continue
                stt = _normalize_stt(stt_raw)
                prompt = str(prompt_raw).strip()
                if stt and prompt:
                    pairs.append((stt, prompt))
            return pairs

        else:
            # .txt mỗi dòng là prompt -> tự đánh số
            with open(path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
            return [(str(i + 1), line) for i, line in enumerate(lines)]
    
    def load_proxies(self, path: str = "proxies.txt") -> List[str]:
        """Load proxies from file"""
        with open(path, "r") as f:
            return [line.strip() for line in f if line.strip()]
    
    def chunk_list(self, lst: List, n: int):
        """Split list into n chunks"""
        k, m = divmod(len(lst), n)
        return (lst[i*k + min(i, m):(i+1)*k + min(i+1, m)] for i in range(n))
    
    def run(self, num_threads: int = 4):
        """Main application execution"""
        self.setup_directories()
        
        provider = self.choose_mail_provider()
        print(f"✅ Đã chọn nguồn mail: {provider}")
        
        prompts_path = self.get_prompts_file()
        prompts = self.read_prompts(prompts_path)
        
        if not prompts:
            log("⚠️ Không có prompt nào trong file.")
            return
        
        print(f"📊 Tổng số prompt: {len(prompts)}")
        
        proxies_list = self.load_proxies("proxies.txt")
        if not proxies_list:
            log("⚠️ Không có proxy nào trong proxies.txt")
            return
        
        num_threads = min(num_threads, len(prompts), len(proxies_list))
        chunks = list(self.chunk_list(prompts, num_threads))
        
        threads = []
        for i in range(num_threads):
            proxy_str = proxies_list[i]
            formatted_proxy = format_proxy(proxy_str)
            if not formatted_proxy:
                log(f"⚠️ Proxy sai định dạng: {proxy_str}")
                continue
            
            worker = ThreadWorker(i+1, chunks[i], formatted_proxy, provider, self.config)
            t = threading.Thread(target=worker.run)
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()

# === UTILITY FUNCTIONS ===
def ensure_dir(path):
    """Ensure directory exists"""
    if not os.path.exists(path):
        os.makedirs(path)

def center_line(text, width=70):
    """Center text within given width"""
    return text.center(width)

def print_box(info):
    """Print authentication info in a formatted box"""
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

# === MAIN EXECUTION ===
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

                app = ArtbreederApp()
                app.run(num_threads)
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
