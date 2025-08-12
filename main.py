import os
import time
import threading
import pandas as pd
import requests
from typing import List, Tuple, Optional, Dict, Any
from dataclasses import dataclass
from accounts import mail_tm
from accounts import mail_10p as mail_10m
from accounts import mail_hunght

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
            log(f"🔄 Thử tạo session ({self.provider}) lần {attempt}/{MAX_SESSION_RETRIES}", proxy=self.proxies)
            
            self.connect_sid = self._new_artbreeder_session()
            if self.connect_sid:
                log(f"✅ Tạo session thành công lần {attempt}", proxy=self.proxies)
                return True
            
            log(f"❌ Lỗi tạo session ({self.provider}), thử lại lần {attempt}/{MAX_SESSION_RETRIES}", proxy=self.proxies)
            
            if attempt < MAX_SESSION_RETRIES:
                # Tăng delay theo số lần thử để tránh rate limiting
                delay = min(attempt * 2, 10)  # 2s, 4s, 6s, 8s, 10s (giảm delay)
                log(f"⏳ Chờ {delay}s trước khi thử lại...", proxy=self.proxies)
                time.sleep(delay)
        
        return False
    
    def _new_artbreeder_session(self) -> Optional[str]:
        """Create email and login to Artbreeder using magic-link"""
        if self.provider == "mail_tm":
            return self._mail_tm_flow()
        elif self.provider == "mail_10m":
            return self._mail_10m_flow()
        elif self.provider == "mail_hunght":
            return self._mail_hunght_flow()
        else:
            log(f"❌ Provider không được hỗ trợ: {self.provider}")
            return None
    
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
    
    def _mail_hunght_flow(self) -> Optional[str]:
        """Hunght1890.com authentication flow"""
        email, password = mail_hunght.generate_email_password()
        log("📧 Email mới (hunght1890):", email, proxy=self.proxies)
        
        # Hunght1890.com không cần tạo tài khoản trước
        token = mail_hunght.get_token(email, password)
        if not token:
            log("❌ Lấy token hunght1890.com thất bại", proxy=self.proxies)
            return None

        return self._complete_magic_link_flow(email, token, mail_hunght)
    
    def _request_magic_link_with_retry(self, email: str, max_retries: int = 10) -> bool:
        """Gửi magic-link với retry logic và delay - tăng lên 10 lần thử"""
        log(f"🚀 Bắt đầu gửi magic-link cho {email} với {max_retries} lần thử", proxy=self.proxies)
        
        for attempt in range(1, max_retries + 1):
            log(f"🔄 Thử gửi magic-link lần {attempt}/{max_retries} cho {email}", proxy=self.proxies)
            
            # Kiểm tra proxy health trước khi gửi
            log(f"🔍 Kiểm tra proxy health...", proxy=self.proxies)
            if not self._check_proxy_health():
                log(f"⚠️ Proxy có vấn đề, bỏ qua lần thử {attempt}", proxy=self.proxies)
                if attempt < max_retries:
                    time.sleep(10)  # Chờ lâu hơn nếu proxy có vấn đề
                continue
            
            log(f"✅ Proxy OK, gửi magic-link...", proxy=self.proxies)
            if request_magic_link(email, proxies=self.proxies):
                log(f"✅ Gửi magic-link thành công lần {attempt}", proxy=self.proxies)
                return True
            
            log(f"❌ Gửi magic-link thất bại lần {attempt}", proxy=self.proxies)
            
            if attempt < max_retries:
                # Tăng delay theo số lần thử
                delay = min(attempt * 3, 15)  # 3s, 6s, 9s, 12s, 15s (max 15s)
                log(f"⏳ Chờ {delay}s trước khi thử lại...", proxy=self.proxies)
                time.sleep(delay)
        
        log(f"💥 Đã thử hết {max_retries} lần nhưng không thành công, bỏ email này", proxy=self.proxies)
        return False
    
    def _check_proxy_health(self) -> bool:
        """Kiểm tra proxy có hoạt động không"""
        try:
            # Test proxy với một request đơn giản
            test_url = "https://httpbin.org/ip"
            response = requests.get(test_url, proxies=self.proxies, timeout=10)
            return response.status_code == 200
        except Exception as e:
            log(f"⚠️ Proxy health check failed: {e}", proxy=self.proxies)
            return False
    
    def _complete_magic_link_flow(self, email: str, token: str, mail_service) -> Optional[str]:
        """Complete magic link authentication flow"""
        # Thử gửi magic-link với retry logic (10 lần)
        magic_link_sent = self._request_magic_link_with_retry(email)
        if not magic_link_sent:
            log("❌ Gửi magic-link đến Artbreeder thất bại sau 10 lần thử, bỏ email này", proxy=self.proxies)
            return None

        log("⏳ Đã yêu cầu magic-link, chờ mail về...", proxy=self.proxies)
        log(f"📧 Đang kiểm tra email: {email}", proxy=self.proxies)
        log(f"🔍 Tìm email từ: {SENDER_CONTAINS}", proxy=self.proxies)
        log(f"📋 Với subject chứa: {SUBJECT_CONTAINS}", proxy=self.proxies)

        # Kiểm tra email hiện có trước khi chờ
        log("🔍 Kiểm tra email hiện có...", proxy=self.proxies)
        try:
            current_messages = mail_service.list_messages(token, proxies=self.proxies)
            if current_messages:
                log(f"📬 Tìm thấy {len(current_messages)} email hiện có", proxy=self.proxies)
                for i, msg in enumerate(current_messages[:3]):  # Chỉ hiển thị 3 email đầu
                    sender = msg.get('from', 'Unknown')
                    subject = msg.get('subject', 'No subject')
                    log(f"   📧 Email {i+1}: {sender} - {subject}", proxy=self.proxies)
            else:
                log("📭 Không có email nào hiện có", proxy=self.proxies)
        except Exception as e:
            log(f"⚠️ Lỗi khi kiểm tra email hiện có: {e}", proxy=self.proxies)

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
            # Kiểm tra lại email hiện có để debug
            log("🔍 Kiểm tra lại email sau khi timeout...", proxy=self.proxies)
            try:
                all_messages = mail_service.list_messages(token, proxies=self.proxies)
                if all_messages:
                    log(f"📬 Tổng cộng có {len(all_messages)} email", proxy=self.proxies)
                    for i, msg_check in enumerate(all_messages):
                        sender = msg_check.get('from', 'Unknown')
                        subject = msg_check.get('subject', 'No subject')
                        date = msg_check.get('date', 'Unknown date')
                        log(f"   📧 Email {i+1}: {sender} - {subject} ({date})", proxy=self.proxies)
                else:
                    log("📭 Vẫn không có email nào", proxy=self.proxies)
            except Exception as e:
                log(f"⚠️ Lỗi khi kiểm tra email sau timeout: {e}", proxy=self.proxies)
            
            # Không nhận được email, bỏ email này
            log("❌ Không nhận được email magic-link, bỏ email này", proxy=self.proxies)
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
        log(f"🔍 Trích xuất magic-link từ message ID: {mid}", proxy=self.proxies)
        
        # Log thông tin message để debug
        log(f"📧 Message info: from={msg.get('from', 'Unknown')}, subject={msg.get('subject', 'No subject')}", proxy=self.proxies)
        
        full = mail_service.get_message_by_id(token, mid, proxies=self.proxies) or msg
        log(f"📄 Đã lấy message đầy đủ, length: {len(str(full)) if full else 0}", proxy=self.proxies)
        
        magic_link = mail_service.extract_magic_link_from_message(full)
        
        if magic_link:
            log(f"✅ Tìm thấy magic-link: {magic_link[:50]}...", proxy=self.proxies)
        else:
            log("❌ Không tìm thấy magic-link trong message", proxy=self.proxies)
            # Log nội dung message để debug
            if full:
                content = str(full)
                if len(content) > 200:
                    log(f"📝 Nội dung message (200 ký tự đầu): {content[:200]}...", proxy=self.proxies)
                else:
                    log(f"📝 Nội dung message: {content}", proxy=self.proxies)
            
            # Không trích xuất được magic-link, bỏ email này
            log("❌ Không trích xuất được magic-link, bỏ email này", proxy=self.proxies)
            return None
        
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
        
        # Sử dụng SAVE_DIR global để đảm bảo ảnh được lưu đúng chỗ
        save_path = os.path.join(SAVE_DIR, filename)
        
        # Đảm bảo thư mục tồn tại trước khi lưu
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            log(f"[Thread {thread_id}] 📁 Đảm bảo thư mục tồn tại: {os.path.dirname(save_path)}", proxy=proxies)
        except Exception as e:
            log(f"[Thread {thread_id}] ❌ Lỗi tạo thư mục: {e}", proxy=proxies)
            return False

        # Log thông tin lưu ảnh
        log(f"[Thread {thread_id}] 💾 Đang lưu ảnh: {filename} vào {save_path}", proxy=proxies)

        if download_image(image_url, save_path, proxies):
            # Kiểm tra file có thực sự được tạo không
            if os.path.exists(save_path):
                file_size = os.path.getsize(save_path)
                log(f"[Thread {thread_id}] ✅ Đã tải: {filename} vào {save_path} (kích thước: {file_size} bytes)", proxy=proxies)
                return True
            else:
                log(f"[Thread {thread_id}] ❌ File không tồn tại sau khi download: {save_path}", proxy=proxies)
                return False
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
                log(f"[Thread {self.thread_id}] 💸 Hết credit, tạo tài khoản mới...", proxy=self.proxies)
                session = self._create_new_session()
                if not session:
                    retry_account += 1
                    log(f"[Thread {self.thread_id}] ⚠️ Không tạo được session mới, thử lại lần {retry_account}", proxy=self.proxies)
                    time.sleep(5)  # Chờ trước khi thử lại
                    continue
            
            log(f"[Thread {self.thread_id}] [{pos_in_slice}/{total}] Gửi req job...", proxy=self.proxies)
            success = self.processor.process_prompt(
                self.thread_id, stt, prompt, session.connect_sid, self.proxies
            )
            
            if success:
                log(f"[Thread {self.thread_id}] ✅ Prompt thành công!", proxy=self.proxies)
                break
            else:
                log(f"[Thread {self.thread_id}] 🔁 Prompt lỗi, tạo tài khoản mới để chạy lại...", proxy=self.proxies)
                session = self._create_new_session()
                retry_account += 1
                
                if retry_account < MAX_SESSION_RETRIES:
                    log(f"[Thread {self.thread_id}] ⏳ Chờ 3s trước khi thử lại...", proxy=self.proxies)
                    time.sleep(3)
        
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
        
        # Đảm bảo thư mục mặc định tồn tại
        self._ensure_default_directory()
    
    def _ensure_default_directory(self):
        """Đảm bảo thư mục mặc định tồn tại"""
        # Khai báo global SAVE_DIR trước khi sử dụng
        global SAVE_DIR
        
        if not os.path.exists(SAVE_DIR):
            try:
                os.makedirs(SAVE_DIR, exist_ok=True)
                print(f"📁 Đã tạo thư mục mặc định: {SAVE_DIR}")
            except Exception as e:
                print(f"❌ Lỗi tạo thư mục mặc định: {e}")
                # Tạo thư mục trong thư mục hiện tại
                fallback_dir = "downloaded_images_fallback"
                os.makedirs(fallback_dir, exist_ok=True)
                SAVE_DIR = fallback_dir
                print(f"⚠️ Sử dụng thư mục fallback: {fallback_dir}")
    
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
        # Khai báo global SAVE_DIR trước khi sử dụng
        global SAVE_DIR
        
        save_dir = input("📁 Nhập tên folder để lưu ảnh (mặc định: downloaded_images): ").strip()
        self.save_dir = save_dir or "downloaded_images"
        
        # Cập nhật SAVE_DIR global để đảm bảo ảnh được lưu đúng chỗ
        SAVE_DIR = self.save_dir
        
        # Tạo thư mục nếu chưa tồn tại
        if not os.path.exists(self.save_dir):
            try:
                os.makedirs(self.save_dir, exist_ok=True)
                print(f"✅ Đã tạo thư mục: {self.save_dir}")
            except Exception as e:
                print(f"❌ Lỗi tạo thư mục: {e}")
                # Fallback về thư mục mặc định
                SAVE_DIR = "downloaded_images"
                os.makedirs(SAVE_DIR, exist_ok=True)
                print(f"⚠️ Sử dụng thư mục mặc định: {SAVE_DIR}")
        else:
            print(f"✅ Thư mục đã tồn tại: {self.save_dir}")
        
        print(f"📁 Ảnh sẽ được lưu vào: {SAVE_DIR}")
        
        # Kiểm tra và hiển thị thông tin thư mục
        self._show_directory_info()
    
    def _show_directory_info(self):
        """Hiển thị thông tin về thư mục lưu ảnh"""
        try:
            if os.path.exists(SAVE_DIR):
                # Đếm số file ảnh hiện có
                image_files = [f for f in os.listdir(SAVE_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
                total_size = sum(os.path.getsize(os.path.join(SAVE_DIR, f)) for f in image_files)
                
                print(f"📊 Thông tin thư mục {SAVE_DIR}:")
                print(f"   📁 Số file ảnh: {len(image_files)}")
                print(f"   💾 Tổng dung lượng: {total_size / (1024*1024):.2f} MB")
                
                if image_files:
                    print(f"   📋 File gần nhất: {image_files[-1]}")
            else:
                print(f"⚠️ Thư mục {SAVE_DIR} chưa tồn tại")
        except Exception as e:
            print(f"❌ Lỗi kiểm tra thư mục: {e}")
    
    def choose_mail_provider(self) -> str:
        """Let user choose email provider"""
        print("\n📧 Chọn nguồn mail dùng để nhận magic-link:")
        print("  1) mail.tm (tạo account + token)")
        print("  2) 10minutemail (session id)")
        print("  3) hunght1890.com (email tạm thời)")
        
        while True:
            choice = input("➡️  Chọn (1/2/3, mặc định 1): ").strip()
            if choice in ("", "1", "2", "3"):
                break
            print("⚠️ Vui lòng nhập 1, 2 hoặc 3.")
        
        if choice in ("", "1"):
            return "mail_tm"
        elif choice == "2":
            return "10minutemail"
        else:
            return "mail_hunght"
    
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
