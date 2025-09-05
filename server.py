#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Artbreeder AI Server API
Quản lý key, lượt sử dụng và thiết bị cho dịch vụ AI
"""

import os
import csv
import json
import time
import hashlib
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from flask import Flask, request, jsonify, g
from flask_cors import CORS
import threading
import uuid
import random

# Import Artbreeder modules
from apis.artbreeder import (
    request_magic_link, follow_magic_link_and_get_cookie,
    submit_realtime_job, download_image, get_remaining_credits
)
from utils import sanitize_filename, log
from accounts import mail_tm
from accounts import mail_10p as mail_10m

# === CONFIGURATION ===
DATABASE_FILE = "artbreeder_keys.db"
KEYS_CSV_FILE = "keys.csv"
DEFAULT_USAGE_LIMIT = 100  # Số lượt mặc định cho mỗi key
DEFAULT_EXPIRY_DAYS = 30   # Số ngày hết hạn mặc định

# Artbreeder configuration
BROWSER_TOKEN = "MTXFyddUTWQW5TGcdb9K"
SENDER_CONTAINS = "noreply@artbreeder.com"
SUBJECT_CONTAINS = "Welcome to Artbreeder"
MAX_JOB_RETRIES = 3
RELOGIN_ON_ERRORS = {401, 402, 403}
MAX_SESSION_RETRIES = 3

# Image size presets
IMAGE_SIZES = {
    "16:9": {"width": 1280, "height": 720, "name": "Landscape (16:9)"},
    "9:16": {"width": 720, "height": 1280, "name": "Portrait (9:16)"},
    "1:1": {"width": 1024, "height": 1024, "name": "Square (1:1)"}
}

# === DATACLASSES ===
@dataclass
class KeyInfo:
    """Thông tin key"""
    key: str
    device_id: str
    usage_limit: int
    usage_count: int
    created_at: str
    expires_at: str
    is_active: bool
    last_used: Optional[str] = None

@dataclass
class UsageRecord:
    """Bản ghi sử dụng"""
    id: str
    key: str
    device_id: str
    timestamp: str
    endpoint: str
    success: bool
    response_time: float
    error_message: Optional[str] = None

@dataclass
class DeviceInfo:
    """Thông tin thiết bị"""
    device_id: str
    key: str
    user_agent: str
    ip_address: str
    first_seen: str
    last_seen: str
    total_requests: int

@dataclass
class ArtbreederConfig:
    """Configuration class for Artbreeder settings"""
    model_version: str = "flux-dev"
    job_type: str = "img2img"
    seed: int = 29830303
    width: int = 1280
    height: int = 720
    strength: float = 1.0
    guidance_scale: float = 3.5
    num_steps: int = 30
    num_inference_steps: int = 28

@dataclass
class GenerationRequest:
    """Request để generate ảnh"""
    prompt: str
    size_preset: str = "16:9"
    seed: Optional[int] = None
    model_version: str = "flux-dev"
    strength: float = 1.0
    guidance_scale: float = 3.5
    server: int = 1  # 1=mail.tm, 2=10minutemail

# === DATABASE MANAGEMENT ===
class DatabaseManager:
    """Quản lý database SQLite"""
    
    def __init__(self, db_file: str = DATABASE_FILE):
        self.db_file = db_file
        self.init_database()
    
    def init_database(self):
        """Khởi tạo database và tạo bảng"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            
            # Kiểm tra xem bảng keys có tồn tại không
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='keys'")
            table_exists = cursor.fetchone()
            
            if table_exists:
                # Bảng đã tồn tại, kiểm tra schema
                cursor.execute("PRAGMA table_info(keys)")
                columns = cursor.fetchall()
                column_names = [col[1] for col in columns]
                
                # Nếu device_id có NOT NULL constraint, cần tạo bảng mới
                if 'device_id' in column_names:
                    # Kiểm tra constraint
                    cursor.execute("PRAGMA table_info(keys)")
                    for col in columns:
                        if col[1] == 'device_id' and col[3] == 1:  # NOT NULL
                            print("⚠️  Database cũ có NOT NULL constraint, tạo bảng mới...")
                            self._recreate_keys_table(cursor)
                            break
            else:
                # Tạo bảng mới
                cursor.execute('''
                    CREATE TABLE keys (
                        key TEXT PRIMARY KEY,
                        device_id TEXT,
                        usage_limit INTEGER NOT NULL,
                        usage_count INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        is_active BOOLEAN DEFAULT 1,
                        last_used TEXT
                    )
                ''')
            
            # Bảng usage_records
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS usage_records (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    success BOOLEAN NOT NULL,
                    response_time REAL NOT NULL,
                    error_message TEXT,
                    FOREIGN KEY (key) REFERENCES keys (key)
                )
            ''')
            
            # Bảng devices
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    key TEXT NOT NULL,
                    user_agent TEXT,
                    ip_address TEXT,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    total_requests INTEGER DEFAULT 0,
                    FOREIGN KEY (key) REFERENCES keys (key)
                )
            ''')
            
            conn.commit()
    
    def _recreate_keys_table(self, cursor):
        """Tạo lại bảng keys với schema mới"""
        # Backup dữ liệu cũ
        cursor.execute("SELECT * FROM keys")
        old_data = cursor.fetchall()
        
        # Xóa bảng cũ
        cursor.execute("DROP TABLE keys")
        
        # Tạo bảng mới
        cursor.execute('''
            CREATE TABLE keys (
                key TEXT PRIMARY KEY,
                device_id TEXT,
                usage_limit INTEGER NOT NULL,
                usage_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                last_used TEXT
            )
        ''')
        
        # Restore dữ liệu (nếu có)
        if old_data:
            cursor.executemany('''
                INSERT INTO keys (key, device_id, usage_limit, usage_count, 
                                created_at, expires_at, is_active, last_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', old_data)
        
        print("✅ Đã tạo lại bảng keys với schema mới")
    
    def get_connection(self):
        """Lấy connection đến database"""
        conn = sqlite3.connect(self.db_file)
        conn.row_factory = sqlite3.Row
        return conn

# === KEY MANAGEMENT ===
class KeyManager:
    """Quản lý key và validation"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.lock = threading.Lock()
    
    def generate_key(self, device_id: Optional[str], usage_limit: int = DEFAULT_USAGE_LIMIT, 
                    expiry_days: int = DEFAULT_EXPIRY_DAYS) -> str:
        """Tạo key mới (device_id có thể None)"""
        with self.lock:
            # Tạo key unique
            timestamp = str(int(time.time()))
            random_part = str(uuid.uuid4())[:8]
            key_data = f"{timestamp}_{random_part}"
            key = hashlib.sha256(key_data.encode()).hexdigest()[:16]
            
            # Tính toán thời gian hết hạn
            created_at = datetime.now().isoformat()
            expires_at = (datetime.now() + timedelta(days=expiry_days)).isoformat()
            
            # Lưu vào database
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO keys (key, device_id, usage_limit, usage_count, 
                                    created_at, expires_at, is_active)
                    VALUES (?, ?, ?, 0, ?, ?, 1)
                ''', (key, device_id, usage_limit, created_at, expires_at))
                conn.commit()
            
            # Lưu vào CSV backup
            self._save_to_csv(key, device_id, usage_limit, created_at, expires_at)
            
            return key
    
    def validate_key(self, key: str, device_id: str) -> Tuple[bool, str, Optional[KeyInfo]]:
        """Validate key và trả về thông tin"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM keys WHERE key = ? AND device_id = ?
            ''', (key, device_id))
            
            row = cursor.fetchone()
            if not row:
                return False, "Key không tồn tại hoặc không khớp với thiết bị", None
            
            # Kiểm tra key có active không
            if not row['is_active']:
                return False, "Key đã bị vô hiệu hóa", None
            
            # Kiểm tra hết hạn
            expires_at = datetime.fromisoformat(row['expires_at'])
            if datetime.now() > expires_at:
                return False, "Key đã hết hạn", None
            
            # Kiểm tra số lượt còn lại
            remaining = row['usage_limit'] - row['usage_count']
            if remaining <= 0:
                return False, "Key đã hết lượt sử dụng", None
            
            # Tạo KeyInfo object
            key_info = KeyInfo(
                key=row['key'],
                device_id=row['device_id'],
                usage_limit=row['usage_limit'],
                usage_count=row['usage_count'],
                created_at=row['created_at'],
                expires_at=row['expires_at'],
                is_active=bool(row['is_active']),
                last_used=row['last_used']
            )
            
            return True, f"Còn {remaining} lượt sử dụng", key_info
    
    def use_key(self, key: str, device_id: str) -> Tuple[bool, str]:
        """Sử dụng 1 lượt của key"""
        with self.lock:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Kiểm tra key trước
                valid, message, key_info = self.validate_key(key, device_id)
                if not valid:
                    return False, message
                
                # Tăng usage_count
                cursor.execute('''
                    UPDATE keys 
                    SET usage_count = usage_count + 1, last_used = ?
                    WHERE key = ? AND device_id = ?
                ''', (datetime.now().isoformat(), key, device_id))
                
                conn.commit()
                
                remaining = key_info.usage_limit - (key_info.usage_count + 1)
                return True, f"Sử dụng thành công. Còn {remaining} lượt"
    
    def bind_key_to_device(self, key: str, device_id: str) -> Tuple[bool, str]:
        """Bind key với device (1 device = 1 key)"""
        with self.lock:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Kiểm tra key có tồn tại không
                cursor.execute('SELECT * FROM keys WHERE key = ?', (key,))
                key_row = cursor.fetchone()
                
                if not key_row:
                    return False, "Key không tồn tại"
                
                # Kiểm tra key có active không
                if not key_row['is_active']:
                    return False, "Key đã bị vô hiệu hóa"
                
                # Kiểm tra hết hạn
                expires_at = datetime.fromisoformat(key_row['expires_at'])
                if datetime.now() > expires_at:
                    return False, "Key đã hết hạn"
                
                # Kiểm tra device đã bind với key khác chưa
                cursor.execute('SELECT key FROM keys WHERE device_id = ? AND key != ?', (device_id, key))
                existing_key = cursor.fetchone()
                
                if existing_key:
                    return False, f"Device đã được bind với key khác: {existing_key['key'][:8]}..."
                
                # Kiểm tra key đã bind với device khác chưa
                if key_row['device_id'] is not None and key_row['device_id'] != device_id:
                    return False, f"Key đã được bind với device khác: {key_row['device_id'][:8]}..."
                
                # Bind key với device
                cursor.execute('''
                    UPDATE keys 
                    SET device_id = ?
                    WHERE key = ?
                ''', (device_id, key))
                
                conn.commit()
                
                # Lấy thông tin key sau khi bind
                cursor.execute('SELECT * FROM keys WHERE key = ?', (key,))
                updated_key = cursor.fetchone()
                
                remaining = updated_key['usage_limit'] - updated_key['usage_count']
                expires_at = datetime.fromisoformat(updated_key['expires_at']).strftime('%d/%m/%Y %H:%M')
                
                return True, f"Xác thực thành công!"
    
    def _save_to_csv(self, key: str, device_id: str, usage_limit: int, 
                    created_at: str, expires_at: str):
        """Lưu key vào CSV file backup"""
        file_exists = os.path.exists(KEYS_CSV_FILE)
        
        with open(KEYS_CSV_FILE, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = ['key', 'device_id', 'usage_limit', 'created_at', 'expires_at', 'is_active']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
            
            writer.writerow({
                'key': key,
                'device_id': device_id,
                'usage_limit': usage_limit,
                'created_at': created_at,
                'expires_at': expires_at,
                'is_active': True
            })
    
    def get_all_keys(self) -> list:
        """Lấy tất cả keys cho admin"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT key, device_id, usage_count, usage_limit, expires_at, is_active, created_at
                    FROM keys
                    ORDER BY created_at DESC
                """)
                
                keys = []
                for row in cursor.fetchall():
                    key, device_id, usage_count, usage_limit, expires_at, is_active, created_at = row
                    
                    # Parse dates
                    expires_dt = None
                    if expires_at:
                        expires_dt = datetime.fromisoformat(expires_at)
                    
                    created_dt = None
                    if created_at:
                        created_dt = datetime.fromisoformat(created_at)
                    
                    keys.append(KeyInfo(
                        key=key,
                        device_id=device_id,
                        usage_count=usage_count,
                        usage_limit=usage_limit,
                        expires_at=expires_dt,
                        is_active=bool(is_active),
                        created_at=created_dt
                    ))
                
                return keys
                
        except Exception as e:
            print(f"Error getting all keys: {e}")
            return []
    
    def delete_key(self, key: str) -> bool:
        """Xóa key"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM keys WHERE key = ?", (key,))
                conn.commit()
                return cursor.rowcount > 0
                
        except Exception as e:
            print(f"Error deleting key: {e}")
            return False
    
    def update_key(self, key: str, usage_limit: int = None, expires_at: str = None, is_active: bool = None) -> bool:
        """Cập nhật key"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Build update query dynamically
                updates = []
                params = []
                
                if usage_limit is not None:
                    updates.append("usage_limit = ?")
                    params.append(usage_limit)
                
                if expires_at is not None:
                    if expires_at:
                        updates.append("expires_at = ?")
                        params.append(expires_at.isoformat())
                    else:
                        updates.append("expires_at = NULL")
                
                if is_active is not None:
                    updates.append("is_active = ?")
                    params.append(is_active)
                
                if not updates:
                    return True  # Nothing to update
                
                params.append(key)
                
                query = f"UPDATE keys SET {', '.join(updates)} WHERE key = ?"
                cursor.execute(query, params)
                conn.commit()
                
                return cursor.rowcount > 0
                
        except Exception as e:
            print(f"Error updating key: {e}")
            return False
    
    def create_key(self, usage_limit: int = 100, expires_at = None, 
                   is_active: bool = True, custom_key: str = None):
        """Tạo key mới"""
        try:
            from datetime import datetime
            # Generate key
            if custom_key:
                key = custom_key
            else:
                key = self._generate_key()
            
            # Check if key already exists
            if self.get_key_info(key):
                if custom_key:
                    raise ValueError("Custom key đã tồn tại")
                else:
                    # Generate new key if collision
                    key = self._generate_key()
            
            # Create key in database
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO keys (key, usage_limit, expires_at, is_active, created_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (key, usage_limit, expires_at.isoformat() if expires_at else None, is_active))
                conn.commit()
            
            # Return key info
            return KeyInfo(
                key=key,
                device_id=None,
                usage_count=0,
                usage_limit=usage_limit,
                expires_at=expires_at,
                is_active=is_active,
                created_at=datetime.now()
            )
            
        except Exception as e:
            print(f"Error creating key: {e}")
            raise e
    
    def _generate_key(self) -> str:
        """Generate random key"""
        import secrets
        import string
        
        # Generate 16 character key with letters and numbers
        alphabet = string.ascii_letters + string.digits
        key = ''.join(secrets.choice(alphabet) for _ in range(16))
        return key
    
    def get_key_info(self, key: str):
        """Lấy thông tin key"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM keys WHERE key = ?
                """, (key,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                # Convert to KeyInfo object
                from datetime import datetime
                return KeyInfo(
                    key=row['key'],
                    device_id=row['device_id'],
                    usage_count=row['usage_count'],
                    usage_limit=row['usage_limit'],
                    expires_at=datetime.fromisoformat(row['expires_at']) if row['expires_at'] else None,
                    is_active=bool(row['is_active']),
                    created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else None
                )
                
        except Exception as e:
            print(f"Error getting key info: {e}")
            return None

# === USAGE TRACKING ===
class UsageTracker:
    """Theo dõi và ghi lại việc sử dụng API"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    
    def record_usage(self, key: str, device_id: str, endpoint: str, 
                    success: bool, response_time: float, 
                    error_message: Optional[str] = None) -> str:
        """Ghi lại việc sử dụng API"""
        usage_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO usage_records 
                (id, key, device_id, timestamp, endpoint, success, response_time, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (usage_id, key, device_id, timestamp, endpoint, success, response_time, error_message))
            
            # Cập nhật thống kê thiết bị
            cursor.execute('''
                INSERT OR REPLACE INTO devices 
                (device_id, key, user_agent, ip_address, first_seen, last_seen, total_requests)
                VALUES (?, ?, ?, ?, 
                    COALESCE((SELECT first_seen FROM devices WHERE device_id = ?), ?),
                    ?, 
                    COALESCE((SELECT total_requests FROM devices WHERE device_id = ?), 0) + 1)
            ''', (device_id, key, request.headers.get('User-Agent', ''), 
                 request.remote_addr, device_id, timestamp, timestamp, device_id))
            
            conn.commit()
        
        return usage_id

    def bind_key_to_device(self, key: str, device_id: str) -> Tuple[bool, str]:
        """Bind key với device (1 device chỉ được 1 key)"""
        with self.lock:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Kiểm tra key có tồn tại và chưa được bind không
                cursor.execute('''
                    SELECT device_id FROM keys WHERE key = ?
                ''', (key,))
                
                result = cursor.fetchone()
                if not result:
                    return False, "Key không tồn tại"
                
                current_device_id = result['device_id']
                if current_device_id is not None:
                    return False, "Key đã được bind với device khác"
                
                # Kiểm tra device đã có key chưa
                cursor.execute('''
                    SELECT key FROM keys WHERE device_id = ? AND is_active = 1
                ''', (device_id,))
                
                existing_key = cursor.fetchone()
                if existing_key:
                    return False, "Device này đã có key khác"
                
                # Bind key với device
                cursor.execute('''
                    UPDATE keys SET device_id = ? WHERE key = ?
                ''', (device_id, key))
                
                conn.commit()
                return True, "Bind key thành công"

# === ARTBREEDER SESSION MANAGEMENT ===
class ArtbreederSession:
    """Manages Artbreeder authentication session"""
    
    def __init__(self, proxies: Optional[Dict] = None, provider: str = "mail_tm"):
        self.proxies = proxies
        self.provider = provider
        self.connect_sid: Optional[str] = None
        
    def create_session(self) -> bool:
        """Create new Artbreeder session"""
        for attempt in range(1, MAX_SESSION_RETRIES + 1):
            log(f"🔄 Thử tạo session ({self.provider}) lần {attempt}/{MAX_SESSION_RETRIES}")
            
            self.connect_sid = self._new_artbreeder_session()
            if self.connect_sid:
                log(f"✅ Tạo session thành công lần {attempt}")
                return True
            
            log(f"❌ Lỗi tạo session ({self.provider}), thử lại lần {attempt}/{MAX_SESSION_RETRIES}")
            
            if attempt < MAX_SESSION_RETRIES:
                delay = min(attempt * 2, 10)
                log(f"⏳ Chờ {delay}s trước khi thử lại...")
                time.sleep(delay)
        
        return False
    
    def _new_artbreeder_session(self) -> Optional[str]:
        """Create email and login to Artbreeder using magic-link"""
        if self.provider == "mail_tm":
            return self._mail_tm_flow()
        elif self.provider == "mail_10m":
            return self._mail_10m_flow()
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
        
        email = None
        for _ in range(12):
            email = mail_10m.get_mail_address(session_id, proxies=self.proxies)
            if email:
                break
            time.sleep(1.0)

        if not email:
            log("❌ Không lấy được email từ 10minutemail")
            return None

        log("📧 Email mới (10min):", email)
        token = mail_10m.get_token(session_id, None)
        
        return self._complete_magic_link_flow(email, token, mail_10m)
    
    def _complete_magic_link_flow(self, email: str, token: str, mail_service) -> Optional[str]:
        """Complete magic link authentication flow"""
        if not request_magic_link(email, proxies=self.proxies):
            log("❌ Gửi magic-link đến Artbreeder thất bại")
            return None

        log("⏳ Đã yêu cầu magic-link, chờ mail về...")
        msg = mail_service.wait_for_message(
            token,
            sender_contains=SENDER_CONTAINS,
            subject_contains=SUBJECT_CONTAINS,
            timeout_seconds=300,
            poll_interval=5,
            proxies=self.proxies
        )
        
        if not msg:
            log("❌ Không nhận được email magic-link trong thời gian chờ")
            return None

        magic_link = mail_service.extract_magic_link_from_message(msg)
        if not magic_link:
            log("❌ Không trích xuất được magic-link")
            return None

        log("🔗 Magic link:", magic_link)
        connect_sid = follow_magic_link_and_get_cookie(magic_link, proxies=self.proxies)
        
        if not connect_sid:
            log("❌ Không lấy được connect.sid sau khi mở magic-link")
            return None

        log("✅ Login cookies OK")
        return connect_sid

# === IMAGE GENERATION ===
class ImageGenerator:
    """Handles image generation with Artbreeder"""
    
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.sessions = {}  # Cache sessions by provider
        self.lock = threading.Lock()
    
    def generate_image(self, request_data: GenerationRequest, key: str, device_id: str) -> Dict:
        """Generate image with Artbreeder"""
        try:
            # Validate key first
            key_manager = KeyManager(self.db)
            valid, message, key_info = key_manager.validate_key(key, device_id)
            if not valid:
                return {
                    'success': False,
                    'message': message,
                    'error_code': 'INVALID_KEY'
                }
            
            # Use one usage
            success, use_message = key_manager.use_key(key, device_id)
            if not success:
                return {
                    'success': False,
                    'message': use_message,
                    'error_code': 'KEY_EXHAUSTED'
                }
            
            # Get or create session
            session = self._get_session(request_data.server)
            if not session:
                return {
                    'success': False,
                    'message': f'Không thể tạo session Artbreeder (server {request_data.server})',
                    'error_code': 'SESSION_ERROR'
                }
            
            # Prepare generation parameters
            size_info = IMAGE_SIZES.get(request_data.size_preset, IMAGE_SIZES["16:9"])
            seed = request_data.seed or random.randint(10000000, 99999999)
            
            # Generate image
            result = self._submit_generation_job(
                prompt=request_data.prompt,
                session=session,
                width=size_info["width"],
                height=size_info["height"],
                seed=seed,
                model_version=request_data.model_version,
                strength=request_data.strength,
                guidance_scale=request_data.guidance_scale
            )
            
            if result['success']:
                # Record successful usage
                usage_tracker.record_usage(
                    key=key,
                    device_id=device_id,
                    endpoint='/api/generate-image',
                    success=True,
                    response_time=getattr(g, 'response_time', 0)
                )
                
                return {
                    'success': True,
                    'message': 'Tạo ảnh thành công',
                    'data': {
                        'image_url': result['image_url'],
                        'prompt': request_data.prompt,
                        'size': f"{size_info['width']}x{size_info['height']}",
                        'seed': seed,
                        'model_version': request_data.model_version
                    }
                }
            else:
                # Record failed usage
                usage_tracker.record_usage(
                    key=key,
                    device_id=device_id,
                    endpoint='/api/generate-image',
                    success=False,
                    response_time=getattr(g, 'response_time', 0),
                    error_message=result['message']
                )
                
                return {
                    'success': False,
                    'message': result['message'],
                    'error_code': 'GENERATION_ERROR'
                }
        
        except Exception as e:
            log(f"❌ Lỗi generate image: {e}")
            return {
                'success': False,
                'message': f'Lỗi generate image: {str(e)}',
                'error_code': 'INTERNAL_ERROR'
            }
    
    def _get_session(self, server_num: int = 1) -> Optional[ArtbreederSession]:
        """Get or create Artbreeder session"""
        with self.lock:
            # Map server number to provider
            provider_map = {1: "mail_tm", 2: "mail_10m"}
            provider = provider_map.get(server_num, "mail_tm")
            
            # Try to reuse existing session for this provider
            if provider in self.sessions:
                session = self.sessions[provider]
                if session.connect_sid and self._check_session_valid(session):
                    return session
            
            # Create new session for specified provider
            try:
                session = ArtbreederSession(provider=provider)
                if session.create_session():
                    self.sessions[provider] = session
                    return session
            except Exception as e:
                log(f"❌ Lỗi tạo session {provider}: {e}")
            
            return None
    
    def _check_session_valid(self, session: ArtbreederSession) -> bool:
        """Check if session is still valid"""
        try:
            credits = get_remaining_credits(session.connect_sid)
            return credits is not None and credits > 0
        except:
            return False
    
    def _submit_generation_job(self, prompt: str, session: ArtbreederSession, 
                              width: int, height: int, seed: int, 
                              model_version: str, strength: float, 
                              guidance_scale: float) -> Dict:
        """Submit job to Artbreeder and handle response"""
        for attempt in range(MAX_JOB_RETRIES):
            job_resp = submit_realtime_job(
                prompt=prompt,
                connect_sid=session.connect_sid,
                browser_token=BROWSER_TOKEN,
                model_version=model_version,
                job_type="img2img",
                seed=seed,
                width=width,
                height=height,
                strength=strength,
                guidance_scale=guidance_scale,
                num_steps=30,
                num_inference_steps=28,
                proxies=session.proxies
            )
            
            if job_resp and job_resp.get("url"):
                # Return image URL for client to download
                image_url = job_resp["url"]
                return {
                    'success': True,
                    'image_url': image_url
                }
            
            # Check if need to relogin
            if job_resp and job_resp.get("status") in RELOGIN_ON_ERRORS:
                log(f"🔁 Cần re-login, tạo session mới...")
                session.create_session()
            
            time.sleep(2)
        
        return {
            'success': False,
            'message': 'Không thể tạo ảnh sau nhiều lần thử'
        }
    

# === FLASK APP ===
app = Flask(__name__)
CORS(app)

# Khởi tạo managers
db_manager = DatabaseManager()
key_manager = KeyManager(db_manager)
usage_tracker = UsageTracker(db_manager)
image_generator = ImageGenerator(db_manager)

# === MIDDLEWARE ===
@app.before_request
def before_request():
    """Middleware trước mỗi request"""
    g.start_time = time.time()

@app.after_request
def after_request(response):
    """Middleware sau mỗi request"""
    if hasattr(g, 'start_time'):
        g.response_time = time.time() - g.start_time
    return response

# === API ENDPOINTS ===

@app.route('/api/health', methods=['GET'])
def health_check():
    """Kiểm tra sức khỏe server"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0'
    })

@app.route('/api/key/generate', methods=['POST'])
def generate_key():
    """Tạo key mới (không cần device_id)"""
    try:
        # Validate request method and content type
        if request.method != 'POST':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận POST request'
            }), 405
        
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Content-Type phải là application/json'
            }), 400
        
        data = request.get_json() or {}
        
        # Validate optional fields
        usage_limit = data.get('usage_limit', DEFAULT_USAGE_LIMIT)
        if not isinstance(usage_limit, int) or usage_limit <= 0 or usage_limit > 10000:
            return jsonify({
                'success': False,
                'message': 'usage_limit phải là số nguyên từ 1 đến 10000'
            }), 400
        
        expiry_days = data.get('expiry_days', DEFAULT_EXPIRY_DAYS)
        if not isinstance(expiry_days, int) or expiry_days <= 0 or expiry_days > 365:
            return jsonify({
                'success': False,
                'message': 'expiry_days phải là số nguyên từ 1 đến 365'
            }), 400
        
        # Generate key (không cần device_id)
        key = key_manager.generate_key(None, usage_limit, expiry_days)
        
        return jsonify({
            'success': True,
            'message': 'Tạo key thành công',
            'data': {
                'key': key,
                'usage_limit': usage_limit,
                'expiry_days': expiry_days
            }
        })
    
    except Exception as e:
        log(f"❌ Lỗi tạo key: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi tạo key: {str(e)}'
        }), 500

@app.route('/api/key/bind', methods=['POST'])
def bind_key():
    """Bind key với device"""
    try:
        # Validate request method and content type
        if request.method != 'POST':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận POST request'
            }), 405
        
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Content-Type phải là application/json'
            }), 400
        
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': 'Request body không được để trống'
            }), 400
        
        # Validate required fields
        key = data.get('key')
        device_id = data.get('device_id')
        
        if not key or not device_id:
            return jsonify({
                'success': False,
                'message': 'key và device_id là bắt buộc'
            }), 400
        
        # Validate key format
        if not isinstance(key, str) or len(key.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'key phải là chuỗi không rỗng'
            }), 400
        
        if len(key) != 16 or not all(c.isalnum() for c in key):
            return jsonify({
                'success': False,
                'message': 'key phải có đúng 16 ký tự alphanumeric'
            }), 400
        
        # Validate device_id format
        if not isinstance(device_id, str) or len(device_id.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'device_id phải là chuỗi không rỗng'
            }), 400
        
        if len(device_id) > 100:
            return jsonify({
                'success': False,
                'message': 'device_id không được quá 100 ký tự'
            }), 400
        
        # Bind key
        success, message = key_manager.bind_key_to_device(key.strip(), device_id.strip())
        
        if success:
            # Lấy thông tin key để trả về remaining
            with key_manager.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM keys WHERE key = ?', (key.strip(),))
                key_info = cursor.fetchone()
                
                remaining = key_info['usage_limit'] - key_info['usage_count']
                
                return jsonify({
                    'success': True,
                    'message': message,
                    'remaining': remaining,
                    'usage_limit': key_info['usage_limit'],
                    'usage_count': key_info['usage_count'],
                    'expires_at': key_info['expires_at']
                })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 400
    
    except Exception as e:
        log(f"❌ Lỗi bind key: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi bind key: {str(e)}'
        }), 500

@app.route('/api/key/validate', methods=['POST'])
def validate_key():
    """Validate key"""
    try:
        # Validate request method and content type
        if request.method != 'POST':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận POST request'
            }), 405
        
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Content-Type phải là application/json'
            }), 400
        
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': 'Request body không được để trống'
            }), 400
        
        # Validate required fields
        key = data.get('key')
        device_id = data.get('device_id')
        
        if not key or not device_id:
            return jsonify({
                'success': False,
                'message': 'key và device_id là bắt buộc'
            }), 400
        
        # Validate key format
        if not isinstance(key, str) or len(key.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'key phải là chuỗi không rỗng'
            }), 400
        
        if len(key) != 16 or not all(c.isalnum() for c in key):
            return jsonify({
                'success': False,
                'message': 'key phải có đúng 16 ký tự alphanumeric'
            }), 400
        
        # Validate device_id format
        if not isinstance(device_id, str) or len(device_id.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'device_id phải là chuỗi không rỗng'
            }), 400
        
        if len(device_id) > 100:
            return jsonify({
                'success': False,
                'message': 'device_id không được quá 100 ký tự'
            }), 400
        
        # Validate key
        valid, message, key_info = key_manager.validate_key(key.strip(), device_id.strip())
        
        if valid:
            return jsonify({
                'success': True,
                'message': message,
                'data': asdict(key_info)
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 401
    
    except Exception as e:
        log(f"❌ Lỗi validate key: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi validate key: {str(e)}'
        }), 500

@app.route('/api/key/use', methods=['POST'])
def use_key():
    """Sử dụng 1 lượt của key"""
    try:
        # Validate request method and content type
        if request.method != 'POST':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận POST request'
            }), 405
        
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Content-Type phải là application/json'
            }), 400
        
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': 'Request body không được để trống'
            }), 400
        
        # Validate required fields
        key = data.get('key')
        device_id = data.get('device_id')
        
        if not key or not device_id:
            return jsonify({
                'success': False,
                'message': 'key và device_id là bắt buộc'
            }), 400
        
        # Validate key format
        if not isinstance(key, str) or len(key.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'key phải là chuỗi không rỗng'
            }), 400
        
        if len(key) != 16 or not all(c.isalnum() for c in key):
            return jsonify({
                'success': False,
                'message': 'key phải có đúng 16 ký tự alphanumeric'
            }), 400
        
        # Validate device_id format
        if not isinstance(device_id, str) or len(device_id.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'device_id phải là chuỗi không rỗng'
            }), 400
        
        if len(device_id) > 100:
            return jsonify({
                'success': False,
                'message': 'device_id không được quá 100 ký tự'
            }), 400
        
        # Use key
        success, message = key_manager.use_key(key.strip(), device_id.strip())
        
        # Ghi lại usage
        usage_tracker.record_usage(
            key=key.strip(),
            device_id=device_id.strip(),
            endpoint='/api/key/use',
            success=success,
            response_time=getattr(g, 'response_time', 0),
            error_message=None if success else message
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 401
    
    except Exception as e:
        log(f"❌ Lỗi sử dụng key: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi sử dụng key: {str(e)}'
        }), 500

@app.route('/api/key/info', methods=['GET'])
def get_key_info():
    """Lấy thông tin chi tiết của key"""
    try:
        # Validate request method
        if request.method != 'GET':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận GET request'
            }), 405
        
        # Get query parameters
        key = request.args.get('key')
        device_id = request.args.get('device_id')
        
        if not key or not device_id:
            return jsonify({
                'success': False,
                'message': 'key và device_id là bắt buộc'
            }), 400
        
        # Validate key format
        if not isinstance(key, str) or len(key.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'key phải là chuỗi không rỗng'
            }), 400
        
        if len(key) != 16 or not all(c.isalnum() for c in key):
            return jsonify({
                'success': False,
                'message': 'key phải có đúng 16 ký tự alphanumeric'
            }), 400
        
        # Validate device_id format
        if not isinstance(device_id, str) or len(device_id.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'device_id phải là chuỗi không rỗng'
            }), 400
        
        if len(device_id) > 100:
            return jsonify({
                'success': False,
                'message': 'device_id không được quá 100 ký tự'
            }), 400
        
        # Validate key
        valid, message, key_info = key_manager.validate_key(key.strip(), device_id.strip())
        
        if valid:
            return jsonify({
                'success': True,
                'message': 'Lấy thông tin thành công',
                'data': asdict(key_info)
            })
        else:
            return jsonify({
                'success': False,
                'message': message
            }), 401
    
    except Exception as e:
        log(f"❌ Lỗi lấy thông tin key: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi lấy thông tin key: {str(e)}'
        }), 500

@app.route('/api/usage/stats', methods=['GET'])
def get_usage_stats():
    """Lấy thống kê sử dụng"""
    try:
        # Validate request method
        if request.method != 'GET':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận GET request'
            }), 405
        
        # Get query parameters
        key = request.args.get('key')
        device_id = request.args.get('device_id')
        
        if not key or not device_id:
            return jsonify({
                'success': False,
                'message': 'key và device_id là bắt buộc'
            }), 400
        
        # Validate key format
        if not isinstance(key, str) or len(key.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'key phải là chuỗi không rỗng'
            }), 400
        
        if len(key) != 16 or not all(c.isalnum() for c in key):
            return jsonify({
                'success': False,
                'message': 'key phải có đúng 16 ký tự alphanumeric'
            }), 400
        
        # Validate device_id format
        if not isinstance(device_id, str) or len(device_id.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'device_id phải là chuỗi không rỗng'
            }), 400
        
        if len(device_id) > 100:
            return jsonify({
                'success': False,
                'message': 'device_id không được quá 100 ký tự'
            }), 400
        
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            
            # Thống kê tổng quan
            cursor.execute('''
                SELECT COUNT(*) as total_requests,
                       SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_requests,
                       AVG(response_time) as avg_response_time
                FROM usage_records 
                WHERE key = ? AND device_id = ?
            ''', (key.strip(), device_id.strip()))
            
            stats = cursor.fetchone()
            
            # Thống kê theo endpoint
            cursor.execute('''
                SELECT endpoint, COUNT(*) as count
                FROM usage_records 
                WHERE key = ? AND device_id = ?
                GROUP BY endpoint
            ''', (key.strip(), device_id.strip()))
            
            endpoint_stats = [dict(row) for row in cursor.fetchall()]
            
            return jsonify({
                'success': True,
                'message': 'Lấy thống kê thành công',
                'data': {
                    'total_requests': stats['total_requests'],
                    'successful_requests': stats['successful_requests'],
                    'avg_response_time': round(stats['avg_response_time'] or 0, 3),
                    'endpoint_stats': endpoint_stats
                }
            })
    
    except Exception as e:
        log(f"❌ Lỗi lấy thống kê: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi lấy thống kê: {str(e)}'
        }), 500

@app.route('/api/admin/keys', methods=['GET'])
def list_all_keys():
    """Admin: Liệt kê tất cả keys"""
    try:
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT k.*, d.user_agent, d.ip_address, d.total_requests
                FROM keys k
                LEFT JOIN devices d ON k.key = d.key AND k.device_id = d.device_id
                ORDER BY k.created_at DESC
            ''')
            
            keys = []
            for row in cursor.fetchall():
                key_data = dict(row)
                key_data['remaining_usage'] = key_data['usage_limit'] - key_data['usage_count']
                keys.append(key_data)
            
            return jsonify({
                'success': True,
                'message': f'Tìm thấy {len(keys)} keys',
                'data': keys
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi lấy danh sách keys: {str(e)}'
        }), 500

@app.route('/api/admin/usage', methods=['GET'])
def list_all_usage():
    """Admin: Liệt kê tất cả usage records"""
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM usage_records 
                ORDER BY timestamp DESC 
                LIMIT ? OFFSET ?
            ''', (limit, offset))
            
            usage_records = [dict(row) for row in cursor.fetchall()]
            
            return jsonify({
                'success': True,
                'message': f'Tìm thấy {len(usage_records)} records',
                'data': usage_records
            })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi lấy danh sách usage: {str(e)}'
        }), 500


@app.route('/api/generate-image', methods=['POST'])
def generate_image():
    """Generate image với Artbreeder"""
    try:
        # Validate request method and content type
        if request.method != 'POST':
            return jsonify({
                'success': False,
                'message': 'Chỉ chấp nhận POST request'
            }), 405
        
        if not request.is_json:
            return jsonify({
                'success': False,
                'message': 'Content-Type phải là application/json'
            }), 400
        
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': 'Request body không được để trống'
            }), 400
        
        # Validate required fields
        key = data.get('key')
        device_id = data.get('device_id')
        prompt = data.get('prompt')
        
        if not key or not device_id or not prompt:
            return jsonify({
                'success': False,
                'message': 'key, device_id và prompt là bắt buộc'
            }), 400
        
        # Validate key format
        if not isinstance(key, str) or len(key.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'key phải là chuỗi không rỗng'
            }), 400
        
        if len(key) != 16 or not all(c.isalnum() for c in key):
            return jsonify({
                'success': False,
                'message': 'key phải có đúng 16 ký tự alphanumeric'
            }), 400
        
        # Validate device_id format
        if not isinstance(device_id, str) or len(device_id.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'device_id phải là chuỗi không rỗng'
            }), 400
        
        if len(device_id) > 100:
            return jsonify({
                'success': False,
                'message': 'device_id không được quá 100 ký tự'
            }), 400
        
        # Validate prompt
        if not isinstance(prompt, str) or len(prompt.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'prompt phải là chuỗi không rỗng'
            }), 400
        
        if len(prompt) > 1000:
            return jsonify({
                'success': False,
                'message': 'prompt không được quá 1000 ký tự'
            }), 400
        
        # Validate optional fields
        size_preset = data.get('size_preset', '16:9')
        if not isinstance(size_preset, str) or size_preset not in IMAGE_SIZES:
            return jsonify({
                'success': False,
                'message': f'Size preset không hợp lệ. Chọn từ: {list(IMAGE_SIZES.keys())}'
            }), 400
        
        seed = data.get('seed')
        if seed is not None:
            if not isinstance(seed, int) or seed < 0 or seed > 999999999:
                return jsonify({
                    'success': False,
                    'message': 'seed phải là số nguyên từ 0 đến 999999999'
                }), 400
        
        model_version = data.get('model_version', 'flux-dev')
        if not isinstance(model_version, str) or len(model_version.strip()) == 0:
            return jsonify({
                'success': False,
                'message': 'model_version phải là chuỗi không rỗng'
            }), 400
        
        strength = data.get('strength', 1.0)
        if not isinstance(strength, (int, float)) or strength < 0.1 or strength > 2.0:
            return jsonify({
                'success': False,
                'message': 'strength phải là số từ 0.1 đến 2.0'
            }), 400
        
        guidance_scale = data.get('guidance_scale', 3.5)
        if not isinstance(guidance_scale, (int, float)) or guidance_scale < 1.0 or guidance_scale > 20.0:
            return jsonify({
                'success': False,
                'message': 'guidance_scale phải là số từ 1.0 đến 20.0'
            }), 400
        
        server = data.get('server', 1)
        if not isinstance(server, int) or server not in [1, 2]:
            return jsonify({
                'success': False,
                'message': 'Server phải là 1 (mail.tm) hoặc 2 (10minutemail)'
            }), 400
        
        # Tạo GenerationRequest
        gen_request = GenerationRequest(
            prompt=prompt.strip(),
            size_preset=size_preset,
            seed=seed,
            model_version=model_version.strip(),
            strength=float(strength),
            guidance_scale=float(guidance_scale),
            server=server
        )
        
        # Generate image
        result = image_generator.generate_image(gen_request, key.strip(), device_id.strip())
        
        if result['success']:
            return jsonify(result)
        else:
            status_code = 500
            if result.get('error_code') == 'INVALID_KEY':
                status_code = 401
            elif result.get('error_code') == 'KEY_EXHAUSTED':
                status_code = 402
            
            return jsonify(result), status_code
    
    except Exception as e:
        log(f"❌ Lỗi generate image: {e}")
        return jsonify({
            'success': False,
            'message': f'Lỗi generate image: {str(e)}',
            'error_code': 'INTERNAL_ERROR'
        }), 500

@app.route('/api/image-sizes', methods=['GET'])
def get_image_sizes():
    """Lấy danh sách kích thước ảnh có sẵn"""
    return jsonify({
        'success': True,
        'message': 'Danh sách kích thước ảnh',
        'data': IMAGE_SIZES
    })

@app.route('/api/version.json', methods=['GET'])
def get_version():
    """Lấy thông tin phiên bản server"""
    return jsonify({
        'version': '1.0.0',
        'build': '2024-01-05',
        'features': [
            'Multi-threading support',
            'Size presets: 16:9, 9:16, 1:1',
            'Server selection: mail.tm, 10minutemail',
            'Excel import',
            'Batch image generation'
        ],
        'api_version': '1.0',
        'server_status': 'running'
    })

@app.route('/api/contact-info', methods=['GET'])
def get_contact_info():
    """Lấy thông tin liên hệ"""
    return jsonify({
        'success': True,
        'data': {
            'company_name': 'MultiGen AI',
            'developer': '@huyit32',
            'telegram': '@huyit32',
            'website': 'https://quochuy.io.vn',
        }
    })

@app.route('/api/packages', methods=['GET'])
def get_packages():
    """Lấy thông tin các gói dịch vụ"""
    return jsonify({
        'success': True,
        'data': {
            'packages': [
                {
                    'id': 'basic',
                    'name': 'Gói Cơ Bản',
                    'icon': '🎯',
                    'images': 500,
                    'price': 150000,
                    'description': 'Phù hợp cho dự án nhỏ, test thử'
                },
                {
                    'id': 'standard',
                    'name': 'Gói Tiêu Chuẩn',
                    'icon': '🚀',
                    'images': 1000,
                    'price': 300000,
                    'description': 'Phù hợp cho dự án vừa, sản xuất nội dung'
                },
                {
                    'id': 'enterprise',
                    'name': 'Gói Doanh Nghiệp',
                    'icon': '💎',
                    'images': 5000,
                    'price': 2000000,
                    'description': 'Phù hợp cho dự án lớn, sản xuất hàng loạt'
                }
            ],
            'offers': [
                'Mua 2 gói trở lên: Giảm 10%',
                'Khách hàng thân thiết: Giảm 15%',
                'Thanh toán trước: Giảm 5%'
            ],
            'payment_methods': [
                'Chuyển khoản ngân hàng',
                'Momo, ZaloPay, PayPal'
            ],
            'qr_code': {
                'url': 'https://img.vietqr.io/image/vietinbank-113366668888-compact.jpg',
                'alt_text': 'Mã QR thanh toán'
            }
        }
    })


# === ADMIN API ENDPOINTS ===

@app.route('/api/admin/keys', methods=['GET'])
def admin_get_keys():
    """Lấy danh sách tất cả keys cho admin"""
    try:
        keys = key_manager.get_all_keys()
        keys_data = []
        
        for key_info in keys:
            keys_data.append({
                'key': key_info.key,
                'device_id': key_info.device_id,
                'usage_count': key_info.usage_count,
                'usage_limit': key_info.usage_limit,
                'expires_at': key_info.expires_at.isoformat() if key_info.expires_at else None,
                'is_active': key_info.is_active,
                'created_at': key_info.created_at.isoformat() if key_info.created_at else None
            })
        
        return jsonify({
            'success': True,
            'data': keys_data,
            'total': len(keys_data)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi lấy danh sách keys: {str(e)}'
        }), 500

@app.route('/api/admin/keys', methods=['POST'])
def admin_create_key():
    """Tạo key mới cho admin"""
    try:
        data = request.get_json()
        
        # Validate input
        usage_limit = data.get('usage_limit', 100)
        expires_at = data.get('expires_at')
        is_active = data.get('is_active', True)
        custom_key = data.get('custom_key')
        
        if not isinstance(usage_limit, int) or usage_limit < 1:
            return jsonify({
                'success': False,
                'message': 'Usage limit phải là số nguyên dương'
            }), 400
        
        # Parse expiry date
        expiry_date = None
        if expires_at:
            try:
                from datetime import datetime
                expiry_date = datetime.fromisoformat(expires_at)
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': 'Định dạng ngày hết hạn không hợp lệ'
                }), 400
        
        # Create key
        key_info = key_manager.create_key(
            usage_limit=usage_limit,
            expires_at=expiry_date,
            is_active=is_active,
            custom_key=custom_key
        )
        
        return jsonify({
            'success': True,
            'message': 'Tạo key thành công',
            'key': key_info.key,
            'data': {
                'key': key_info.key,
                'usage_limit': key_info.usage_limit,
                'expires_at': key_info.expires_at.isoformat() if key_info.expires_at else None,
                'is_active': key_info.is_active
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi tạo key: {str(e)}'
        }), 500

@app.route('/api/admin/keys/<key>', methods=['DELETE'])
def admin_delete_key(key):
    """Xóa key cho admin"""
    try:
        if not key:
            return jsonify({
                'success': False,
                'message': 'Key không được để trống'
            }), 400
        
        # Check if key exists
        key_info = key_manager.get_key_info(key)
        if not key_info:
            return jsonify({
                'success': False,
                'message': 'Key không tồn tại'
            }), 404
        
        # Delete key
        success = key_manager.delete_key(key)
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Xóa key thành công'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Không thể xóa key'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi xóa key: {str(e)}'
        }), 500

@app.route('/api/admin/keys/<key>', methods=['PUT'])
def admin_update_key(key):
    """Cập nhật key cho admin"""
    try:
        if not key:
            return jsonify({
                'success': False,
                'message': 'Key không được để trống'
            }), 400
        
        data = request.get_json()
        
        # Check if key exists
        key_info = key_manager.get_key_info(key)
        if not key_info:
            return jsonify({
                'success': False,
                'message': 'Key không tồn tại'
            }), 404
        
        # Parse expiry date
        expiry_date = data.get('expires_at')
        if expiry_date:
            try:
                from datetime import datetime
                expiry_date = datetime.fromisoformat(expiry_date)
            except ValueError:
                return jsonify({
                    'success': False,
                    'message': 'Định dạng ngày hết hạn không hợp lệ'
                }), 400
        
        # Update key
        success = key_manager.update_key(
            key=key,
            usage_limit=data.get('usage_limit'),
            expires_at=expiry_date,
            is_active=data.get('is_active')
        )
        
        if success:
            return jsonify({
                'success': True,
                'message': 'Cập nhật key thành công'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Không thể cập nhật key'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Lỗi cập nhật key: {str(e)}'
        }), 500

# === ERROR HANDLERS ===
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'success': False,
        'message': 'Endpoint không tồn tại'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'success': False,
        'message': 'Lỗi server nội bộ'
    }), 500

# === MAIN ===
if __name__ == '__main__':
    print("🚀 Artbreeder AI Server API")
    print("📊 Database:", DATABASE_FILE)
    print("📄 Keys CSV:", KEYS_CSV_FILE)
    print("🌐 Server: http://localhost:5000")
    print("📚 API Endpoints:")
    print("   GET  /api/health")
    print("   POST /api/key/generate")
    print("   POST /api/key/validate") 
    print("   POST /api/key/use")
    print("   GET  /api/key/info")
    print("   GET  /api/usage/stats")
    print("   POST /api/generate-image")
    print("   GET  /api/image-sizes")
    print("   GET  /api/generated-images")
    print("   GET  /api/admin/keys")
    print("   GET  /api/admin/usage")
    print("=" * 40)
    
    app.run(host='0.0.0.0', port=5000, debug=False)