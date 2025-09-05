#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Start Artbreeder AI Server
"""

import os
import sys

def main():
    print("🚀 Khởi động Artbreeder AI Server...")
    
    # Kiểm tra Python version
    if sys.version_info < (3, 7):
        print("❌ Cần Python 3.7+ để chạy server")
        sys.exit(1)
    
    # Kiểm tra dependencies
    try:
        import flask
        import flask_cors
        print("✅ Dependencies OK")
    except ImportError as e:
        print(f"❌ Thiếu dependency: {e}")
        print("📦 Chạy: pip install -r requirements.txt")
        sys.exit(1)
    
    
    # Import và chạy server
    try:
        from server import app
        print("🌐 Server chạy tại: http://localhost:5000")
        print("📚 API Documentation: README_API.md")
        print("=" * 50)
        app.run(host='0.0.0.0', port=5000, debug=False)
    except Exception as e:
        print(f"❌ Lỗi khởi động server: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()