import uuid
import hashlib
import platform
import subprocess
import requests
from requests.exceptions import RequestException, ConnectionError, Timeout

SECRET_SALT = "huydev"

def get_device_id():
    mac = str(uuid.getnode())
    serial = "unknown"

    if platform.system() == "Windows":
        try:
            result = subprocess.check_output("wmic diskdrive get SerialNumber", shell=True)
            lines = result.decode().strip().split("\n")
            if len(lines) > 1:
                serial = lines[1].strip()
        except:
            pass

    raw = f"{mac}-{serial}-{SECRET_SALT}"
    device_id_hash = hashlib.sha256(raw.encode()).hexdigest()
    return device_id_hash, mac, serial


def check_key_online(key: str, api_url: str):
    device_id_hash, mac, serial = get_device_id()

    try:
        response = requests.post(api_url, data={
            "key": key,
            "device_id": device_id_hash
        }, timeout=10)

        try:
            res = response.json()
            message = res.get("message", f"❌ HTTP {response.status_code}")
        except Exception:
            message = f"❌ HTTP {response.status_code} (no JSON)"
            res = {}

        if response.status_code != 200:
            return False, message, {}

        if res.get("success"):
            info = {
                "key": key,
                "device_id": f"{mac} | {serial}",
                "expires": res.get("expires", ""),
                "remaining": res.get("remaining", "")
            }
            return True, res.get("message", "✅ Thành công"), info
        else:
            return False, res.get("message", "❌ KEY không hợp lệ"), {}

    except ConnectionError:
        return False, "📡 Không thể kết nối tới máy chủ. Kiểm tra kết nối mạng.", {}
    except Timeout:
        return False, "⏳ Máy chủ không phản hồi. Vui lòng thử lại sau.", {}
    except RequestException as e:
        return False, f"❌ Lỗi mạng: {str(e)}", {}
    except Exception as e:
        return False, f"⚠️ Lỗi không xác định: {str(e)}", {}
