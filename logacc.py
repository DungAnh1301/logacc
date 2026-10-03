import requests
import sys
import os
import time
import re
import urllib.parse
import asyncio
from datetime import datetime, timezone, timedelta
import subprocess
import random
import hashlib
import shutil
import importlib.util
import ctypes
import psutil
import threading
import traceback

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QTextEdit, QLineEdit, QLabel, QSizePolicy, QMessageBox,
                             QComboBox, QDialog, QCheckBox, QSpinBox, QProgressBar, QTabWidget, QGroupBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QTextCursor

from playwright.async_api import async_playwright   
import inspect
import playwright
import playwright._impl._driver as pw_driver

def patch_playwright_driver():
    """Vá lỗi Playwright không tìm thấy node.exe khi chạy trong file EXE (PyInstaller)"""
    original_compute = pw_driver.compute_driver_executable

    def custom_compute():
        candidates = []
        # 1. Thư mục tạm giải nén của PyInstaller
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "playwright", "driver"))
            candidates.append(os.path.join(meipass, "driver"))
            candidates.append(meipass)

        # 2. Thư mục chứa file exe / script
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "playwright", "driver"))
        candidates.append(os.path.join(exe_dir, "driver"))
        candidates.append(exe_dir)

        # 3. Thư mục playwright gốc
        try:
            p_file = inspect.getfile(playwright)
            clean_dir = os.path.dirname(p_file)
            if "base_library.zip" in clean_dir:
                clean_dir = clean_dir.replace("base_library.zip", "").rstrip("/\\")
            candidates.append(os.path.join(clean_dir, "driver"))
        except Exception:
            pass

        # 4. Thư mục site-packages python trên máy người dùng
        user_profile = os.environ.get("USERPROFILE", "")
        if user_profile:
            candidates.append(os.path.join(user_profile, r"AppData\Local\Programs\Python\Python313\Lib\site-packages\playwright\driver"))
            candidates.append(os.path.join(user_profile, r"AppData\Local\Programs\Python\Python312\Lib\site-packages\playwright\driver"))
            candidates.append(os.path.join(user_profile, r"AppData\Local\Programs\Python\Python311\Lib\site-packages\playwright\driver"))

        for c in candidates:
            node = os.path.join(c, "node.exe")
            cli = os.path.join(c, "package", "cli.js")
            if os.path.isfile(node) and os.path.isfile(cli):
                return (node, cli)

        return original_compute()

    pw_driver.compute_driver_executable = custom_compute

try:
    patch_playwright_driver()
except Exception:
    pass

# Cấu hình mã hóa UTF-8 cho console trên Windows để tránh lỗi UnicodeEncodeError
if sys.platform == "win32":
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def safe_print(msg):
    try:
        print(msg)
    except Exception:
        try:
            print(msg.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass

def global_exception_handler(exctype, value, tb):
    err = "".join(traceback.format_exception(exctype, value, tb))
    safe_print(f"⚠️ Đã chặn crash giao diện: {err}")

sys.excepthook = global_exception_handler

# --- CẤU HÌNH PHIÊN BẢN & TỰ ĐỘNG CẬP NHẬT TỪ GITHUB ---
APP_VERSION = "1.3.0"
GITHUB_REPO_OWNER = "DungAnh1301"
GITHUB_REPO_NAME = "logacc"
GITHUB_FILE_PATH = "logacc.py"

def check_auto_update(silent=False, parent_widget=None):
    """
    Tự động kiểm tra bản cập nhật mới nhất từ GitHub.
    - Chạy dạng EXE: Tải bản cập nhật về logacc_engine.py cùng thư mục exe và nạp tự động.
    - Chạy dạng Script: Cập nhật đè file script hiện tại.
    Bypass triệt để cache của GitHub bằng Commit SHA.
    """
    try:
        if "--no-update" in sys.argv:
            return False

        is_frozen = getattr(sys, "frozen", False)
        if is_frozen:
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            target_file = os.path.join(exe_dir, "logacc_engine.py")
        else:
            target_file = os.path.abspath(__file__)
            # Kiểm tra nếu đang trong repo git phát triển có uncommitted changes thì không ghi đè
            git_dir = os.path.join(os.path.dirname(target_file), ".git")
            if os.path.exists(git_dir):
                try:
                    res = subprocess.run(
                        ["git", "status", "--porcelain", os.path.basename(target_file)],
                        cwd=os.path.dirname(target_file),
                        capture_output=True, text=True, timeout=2
                    )
                    if res.stdout.strip():
                        msg = "⚠️ Code local đang chỉnh sửa (uncommitted git). Tạm bỏ qua auto-update để bảo vệ code."
                        safe_print(msg)
                        if parent_widget and hasattr(parent_widget, "update_log"):
                            parent_widget.update_log(msg)
                        return False
                except Exception:
                    pass

        if not silent:
            msg = f"🔍 Đang kiểm tra cập nhật từ GitHub ({GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME})..."
            safe_print(msg)
            if parent_widget and hasattr(parent_widget, "update_log"):
                parent_widget.update_log(msg)

        headers = {
            "User-Agent": "logacc-updater",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }

        # 1. Lấy SHA commit mới nhất từ GitHub API để tránh cache của CDN Fastly/Cloudflare
        latest_sha = None
        try:
            api_url = f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/commits/main"
            api_resp = requests.get(api_url, headers=headers, timeout=4)
            if api_resp.status_code == 200:
                latest_sha = api_resp.json().get("sha")
        except Exception:
            pass

        # 2. Xây dựng URL tải file: dùng SHA nếu có, ngược lại dùng main với timestamp
        if latest_sha:
            update_url = f"https://raw.githubusercontent.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/{latest_sha}/{GITHUB_FILE_PATH}"
        else:
            update_url = f"https://raw.githubusercontent.com/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/main/{GITHUB_FILE_PATH}?t={int(time.time())}"

        resp = requests.get(update_url, headers=headers, timeout=6)

        if resp.status_code == 200:
            remote_code = resp.content
            # Kiểm tra sơ bộ tính toàn vẹn của file code tải về
            if len(remote_code) > 2000 and b"MainWindow" in remote_code:
                local_code = b""
                if os.path.isfile(target_file):
                    with open(target_file, "rb") as f:
                        local_code = f.read()

                local_hash = hashlib.sha256(local_code.replace(b'\r\n', b'\n')).hexdigest() if local_code else ""
                remote_hash = hashlib.sha256(remote_code.replace(b'\r\n', b'\n')).hexdigest()

                if local_hash != remote_hash:
                    msg = "🚀 Phát hiện phiên bản mới trên GitHub! Đang tiến hành cập nhật..."
                    safe_print(msg)
                    if parent_widget and hasattr(parent_widget, "update_log"):
                        parent_widget.update_log(msg)

                    # Tạo bản sao lưu an toàn nếu file cũ đã tồn tại
                    if os.path.isfile(target_file):
                        backup_file = target_file + ".bak"
                        try:
                            shutil.copy2(target_file, backup_file)
                        except Exception:
                            pass

                    # Ghi đè mã nguồn mới
                    with open(target_file, "wb") as f:
                        f.write(remote_code)

                    safe_print("✅ Cập nhật thành công! Đang khởi động lại ứng dụng...")
                    if parent_widget and hasattr(parent_widget, "update_log"):
                        parent_widget.update_log("✅ Đã cập nhật xong! Đang khởi động lại ứng dụng...")

                    # Khởi động lại tiến trình
                    args = [a for a in sys.argv[1:] if a != "--updated"]
                    if is_frozen:
                        subprocess.Popen([sys.executable] + args)
                    else:
                        subprocess.Popen([sys.executable, target_file] + args)

                    if parent_widget:
                        QApplication.quit()
                    else:
                        sys.exit(0)
                    return True
                else:
                    if not silent:
                        msg = "✅ Bạn đang dùng phiên bản mới nhất."
                        safe_print(msg)
                        if parent_widget and hasattr(parent_widget, "update_log"):
                            parent_widget.update_log(msg)
            else:
                if not silent:
                    safe_print("⚠️ File tải về từ GitHub không hợp lệ.")
        else:
            if not silent:
                safe_print(f"⚠️ Không thể kết nối GitHub (HTTP {resp.status_code})")
    except Exception as e:
        if not silent:
            safe_print(f"⚠️ Kiểm tra cập nhật thất bại: {e}. Tiếp tục chạy...")
    return False
   

# --- HÀM TỰ ĐỘNG NHẬN DIỆN EMAIL VÀ MẬT KHẨU EMAIL TRONG CHUỖI ĐẦU VÀO ---
def extract_email_and_password(raw_str):
    """
    Tự động nhận diện tài khoản email và mật khẩu email từ chuỗi phân tách bằng dấu '|'.
    - Dấu hiệu nhận diện email: chứa ký tự '@' và có tên miền (như .com, .net, hotmail, outlook, gmail...)
    - Mật khẩu email: nằm ngay ở vị trí tiếp theo sau vị trí của email (parts[i+1]).
    Trả về: (email, password, email_index) hoặc (None, None, -1) nếu không tìm thấy.
    """
    if not raw_str:
        return None, None, -1
        
    parts = [p.strip() for p in raw_str.split('|') if p.strip()]
    if not parts:
        return None, None, -1
    
    # Tìm phần tử là email: có '@' và dấu chấm tên miền sau '@' (như .com)
    email_idx = -1
    for idx, part in enumerate(parts):
        lower_part = part.lower()
        if '@' in lower_part:
            domain_part = lower_part.split('@')[-1]
            if '.com' in domain_part or '.' in domain_part:
                email_idx = idx
                break
                
    if email_idx != -1:
        email = parts[email_idx]
        password = parts[email_idx + 1] if (email_idx + 1 < len(parts)) else ""
        return email, password, email_idx
        
    # Dự phòng nếu chuỗi có 2 phần tk|mk mà thiếu @
    if len(parts) >= 2:
        return parts[0], parts[1], 0
    elif len(parts) == 1:
        return parts[0], "", 0
        
    return None, None, -1

# --- HÀM HỖ TRỢ LẤY OTP TỪ EMAIL KHÔI PHỤC (CHIẾN THUẬT 2 PHÚT / 20S) ---
def get_outlook_otp_via_api(recovery_acc_str, log_signal):
    try:
        parts = recovery_acc_str.split('|')
        if len(parts) < 4:
            log_signal.emit("❌ Định dạng email khôi phục sai! Cần dạng tk|mk|outh2|clientId")
            return None
        
        ref_token = parts[2].strip()
        client_id = parts[3].strip()

        log_signal.emit(f"🔄 Đang dùng OAuth2 của email khôi phục để lấy Access Token...")
        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        payload = {
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": ref_token,
            "scope": "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
        }
        
        res = requests.post(token_url, data=payload, timeout=15)
        res_data = res.json()
        access_token = res_data.get('access_token')

        if not access_token:
            log_signal.emit(f"❌ Không đổi được Access Token từ email khôi phục: {res_data.get('error_description')}")
            return None

        log_signal.emit(f"✅ Đã có Access Token khôi phục. Đang quét hộp thư tìm mã OTP...")
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        messages_url = "https://graph.microsoft.com/v1.0/me/messages?$select=subject,bodyPreview,receivedDateTime&$orderby=receivedDateTime%20desc&$top=5"

        start_time_loop = time.time()

        for attempt in range(20):
            response = requests.get(messages_url, headers=headers, timeout=15)
            if response.status_code == 200:
                messages = response.json().get('value', [])
                elapsed_time = time.time() - start_time_loop
                
                for msg in messages:
                    received_str = msg.get('receivedDateTime', '')
                    subject = msg.get('subject', '')
                    preview = msg.get('bodyPreview', '')
                    full_text = f"{subject} {preview}"
                    
                    if "microsoft" in full_text.lower() or "mã" in full_text.lower() or "code" in full_text.lower() or "security" in full_text.lower():
                        match = re.search(r'\b\d{4,7}\b', full_text)
                        if match:
                            otp_code = match.group(0)
                            if received_str:
                                msg_time = datetime.fromisoformat(received_str.replace('Z', '+00:00'))
                                time_diff = (datetime.now(timezone.utc) - msg_time).total_seconds()
                                if time_diff <= 120 and time_diff >= -10:
                                    log_signal.emit(f"🎯 Bắt được mã OTP mới (Nhận cách đây {int(time_diff)}s): {otp_code}")
                                    return otp_code

                if elapsed_time > 20 and messages:
                    for msg in messages:
                        subject = msg.get('subject', '')
                        preview = msg.get('bodyPreview', '')
                        full_text = f"{subject} {preview}"
                        if "microsoft" in full_text.lower() or "mã" in full_text.lower() or "code" in full_text.lower() or "security" in full_text.lower():
                            match = re.search(r'\b\d{4,7}\b', full_text)
                            if match:
                                otp_code = match.group(0)
                                log_signal.emit(f"⚠️ Lấy tạm mã OTP gần nhất: {otp_code}")
                                return otp_code

            time.sleep(4)
            log_signal.emit(f"⏳ Đang đợi thư OTP về (lần thử {attempt+1}/20)...")

        return None
    except Exception as e:
        log_signal.emit(f"❌ Lỗi khi lấy OTP qua API: {str(e)}")
        return None


# ========================================================
# CẤU HÌNH & BỘ SINH FAKE FINGERPRINT (CHỐNG QUÉT THIẾT BỊ)
# ========================================================
CHROME_VERSIONS = [
    ("133", "133.0.6943.53", "15.0.0"),
    ("132", "132.0.6834.83", "15.0.0"),
    ("131", "131.0.6778.86", "10.0.0"),
    ("130", "130.0.6723.92", "10.0.0"),
    ("129", "129.0.6668.71", "10.0.0"),
    ("128", "128.0.6613.120", "10.0.0"),
]

GPU_PROFILES = [
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 770 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Series Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
]

SCREEN_RESOLUTIONS = [
    (1920, 1080),
    (1366, 768),
    (1536, 864),
    (1600, 900),
    (1440, 900),
    (2560, 1440),
]

def generate_fake_fingerprint():
    """Tạo bộ thông số vân tay trình duyệt ngẫu nhiên cho mỗi phiên đăng nhập"""
    chrome_major, chrome_full, plat_ver = random.choice(CHROME_VERSIONS)
    gpu_vendor, gpu_renderer = random.choice(GPU_PROFILES)
    screen_w, screen_h = random.choice(SCREEN_RESOLUTIONS)
    cpu_cores = random.choice([4, 6, 8, 12, 16])
    device_ram = random.choice([4, 8, 16, 32])
    touch_points = random.choice([0, 0, 0, 10])  # Desktop chuẩn là 0, một số laptop có touch là 10
    canvas_shift = random.choice([1, 2, 3, -1, -2])
    audio_noise = random.choice([0.00001, 0.00002, -0.00001, -0.00002])
    
    user_agent = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_full} Safari/537.36"
    
    return {
        "chrome_major": chrome_major,
        "chrome_full": chrome_full,
        "plat_ver": plat_ver,
        "user_agent": user_agent,
        "gpu_vendor": gpu_vendor,
        "gpu_renderer": gpu_renderer,
        "screen_w": screen_w,
        "screen_h": screen_h,
        "cpu_cores": cpu_cores,
        "device_ram": device_ram,
        "touch_points": touch_points,
        "canvas_shift": canvas_shift,
        "audio_noise": audio_noise,
    }


async def process_single_account(raw_input_str, email, password, recovery_list, log_signal, result_signal, pause_event):
    await pause_event.wait()
    if isinstance(recovery_list, str):
        recovery_list = [recovery_list] if recovery_list.strip() else []
    elif not recovery_list:
        recovery_list = []

    CLIENT_ID = "b849bc72-dc2c-492f-b087-71e84e926496"
    REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"
    SCOPES = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read https://outlook.office.com/IMAP.AccessAsUser.All"
    
    TARGET_URL = f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&response_mode=query&scope={SCOPES.replace(' ', '%20')}&prompt=consent"

    async with async_playwright() as p:
        browser = None
        launch_errs = []

        # 1. Tìm đường dẫn Google Chrome thật trên máy
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
        ]
        real_chrome = None
        for cp in chrome_paths:
            if os.path.isfile(cp):
                real_chrome = cp
                break

        # Các tham số chặn popup WebAuthn / Passkey của Windows
        chrome_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=WebAuthentication,WebAuthenticationNewWindowUI,WebAuthenticationProxy",
        ]

        # Ưu tiên mở Google Chrome
        if real_chrome:
            try:
                browser = await p.chromium.launch(executable_path=real_chrome, headless=False, args=chrome_args)
            except Exception as ex:
                launch_errs.append(f"Chrome ({real_chrome}): {ex}")

        if not browser:
            try:
                browser = await p.chromium.launch(channel="chrome", headless=False, args=chrome_args)
            except Exception as ex:
                launch_errs.append(f"Channel chrome: {ex}")

        # Fallback về Chromium mặc định của Playwright nếu không có Chrome
        if not browser:
            try:
                browser = await p.chromium.launch(headless=False, args=chrome_args)
            except Exception as ex:
                launch_errs.append(f"Chromium: {ex}")

        if not browser:
            raise RuntimeError("Không thể khởi động Google Chrome:\n" + "\n".join(launch_errs))

        # ========================================================
        # KHỞI TẠO BỘ FAKE FINGERPRINT RIÊNG BIỆT CHO MỖI TÀI KHOẢN
        # ========================================================
        fp = generate_fake_fingerprint()
        short_gpu = fp["gpu_renderer"].split(",")[1].strip() if "," in fp["gpu_renderer"] else fp["gpu_renderer"]
        log_signal.emit(f"🎭 [Fake Fingerprint] Chrome v{fp['chrome_major']} | GPU: {short_gpu} | CPU: {fp['cpu_cores']}C | RAM: {fp['device_ram']}GB | Touch: {fp['touch_points']} | Screen: {fp['screen_w']}x{fp['screen_h']}")

        context = await browser.new_context(
            viewport={'width': 500, 'height': 650},
            user_agent=fp["user_agent"],
            locale="en-US",
            timezone_id="Asia/Ho_Chi_Minh",
            has_touch=(fp["touch_points"] > 0)
        )
        
        # Tiêm mã JS giả lập vân tay + chặn Passkey trước khi tải bất kỳ trang web nào
        stealth_script = f"""
        (() => {{
            // 1. Chặn triệt để Windows Hello / Passkey popup của Windows
            if (window.PublicKeyCredential) {{
                window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable = () => Promise.resolve(false);
                if (window.PublicKeyCredential.isConditionalMediationAvailable) {{
                    window.PublicKeyCredential.isConditionalMediationAvailable = () => Promise.resolve(false);
                }}
            }}
            if (window.navigator && window.navigator.credentials) {{
                window.navigator.credentials.create = () => Promise.reject(new DOMException("The user canceled the operation.", "NotAllowedError"));
                window.navigator.credentials.get = () => Promise.reject(new DOMException("The user canceled the operation.", "NotAllowedError"));
            }}

            // 2. Ẩn navigator.webdriver
            try {{
                Object.defineProperty(navigator, 'webdriver', {{
                    get: () => undefined,
                    configurable: true
                }});
            }} catch(e) {{}}

            // 3. Fake Client Hints (navigator.userAgentData) khớp với User-Agent Chrome
            try {{
                if (navigator.userAgentData) {{
                    Object.defineProperty(navigator, 'userAgentData', {{
                        get: () => ({{
                            brands: [
                                {{ brand: "Chromium", version: "{fp['chrome_major']}" }},
                                {{ brand: "Google Chrome", version: "{fp['chrome_major']}" }},
                                {{ brand: "Not=A?Brand", version: "24" }}
                            ],
                            mobile: false,
                            platform: "Windows",
                            getHighEntropyValues: async (hints) => ({{
                                architecture: "x86",
                                bitness: "64",
                                brands: [
                                    {{ brand: "Chromium", version: "{fp['chrome_major']}" }},
                                    {{ brand: "Google Chrome", version: "{fp['chrome_major']}" }},
                                    {{ brand: "Not=A?Brand", version: "24" }}
                                ],
                                fullVersionList: [
                                    {{ brand: "Chromium", version: "{fp['chrome_full']}" }},
                                    {{ brand: "Google Chrome", version: "{fp['chrome_full']}" }},
                                    {{ brand: "Not=A?Brand", version: "24.0.0.0" }}
                                ],
                                mobile: false,
                                model: "",
                                platform: "Windows",
                                platformVersion: "{fp['plat_ver']}",
                                uaFullVersion: "{fp['chrome_full']}"
                            }})
                        }}),
                        configurable: true
                    }});
                }}
            }} catch(e) {{}}

            // 4. Fake Hardware Specs (CPU, RAM)
            try {{
                Object.defineProperty(navigator, 'hardwareConcurrency', {{
                    get: () => {fp['cpu_cores']},
                    configurable: true
                }});
                Object.defineProperty(navigator, 'deviceMemory', {{
                    get: () => {fp['device_ram']},
                    configurable: true
                }});
            }} catch(e) {{}}

            // 5. Fake Touch points (findtouch / maxTouchPoints)
            try {{
                Object.defineProperty(navigator, 'maxTouchPoints', {{
                    get: () => {fp['touch_points']},
                    configurable: true
                }});
            }} catch(e) {{}}

            // 6. Fake Screen Resolution & Color
            try {{
                const sw = {fp['screen_w']};
                const sh = {fp['screen_h']};
                Object.defineProperty(screen, 'width', {{ get: () => sw, configurable: true }});
                Object.defineProperty(screen, 'height', {{ get: () => sh, configurable: true }});
                Object.defineProperty(screen, 'availWidth', {{ get: () => sw, configurable: true }});
                Object.defineProperty(screen, 'availHeight', {{ get: () => sh - 40, configurable: true }});
                Object.defineProperty(screen, 'colorDepth', {{ get: () => 24, configurable: true }});
                Object.defineProperty(screen, 'pixelDepth', {{ get: () => 24, configurable: true }});
            }} catch(e) {{}}

            // 7. Fake WebGL Vendor & Renderer
            try {{
                const getParameter1 = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(param) {{
                    if (param === 37445) return "{fp['gpu_vendor']}";
                    if (param === 37446) return "{fp['gpu_renderer']}";
                    return getParameter1.apply(this, arguments);
                }};
                if (window.WebGL2RenderingContext) {{
                    const getParameter2 = WebGL2RenderingContext.prototype.getParameter;
                    WebGL2RenderingContext.prototype.getParameter = function(param) {{
                        if (param === 37445) return "{fp['gpu_vendor']}";
                        if (param === 37446) return "{fp['gpu_renderer']}";
                        return getParameter2.apply(this, arguments);
                    }};
                }}
            }} catch(e) {{}}

            // 8. Fake Canvas Fingerprint (chèn vi sai ngẫu nhiên từng phiên)
            try {{
                const shift = {fp['canvas_shift']};
                const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
                HTMLCanvasElement.prototype.toDataURL = function(...args) {{
                    const ctx = this.getContext('2d');
                    if (ctx && this.width > 0 && this.height > 0) {{
                        try {{
                            const img = ctx.getImageData(0, 0, Math.min(this.width, 8), Math.min(this.height, 8));
                            for (let i = 0; i < img.data.length; i += 4) {{
                                img.data[i] = (img.data[i] + shift) % 256;
                            }}
                            ctx.putImageData(img, 0, 0);
                        }} catch(err) {{}}
                    }}
                    return origToDataURL.apply(this, args);
                }};

                const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
                CanvasRenderingContext2D.prototype.getImageData = function(...args) {{
                    const imgData = origGetImageData.apply(this, args);
                    if (imgData && imgData.data && imgData.data.length > 0) {{
                        for (let i = 0; i < Math.min(imgData.data.length, 32); i += 4) {{
                            imgData.data[i] = (imgData.data[i] + shift) % 256;
                        }}
                    }}
                    return imgData;
                }};
            }} catch(e) {{}}

            // 9. Fake AudioContext Fingerprint
            try {{
                if (window.AudioBuffer) {{
                    const origGetChannelData = AudioBuffer.prototype.getChannelData;
                    AudioBuffer.prototype.getChannelData = function(...args) {{
                        const data = origGetChannelData.apply(this, args);
                        for (let i = 0; i < Math.min(data.length, 50); i += 5) {{
                            data[i] += {fp['audio_noise']};
                        }}
                        return data;
                    }};
                }}
            }} catch(e) {{}}

            // 10. Fake Chrome Object & Plugins
            try {{
                if (!window.chrome) window.chrome = {{}};
                if (!window.chrome.runtime) window.chrome.runtime = {{}};
                if (!window.chrome.loadTimes) window.chrome.loadTimes = () => ({{}});
                if (!window.chrome.csi) window.chrome.csi = () => ({{}});
            }} catch(e) {{}}
        }})();
        """
        await context.add_init_script(stealth_script)

        await context.clear_cookies()
        await context.clear_permissions()
        
        page = await context.new_page()

        try:
            # ==========================================
            # BƯỚC 1: NHẬP TÀI KHOẢN & BẤM NEXT
            # ==========================================
            await pause_event.wait()
            log_signal.emit(f"🚀 [Bước 1] Nạp trang đăng nhập cho: {email}")
            await page.goto(TARGET_URL, timeout=60000)

            await page.wait_for_selector("input[type='email']", state="visible")
            await page.fill("input[type='email']", email) 
            await page.click("#idSIButton9")
            await asyncio.sleep(2)

            pwd_selector = "input[type='password'], #i0118"

            # ==========================================
            # BƯỚC 2: CHECK KHUNG NHẬP PASS / CLICK USER NẾU KẸT
            # ==========================================
            log_signal.emit(f"🔍 [Bước 2] Kiểm tra khung nhập mật khẩu...")
            for _ in range(8):
                await asyncio.sleep(1)
                content = await page.content()
                
                # Nếu có nút "Use your password" hoặc ô pass trực tiếp thì thoát check
                if "verify your email" in content.lower() or await page.get_by_role("button", name="Use your password").count() > 0:
                    break
                if await page.is_visible(pwd_selector):
                    break
                
                # Nếu không thấy ô pass, check xem có hiển thị tên user để click không
                if await page.locator(f"text='{email}'").count() > 0:
                    log_signal.emit(f"🖱️ Không thấy ô pass, đang click vào tên user...")
                    try:
                        await page.locator(f"text='{email}'").first.click(timeout=3000)
                        await asyncio.sleep(2)
                    except:
                        pass
                    break

            # Xử lý nếu kẹt ở màn hình Verify your email có nút "Use your password"
            content_check = await page.content()
            if "verify your email" in content_check.lower() or await page.get_by_role("button", name="Use your password").count() > 0:
                log_signal.emit(f"🛡️ Phát hiện màn hình xác thực, đang bấm 'Use your password'...")
                try:
                    await page.get_by_role("button", name="Use your password").click(timeout=5000)
                    await asyncio.sleep(2)
                except:
                    pass

            # NHẬP MẬT KHẨU
            log_signal.emit(f"🔑 Đang điền mật khẩu...")
            await page.wait_for_selector(pwd_selector, state="visible", timeout=15000)
            await asyncio.sleep(1)
            await page.fill(pwd_selector, password) 
            log_signal.emit(f"✍️ Đã điền xong mật khẩu.")
            await page.locator(pwd_selector).click(force=True, no_wait_after=True)
            await page.locator(pwd_selector).press("Enter")
            
            try:
                await page.locator("#idSIButton9").click(timeout=2000, force=True)
            except:
                pass
            await asyncio.sleep(3) 

            # ==========================================
            # BƯỚC 3: CHECK KHUNG BẢO MẬT / THÊM EMAIL KHÔI PHỤC & OTP / PASSKEY
            # ==========================================
            log_signal.emit(f"🛡️ [Bước 3] Kiểm tra khung bảo mật / email khôi phục...")
            for _ in range(25):
                await pause_event.wait()
                await asyncio.sleep(1)
                try:
                    content = await page.content()
                except:
                    break

                # Check nếu hiện màn hình Passkey (FIDO) / Windows Hello
                is_passkey_screen = (
                    "fido" in page.url.lower()
                    or "passkey" in content.lower()
                    or "setting up your passkey" in content.lower()
                )
                if is_passkey_screen:
                    log_signal.emit("🛡️ Phát hiện màn hình Passkey, đang tự động bấm Cancel / Skip để bỏ qua...")
                    try:
                        btn_cancel = page.locator("button:has-text('Cancel'), input[value='Cancel'], #idBtn_Back, button:has-text('Hủy'), button:has-text('Skip'), a:has-text('Skip'), a:has-text('Cancel')")
                        if await btn_cancel.count() > 0:
                            await btn_cancel.first.click(timeout=3000)
                            await asyncio.sleep(2)
                            continue
                    except Exception as e:
                        log_signal.emit(f"⚠️ Thử bấm Cancel Passkey: {e}")

                # Luồng Microsoft Fluent mới có thể đổi chữ nút theo từng bước:
                # 1) Help protect your account -> Add email
                # 2) Add an email address -> nhập email -> Next (trước đây là Add email)
                # 3) Enter your code -> 6 ô OTP tách rời
                # Không phụ thuộc riêng vào text "Add email", vì Microsoft đã đổi
                # nút submit của màn hình nhập email thành "Next".
                new_recovery_input = page.locator("#floatingLabelInput10")
                new_primary_button = page.locator("button[data-testid='primaryButton']")
                is_new_recovery_flow = (
                    "help protect your account" in content.lower()
                    or "add an email address" in content.lower()
                    or await new_recovery_input.count() > 0
                )

                if is_new_recovery_flow:
                    log_signal.emit("🛡️ Phát hiện luồng bảo mật Fluent mới...")
                    if not recovery_list:
                        log_signal.emit("⚠️ Không có email khôi phục nào trong danh sách!")
                        break

                    recovery_success = False
                    for rec_idx, cur_recovery in enumerate(recovery_list):
                        rec_email = cur_recovery.split('|')[0].strip() if '|' in cur_recovery else cur_recovery.strip()
                        if not rec_email:
                            continue

                        log_signal.emit(f"📧 [Email {rec_idx+1}/{len(recovery_list)}] Thử email khôi phục: {rec_email}")
                        try:
                            # Ở trang Help protect, phải bấm Add email để mở ô nhập.
                            if not await new_recovery_input.is_visible():
                                add_email_button = page.get_by_role(
                                    "button", name=re.compile(r"^Add email$", re.I)
                                )
                                if await add_email_button.count() > 0:
                                    await add_email_button.click(timeout=10000)
                                    await page.wait_for_selector(
                                        "#floatingLabelInput10",
                                        state="visible",
                                        timeout=15000
                                    )

                            await new_recovery_input.fill("")
                            await new_recovery_input.fill(rec_email)

                            await new_primary_button.wait_for(state="visible", timeout=10000)
                            await new_primary_button.click(timeout=10000)

                            # Chờ xem có xuất hiện ô OTP không
                            otp_appeared = False
                            for _ in range(8):
                                await pause_event.wait()
                                if await page.is_visible("#codeEntry-0"):
                                    otp_appeared = True
                                    break
                                await asyncio.sleep(1)

                            if not otp_appeared:
                                log_signal.emit(f"⚠️ Email {rec_email} không xuất hiện ô OTP (có thể bị giới hạn hoặc lỗi).")
                                if rec_idx + 1 < len(recovery_list):
                                    log_signal.emit("🔄 Đang thử quay lại để đổi sang email khôi phục tiếp theo...")
                                    btn_back = page.locator("button:has-text('Back'), #idBtn_Back, button[aria-label='Back']")
                                    if await btn_back.count() > 0:
                                        await btn_back.first.click(timeout=3000)
                                        await asyncio.sleep(2)
                                continue

                            log_signal.emit(f"⏳ Đang đợi OTP cho email {rec_email}...")
                            otp_code = get_outlook_otp_via_api(cur_recovery, log_signal)
                            if otp_code:
                                otp_digits = str(otp_code).strip()
                                otp_inputs = page.locator("input[id^='codeEntry-']")
                                input_count = await otp_inputs.count()
                                if len(otp_digits) != input_count:
                                    raise RuntimeError(
                                        f"OTP có {len(otp_digits)} số nhưng trang yêu cầu {input_count} số"
                                    )

                                for index, digit in enumerate(otp_digits):
                                    await page.fill(f"#codeEntry-{index}", digit)

                                log_signal.emit(f"✅ Đã điền OTP vào 6 ô từ email {rec_email} thành công.")
                                await asyncio.sleep(4)
                                recovery_success = True
                                break
                            else:
                                log_signal.emit(f"❌ Không lấy được OTP cho email: {rec_email}.")
                                if rec_idx + 1 < len(recovery_list):
                                    log_signal.emit("🔄 Đang tự động đổi sang email khôi phục tiếp theo trong list...")
                                    btn_back = page.locator("button:has-text('Back'), #idBtn_Back, a:has-text('Back'), a:has-text('Cancel')")
                                    if await btn_back.count() > 0:
                                        await btn_back.first.click(timeout=3000)
                                        await asyncio.sleep(2)
                        except Exception as e:
                            log_signal.emit(f"⚠️ Lỗi xử lý email {rec_email}: {str(e)}")
                            if rec_idx + 1 < len(recovery_list):
                                continue

                    if not recovery_success:
                        log_signal.emit("⚠️ Đã thử hết danh sách email khôi phục.")
                    break
                 
                # Kiểm tra nếu dính màn hình bắt buộc nhập email khôi phục ("Let's protect your account")
                if "protect your account" in content.lower() or await page.is_visible("#EmailAddress"):
                    log_signal.emit(f"🛡️ Phát hiện yêu cầu thêm email khôi phục (giao diện cũ)...")
                    if not recovery_list:
                        log_signal.emit("⚠️ Không có email khôi phục nào trong danh sách!")
                        break

                    recovery_success = False
                    for rec_idx, cur_recovery in enumerate(recovery_list):
                        rec_email = cur_recovery.split('|')[0].strip() if '|' in cur_recovery else cur_recovery.strip()
                        if not rec_email:
                            continue

                        log_signal.emit(f"📧 [Email {rec_idx+1}/{len(recovery_list)}] Thử email khôi phục: {rec_email}")
                        try:
                            await page.wait_for_selector("#EmailAddress", state="visible", timeout=10000)
                            await page.fill("#EmailAddress", "")
                            await page.fill("#EmailAddress", rec_email)
                            await asyncio.sleep(1)
                            await page.click("#iNext")
                            await asyncio.sleep(3)
                            
                            # Chờ ô nhập OTP
                            otp_appeared = False
                            for _ in range(8):
                                await pause_event.wait()
                                if await page.is_visible("input[type='tel'], input[name='otc'], input[id*='otc']"):
                                    otp_appeared = True
                                    break
                                await asyncio.sleep(1)

                            if not otp_appeared:
                                log_signal.emit(f"⚠️ Email {rec_email} không mở được ô nhập OTP.")
                                if rec_idx + 1 < len(recovery_list):
                                    btn_back = page.locator("#idBtn_Back, button:has-text('Back')")
                                    if await btn_back.count() > 0:
                                        await btn_back.first.click(timeout=3000)
                                        await asyncio.sleep(2)
                                continue

                            # Bắt mã OTP
                            log_signal.emit(f"⏳ Đang đợi mã OTP từ email: {rec_email}...")
                            otp_code = get_outlook_otp_via_api(cur_recovery, log_signal)
                            if otp_code:
                                await page.fill("input[type='tel'], input[name='otc'], input[id*='otc']", otp_code)
                                await page.click("#iNext, #idSIButton9")
                                await asyncio.sleep(3)
                                recovery_success = True
                                log_signal.emit(f"✅ Đã điền OTP thành công cho email: {rec_email}")
                                break
                            else:
                                log_signal.emit(f"❌ Không lấy được OTP cho email: {rec_email}.")
                                if rec_idx + 1 < len(recovery_list):
                                    log_signal.emit("🔄 Đang thử quay lại để đổi email khôi phục tiếp theo...")
                                    btn_back = page.locator("#idBtn_Back, button:has-text('Back')")
                                    if await btn_back.count() > 0:
                                        await btn_back.first.click(timeout=3000)
                                        await asyncio.sleep(2)
                        except Exception as e:
                            log_signal.emit(f"⚠️ Lỗi thử email {rec_email}: {str(e)}")
                            if rec_idx + 1 < len(recovery_list):
                                continue

                    if not recovery_success:
                        log_signal.emit("⚠️ Đã thử hết danh sách email khôi phục.")
                    break

                if "code=" in page.url or await page.is_visible("button:has-text('Accept')") or "consent" in page.url.lower():
                    break

            # ==========================================
            # BƯỚC 4: XỬ LÝ MÀN HÌNH CẤP QUYỀN (ACCEPT / SKIP) LẤY OAUTH2
            # ==========================================
            log_signal.emit(f"🧭 [Bước 4] Đang xử lý màn hình cấp quyền (Accept/Skip) để lấy OAuth2...")
            sel_accept = "button:has-text('Accept'), button:has-text('Chấp nhận')"
            sel_skip = "a#iShowSkip, .internal-link:has-text('Skip for now')"
            sel_stay = "#idSIButton9"

            for i in range(25):
                await pause_event.wait()
                await asyncio.sleep(0.5)
                if "code=" in page.url: break
                
                try:
                    # Cuộn xuống đáy để đảm bảo nhìn thấy nút Accept
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                    await asyncio.sleep(0.5)

                    # Bỏ qua màn hình Passkey nếu xuất hiện ở bước này
                    if "fido" in page.url.lower() or "passkey" in (await page.content()).lower():
                        btn_cancel = page.locator("button:has-text('Cancel'), input[value='Cancel'], #idBtn_Back, button:has-text('Hủy'), a:has-text('Cancel')")
                        if await btn_cancel.count() > 0:
                            await btn_cancel.first.click(timeout=3000)
                            await asyncio.sleep(1.5)
                            continue

                    if await page.is_visible(sel_accept):
                        log_signal.emit(f"📝 Đã thấy bảng Accept, đang bấm...")
                        await page.click(sel_accept)
                        await asyncio.sleep(2) 
                        continue
                    
                    if await page.is_visible(sel_skip):
                        log_signal.emit(f"🛡️ Nhấn Skip for now...")
                        await page.click(sel_skip, force=True)
                        await asyncio.sleep(2) 
                        continue 
                except:
                    continue
                
                if await page.is_visible(sel_stay):
                    btn_text = await page.inner_text(sel_stay)
                    if btn_text in ["Yes", "Có", "Next", "Tiếp theo", "Tiếp tục"]:
                        await page.click(sel_stay)

            # LẤY CODE VÀ ĐỔI REFRESH TOKEN
            log_signal.emit(f"⏳ Đợi Redirect lấy Auth Code...")
            try:
                await page.wait_for_url("**/nativeclient?code=*", timeout=30000)
                final_url = page.url
            except:
                log_signal.emit(f"❌ Không bắt được URL chứa Code.")
                return

            if "code=" in final_url:
                auth_code = final_url.split("code=")[1].split("&")[0]
                auth_code = urllib.parse.unquote(auth_code) 
                log_signal.emit(f"✅ Đã bốc được Auth Code.")

                log_signal.emit(f"🔄 Đang đổi sang Refresh Token...")
                payload = {
                    "client_id": CLIENT_ID,
                    "scope": "offline_access https://graph.microsoft.com/Mail.Read",
                    "code": auth_code,
                    "redirect_uri": REDIRECT_URI,
                    "grant_type": "authorization_code"
                }

                response = requests.post(
                    "https://login.microsoftonline.com/common/oauth2/v2.0/token", 
                    data=payload, 
                    timeout=15
                )
                
                res_data = response.json()
                refresh_token = res_data.get('refresh_token')

                if refresh_token:
                    # Tự động cắt bỏ token cũ nếu chuỗi đầu vào đã có sẵn token ở đuôi
                    parts = [p.strip() for p in raw_input_str.split('|') if p.strip()]
                    _, _, email_idx = extract_email_and_password(raw_input_str)
                    
                    if email_idx != -1 and (email_idx + 1 < len(parts)):
                        # Chỉ giữ lại từ đầu đến hết mật khẩu email (loại bỏ token cũ phía sau nếu có)
                        base_parts = parts[:email_idx + 2]
                        base_str = "|".join(base_parts)
                    else:
                        base_str = raw_input_str.strip()

                    final_result = f"{base_str}|{refresh_token}|{CLIENT_ID}"
                    log_signal.emit(f"💾 LẤY THÀNH CÔNG:")
                    result_signal.emit(final_result)
                    
                    log_signal.emit(f"🌐 Đang mở Outlook.com để load giao diện hộp thư...")
                    await page.goto("https://outlook.live.com/mail/0/", timeout=60000)
                    log_signal.emit(f"🛑 Đã vào Outlook thành công. Trình duyệt đứng im tại đây.")
                    
                    while True:
                        await pause_event.wait()
                        await asyncio.sleep(1)
                else:
                    error_desc = res_data.get('error_description', 'Lỗi không xác định')
                    log_signal.emit(f"❌ Microsoft từ chối: {error_desc}")

        except Exception as e:
            log_signal.emit(f"⚠️ Lỗi trong trình duyệt: {str(e)[:50]}")

# --- WORKER CHẠY LUỒNG RIÊNG ---
class AutomationWorker(QThread):
    log_signal = pyqtSignal(str)
    result_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, target_acc, recovery_list):
        super().__init__()
        self.target_acc = target_acc
        if isinstance(recovery_list, list):
            self.recovery_list = recovery_list
        elif recovery_list:
            self.recovery_list = [recovery_list]
        else:
            self.recovery_list = []
        self.recovery_acc = self.recovery_list[0] if self.recovery_list else ""
        self.loop = None
        self.pause_event = None

    def run(self):
        email, password, email_idx = extract_email_and_password(self.target_acc)
        if not email:
            self.log_signal.emit("❌ Không nhận diện được email trong chuỗi nhập (cần chứa ký tự @ và .com)!")
            self.finished_signal.emit()
            return
        if not password:
            self.log_signal.emit(f"❌ Không tìm thấy mật khẩu email ở phía sau email '{email}'!")
            self.finished_signal.emit()
            return

        self.log_signal.emit(f"🎯 Nhận diện thành công -> Email: {email} | Mật khẩu: {password}")

        if sys.platform == "win32":
            try:
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            except Exception:
                pass
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self.pause_event = asyncio.Event()
        self.pause_event.set() 

        try:
            loop.run_until_complete(process_single_account(self.target_acc, email, password, self.recovery_list, self.log_signal, self.result_signal, self.pause_event))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self.log_signal.emit(f"❌ Lỗi luồng: {str(e)}")
            safe_print(f"Lỗi luồng chi tiết:\n{tb}")
        finally:
            loop.close()
            self.finished_signal.emit()

    def pause(self):
        if self.pause_event:
            self.pause_event.clear()

    def resume(self):
        if self.pause_event:
            self.pause_event.set()

    def stop(self):
        if self.pause_event:
            self.pause_event.set() 
        if self.loop and self.loop.is_running():
            self.loop.stop()


# ========================================================
# QUẢN LÝ TỆP TIN & HỘP THOẠI DANH SÁCH (RECOVERY & TIKTOK)
# ========================================================
def get_app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR = get_app_dir()
RECOVERY_EMAILS_FILE = os.path.join(APP_DIR, "recovery_emails.txt")
NICKNAMES_FILE = os.path.join(APP_DIR, "nicknames.txt")
AVATARS_DIR = os.path.join(APP_DIR, "avatars")

os.makedirs(AVATARS_DIR, exist_ok=True)

def load_recovery_emails():
    if not os.path.isfile(RECOVERY_EMAILS_FILE):
        return []
    try:
        with open(RECOVERY_EMAILS_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except Exception:
        return []

def save_recovery_emails(email_list):
    try:
        with open(RECOVERY_EMAILS_FILE, "w", encoding="utf-8") as f:
            for item in email_list:
                if item.strip():
                    f.write(item.strip() + "\n")
    except Exception as e:
        safe_print(f"Lỗi lưu recovery emails: {e}")

def load_nicknames():
    if not os.path.isfile(NICKNAMES_FILE):
        return []
    try:
        with open(NICKNAMES_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except Exception:
        return []

def save_nicknames(nick_list):
    try:
        with open(NICKNAMES_FILE, "w", encoding="utf-8") as f:
            for item in nick_list:
                if item.strip():
                    f.write(item.strip() + "\n")
    except Exception as e:
        safe_print(f"Lỗi lưu nicknames: {e}")

def get_random_avatar():
    if not os.path.isdir(AVATARS_DIR):
        return None
    valid_exts = (".png", ".jpg", ".jpeg", ".webp")
    imgs = [os.path.join(AVATARS_DIR, f) for f in os.listdir(AVATARS_DIR) if f.lower().endswith(valid_exts)]
    return random.choice(imgs) if imgs else None

def get_random_nickname():
    nicks = load_nicknames()
    return random.choice(nicks) if nicks else None


class RecoveryEmailsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quản Lý Danh Sách Email Khôi Phục")
        self.resize(680, 480)
        self.setStyleSheet("""
            QDialog { background-color: #121212; color: #ffffff; }
            QLabel { color: #e0e0e0; font-size: 13px; }
            QTextEdit { background-color: #1e1e1e; color: #03dac6; border: 1px solid #333; border-radius: 4px; font-family: Consolas, monospace; font-size: 12px; }
            QPushButton { border-radius: 4px; padding: 8px 16px; font-weight: bold; }
        """)

        layout = QVBoxLayout(self)
        
        lbl_info = QLabel("<b>Nhập danh sách email khôi phục (mỗi dòng: tk|mk|outh2|clientId):</b><br><span style='color: #888;'>Khi gặp lỗi (hết OTP, bị chặn), tool sẽ tự động thử lần lượt các email tiếp theo trong danh sách.</span>")
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        self.text_edit = QTextEdit()
        emails = load_recovery_emails()
        self.text_edit.setPlainText("\n".join(emails))
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("💾 Lưu Danh Sách")
        self.btn_save.setStyleSheet("background-color: #00897b; color: white;")
        self.btn_save.clicked.connect(self.save_and_close)

        self.btn_cancel = QPushButton("Đóng")
        self.btn_cancel.setStyleSheet("background-color: #424242; color: white;")
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def save_and_close(self):
        content = self.text_edit.toPlainText().strip()
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        save_recovery_emails(lines)
        QMessageBox.information(self, "Thông báo", f"Đã lưu thành công {len(lines)} email khôi phục!")
        self.accept()


class NicknamesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quản Lý Danh Sách Tên Nick (Random)")
        self.resize(500, 480)
        self.setStyleSheet("""
            QDialog { background-color: #121212; color: #ffffff; }
            QLabel { color: #e0e0e0; font-size: 13px; }
            QTextEdit { background-color: #1e1e1e; color: #ffcc80; border: 1px solid #333; border-radius: 4px; font-size: 13px; }
            QPushButton { border-radius: 4px; padding: 8px 16px; font-weight: bold; }
        """)

        layout = QVBoxLayout(self)
        
        lbl_info = QLabel("<b>Nhập danh sách tên nick để đổi ngẫu nhiên trên TikTok (mỗi dòng 1 tên):</b><br><span style='color: #888;'>Nếu danh sách trống, tool sẽ tự động bỏ qua bước đổi tên nick.</span>")
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        self.text_edit = QTextEdit()
        nicks = load_nicknames()
        self.text_edit.setPlainText("\n".join(nicks))
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("💾 Lưu Danh Sách")
        self.btn_save.setStyleSheet("background-color: #5c6bc0; color: white;")
        self.btn_save.clicked.connect(self.save_and_close)

        self.btn_cancel = QPushButton("Đóng")
        self.btn_cancel.setStyleSheet("background-color: #424242; color: white;")
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def save_and_close(self):
        content = self.text_edit.toPlainText().strip()
        lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        save_nicknames(lines)
        QMessageBox.information(self, "Thông báo", f"Đã lưu thành công {len(lines)} tên nick!")
        self.accept()

# ========================================================
# TỰ ĐỘNG DÒ API, KHỞI CHẠY GPM & GIẢI LICENSE
# ========================================================
def find_gpm_exe_path():
    """Tìm đường dẫn tệp thực thi GPMLogin.exe trên máy"""
    candidates = [
        r"C:\Users\DungAnh\AppData\Local\Programs\GPMLogin\GPMLogin.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Programs\GPMLogin\GPMLogin.exe"),
        r"D:\GPMLogin\GPMLogin.exe",
        r"E:\GPMLogin\GPMLogin.exe",
        r"C:\Program Files\GPMLogin\GPMLogin.exe",
        r"C:\Program Files (x86)\GPMLogin\GPMLogin.exe"
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None

def is_gpm_running():
    """Kiểm tra GPMLogin.exe có đang chạy không"""
    for p in psutil.process_iter(['name']):
        if (p.info['name'] or '').lower() == 'gpmlogin.exe':
            return True
    return False

def detect_gpm_api_url():
    """Tự động quét cổng API của GPM (từ file api_port.dat hoặc các cổng thông dụng)"""
    exe_path = find_gpm_exe_path()
    ports_to_try = []

    if exe_path:
        gpm_dir = os.path.dirname(exe_path)
        port_file = os.path.join(gpm_dir, "api_port.dat")
        if os.path.isfile(port_file):
            try:
                with open(port_file, "r", encoding="utf-8", errors="ignore") as f:
                    p = f.read().strip()
                    if p.isdigit():
                        ports_to_try.append(int(p))
            except Exception:
                pass

    for p in [13600, 19955, 50615, 9996]:
        if p not in ports_to_try:
            ports_to_try.append(p)

    for port in ports_to_try:
        url = f"http://127.0.0.1:{port}"
        try:
            resp = requests.get(f"{url}/api/v3/profiles", timeout=1)
            if resp.status_code == 200:
                return f"{url}/api/v3"
        except Exception:
            pass
        try:
            resp = requests.get(f"{url}/v2/profiles", timeout=1)
            if resp.status_code == 200:
                return f"{url}/api/v3"
        except Exception:
            pass

    return None

def auto_detect_or_launch_gpm(log_func=None, update_api_callback=None):
    """
    Tự động quét API của GPM.
    Nếu chưa có GPM thì tự khởi động GPM lên và tự giải License:
    - Gửi phím Enter
    - Kéo chuột xuống góc dưới cùng bên phải màn hình ấn Show Desktop
    - Quét cổng API và cập nhật vào tool
    """
    def log(msg):
        if log_func:
            log_func(msg)
        else:
            safe_print(msg)

    # 1. Nếu GPM đang chạy -> thử quét API ngay
    if is_gpm_running():
        log("🔍 GPMLogin đang chạy -> Đang tự động quét cổng API...")
        api_url = detect_gpm_api_url()
        if api_url:
            log(f"✅ Đã tự động nhận diện GPM Local API: {api_url}")
            if update_api_callback:
                update_api_callback(api_url)
            return api_url

    # 2. Nếu GPM chưa chạy (hoặc đang chạy nhưng không phản hồi API)
    log("🚀 Chưa có GPMLogin hoạt động -> Đang tự động bật GPMLogin...")
    exe_path = find_gpm_exe_path()
    if not exe_path:
        log("❌ Không tìm thấy file GPMLogin.exe trên máy để khởi động tự động!")
        return None

    try:
        subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path))
    except Exception as e:
        log(f"❌ Lỗi khi khởi chạy GPMLogin: {e}")
        return None

    log("⏳ Đang chờ cửa sổ GPMLogin hiển thị (khoảng 3.5 giây)...")
    time.sleep(3.5)

    # Giải License: Ấn Enter
    log("🔑 Đang giải mã License: Tự động gửi phím Enter...")
    try:
        user32 = ctypes.windll.user32
        user32.keybd_event(0x0D, 0, 0, 0)
        time.sleep(0.08)
        user32.keybd_event(0x0D, 0, 2, 0)
    except Exception as e:
        log(f"⚠️ Lỗi gửi phím Enter: {e}")

    time.sleep(1.0)

    # Giải License: Dùng chuột kéo xuống góc dưới bên phải ấn Show Desktop
    log("🖱️ Đang di chuyển chuột xuống góc dưới phải màn hình để nhấn Show Desktop...")
    try:
        user32 = ctypes.windll.user32
        w = user32.GetSystemMetrics(0)
        h = user32.GetSystemMetrics(1)
        target_x = max(0, w - 2)
        target_y = max(0, h - 2)
        user32.SetCursorPos(target_x, target_y)
        time.sleep(0.2)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.08)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.4)
    except Exception as e:
        log(f"⚠️ Lỗi click Show Desktop: {e}")

    time.sleep(1.0)
    log("🔍 Đang quét cổng API của GPMLogin vừa khởi động...")
    
    api_url = None
    for sec in range(15):
        api_url = detect_gpm_api_url()
        if api_url:
            break
        time.sleep(1.0)

    if api_url:
        log(f"🎉 GPMLogin đã khởi động, vượt License và kết nối API thành công: {api_url}")
        if update_api_callback:
            update_api_callback(api_url)
        return api_url
    else:
        log("⚠️ Đã mở GPMLogin nhưng chưa phát hiện cổng API phản hồi. Bạn có thể bấm '🔍 Quét API' để thử lại.")
        return None

# ========================================================
# TỰ ĐỘNG TẠO PROFILE GPM (LOCAL API V2/V3)
# ========================================================
def get_gpm_base_url(api_url):
    """Chuẩn hóa URL GPM về dạng gốc http://127.0.0.1:PORT"""
    url = (api_url or "http://127.0.0.1:13600").strip().rstrip('/')
    if url.endswith('/api/v3'):
        return url[:-7]
    elif url.endswith('/api/v2'):
        return url[:-7]
    elif url.endswith('/v2'):
        return url[:-3]
    return url

def fetch_gpm_groups(api_url):
    """Lấy danh sách các nhóm trên GPM (API v3 /api/v3/groups)"""
    base_url = get_gpm_base_url(api_url)
    try:
        resp = requests.get(f"{base_url}/api/v3/groups", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and "data" in data:
                groups = [g.get("name") for g in data["data"] if g.get("name")]
                if groups:
                    return groups
    except Exception:
        pass
    return ["All", "US", "RU", "MX", "BL", "binance"]

def fetch_all_gpm_profiles(api_url):
    """Lấy toàn bộ danh sách profile hiện có trên GPM (dict: {profile_name: profile_id})"""
    base_url = get_gpm_base_url(api_url)
    profiles_dict = {}
    
    # 1. Thử lấy qua API v2 /v2/profiles
    try:
        resp = requests.get(f"{base_url}/v2/profiles?per_page=10000", timeout=10)
        if resp.status_code == 200:
            items = resp.json()
            if isinstance(items, list):
                for it in items:
                    name = str(it.get("name", "")).strip()
                    pid = str(it.get("id", "")).strip()
                    if name:
                        profiles_dict[name] = pid
                return profiles_dict
    except Exception:
        pass

    # 2. Thử phân trang qua API v3 /api/v3/profiles
    try:
        page = 1
        while True:
            resp = requests.get(f"{base_url}/api/v3/profiles", params={"page": page, "page_size": 100}, timeout=10)
            if resp.status_code == 200:
                payload = resp.json()
                batch = payload.get("data") or []
                for it in batch:
                    name = str(it.get("name", "")).strip()
                    pid = str(it.get("id", "")).strip()
                    if name:
                        profiles_dict[name] = pid
                total = (payload.get("pagination") or {}).get("total", 0)
                if not batch or len(batch) < 100 or len(profiles_dict) >= total:
                    break
                page += 1
            else:
                break
    except Exception:
        pass
    return profiles_dict

def find_gpm_profile(api_url, profile_name):
    """Tìm profile trên GPM theo tên: ưu tiên dùng search của GPM API v3, nếu không thấy sẽ phân trang quét toàn bộ."""
    if not profile_name or not api_url:
        return None
    base_url = get_gpm_base_url(api_url)
    target_name = profile_name.strip().lower()

    # 1. Thử tìm kiếm trực tiếp bằng tham số search qua API v3 (tốc độ cao 50ms)
    endpoints = [f"{base_url}/api/v3/profiles", f"{api_url.rstrip('/')}/profiles"]
    for ep in endpoints:
        try:
            r = requests.get(ep, params={"search": profile_name.strip()}, timeout=10)
            if r.status_code == 200:
                data = r.json().get("data") or []
                match = next((p for p in data if str(p.get("name", "")).strip().lower() == target_name), None)
                if match:
                    return match
        except Exception:
            pass

    # 2. Phân trang tìm kiếm nếu search không ra (hỗ trợ tài khoản có nhiều trang profile)
    try:
        page = 1
        while True:
            r = requests.get(f"{base_url}/api/v3/profiles", params={"page": page, "page_size": 100}, timeout=10)
            if r.status_code != 200:
                break
            payload = r.json()
            batch = payload.get("data") or []
            if not batch:
                break
            match = next((p for p in batch if str(p.get("name", "")).strip().lower() == target_name), None)
            if match:
                return match
            total = (payload.get("pagination") or {}).get("total", 0)
            if len(batch) < 100 or (total and page * 100 >= total):
                break
            page += 1
    except Exception:
        pass
    return None

def create_single_gpm_profile(api_url, name, group="All", proxy="", canvas=True, font=True, webrtc=True, client_rect=True, webgl=True, audio=True):
    """Tạo 1 profile mới trên GPM với đầy đủ 4 chế độ Noise (Canvas, ClientRect, WebGL, Audio) đều ON"""
    base_url = get_gpm_base_url(api_url)

    # 1. Thử tạo qua API v3 (/api/v3/profiles/create) để bật đầy đủ cả 4 chế độ Noise ON
    try:
        v3_url = f"{base_url}/api/v3/profiles/create"
        payload = {
            "profile_name": name.strip(),
            "group_name": group.strip() if group else "All",
            "is_noise_canvas": bool(canvas),
            "is_noise_client_rect": bool(client_rect),
            "is_noise_webgl": bool(webgl),
            "is_noise_audio_context": bool(audio),
            "is_masked_font": bool(font),
            "is_masked_webgl_data": True,
            "webrtc_mode": 2 if webrtc else 1
        }
        if proxy and proxy.strip():
            payload["raw_proxy"] = proxy.strip()

        resp = requests.post(v3_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("data") and data["data"].get("id"):
                return data["data"]["id"]
    except Exception as e:
        safe_print(f"Lỗi tạo profile GPM v3: {e}")

    # 2. Dự phòng qua API v2 nếu v3 không phản hồi
    try:
        params = {
            "name": name.strip(),
            "group": group.strip() if group else "All",
            "canvas": "on" if canvas else "off",
            "font": "on" if font else "off",
            "webrtc": "on" if webrtc else "off",
            "is_noise_canvas": "on" if canvas else "off",
            "is_noise_client_rect": "on" if client_rect else "off",
            "is_noise_webgl": "on" if webgl else "off",
            "is_noise_audio_context": "on" if audio else "off"
        }
        if proxy and proxy.strip():
            params["proxy"] = proxy.strip()

        resp = requests.get(f"{base_url}/v2/create", params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") and data.get("profile_id"):
                return data["profile_id"]
    except Exception as e:
        safe_print(f"Lỗi gọi create profile GPM v2: {e}")
    return None

class GpmBulkCreateWorker(QThread):
    progress_signal = pyqtSignal(int, int, str)  # current, total, log_message
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int, int) # created, skipped, failed

    def __init__(self, api_url, profile_names, group="All", proxy="", canvas=True, font=True, webrtc=True, client_rect=True, webgl=True, audio=True, skip_existing=True):
        super().__init__()
        self.api_url = api_url
        self.profile_names = profile_names
        self.group = group
        self.proxy = proxy
        self.canvas = canvas
        self.font = font
        self.webrtc = webrtc
        self.client_rect = client_rect
        self.webgl = webgl
        self.audio = audio
        self.skip_existing = skip_existing
        self.is_running = True

    def run(self):
        total = len(self.profile_names)
        if total == 0:
            self.log_signal.emit("⚠️ Không có tên profile nào được cung cấp.")
            self.finished_signal.emit(0, 0, 0)
            return

        self.log_signal.emit("🔍 Đang kết nối và kiểm tra danh sách profile trên GPM...")
        existing_profiles = fetch_all_gpm_profiles(self.api_url)
        self.log_signal.emit(f"📋 GPM hiện có {len(existing_profiles)} profile đang hoạt động.")

        created_count = 0
        skipped_count = 0
        failed_count = 0

        for i, name in enumerate(self.profile_names):
            if not self.is_running:
                self.log_signal.emit("🛑 Người dùng đã bấm dừng tiến trình tạo profile.")
                break

            current_idx = i + 1
            if self.skip_existing and name in existing_profiles:
                skipped_count += 1
                msg = f"[{current_idx}/{total}] ⏩ '{name}' đã có sẵn trên GPM -> Bỏ qua."
                self.log_signal.emit(msg)
                self.progress_signal.emit(current_idx, total, msg)
                continue

            self.log_signal.emit(f"[{current_idx}/{total}] 🔨 Đang tạo profile: {name} (Nhóm: {self.group})...")
            pid = create_single_gpm_profile(
                self.api_url,
                name,
                group=self.group,
                proxy=self.proxy,
                canvas=self.canvas,
                font=self.font,
                webrtc=self.webrtc,
                client_rect=self.client_rect,
                webgl=self.webgl,
                audio=self.audio
            )

            if pid:
                created_count += 1
                existing_profiles[name] = pid
                msg = f"[{current_idx}/{total}] ✅ Tạo thành công '{name}' (ID: {pid[:8]}...)"
                self.log_signal.emit(msg)
            else:
                failed_count += 1
                msg = f"[{current_idx}/{total}] ❌ Thất bại khi tạo: '{name}'"
                self.log_signal.emit(msg)

            self.progress_signal.emit(current_idx, total, msg)
            time.sleep(0.1)

        self.finished_signal.emit(created_count, skipped_count, failed_count)

    def stop(self):
        self.is_running = False

class GpmBulkCreatorDialog(QDialog):
    def __init__(self, api_url="http://127.0.0.1:13600/api/v3", default_profile="US-45-1", parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚡ Tự Động Tạo Profile GPM Hàng Loạt")
        self.resize(650, 620)
        self.api_url = api_url
        self.worker = None

        self.setStyleSheet("""
            QDialog { background-color: #121212; color: #ffffff; }
            QLabel { color: #e0e0e0; font-size: 13px; }
            QLineEdit, QSpinBox, QComboBox { 
                background-color: #1e1e1e; color: #ffffff; 
                border: 1px solid #333; border-radius: 4px; padding: 6px; font-size: 13px; 
            }
            QComboBox::drop-down { border: none; }
            QTextEdit { 
                background-color: #1a1a1a; color: #03dac6; 
                border: 1px solid #333; border-radius: 4px; font-family: Consolas, monospace; font-size: 12px; 
            }
            QProgressBar {
                border: 1px solid #333; border-radius: 4px; text-align: center; color: white; background-color: #1e1e1e;
            }
            QProgressBar::chunk { background-color: #4caf50; border-radius: 3px; }
            QTabWidget::pane { border: 1px solid #333; background-color: #181818; border-radius: 4px; }
            QTabBar::tab { background: #262626; color: #bbb; padding: 8px 16px; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px; }
            QTabBar::tab:selected { background: #37474f; color: #ffffff; font-weight: bold; }
            QPushButton { border-radius: 4px; padding: 8px 16px; font-weight: bold; }
            QCheckBox { color: #e0e0e0; font-size: 12px; }
        """)

        layout = QVBoxLayout(self)

        # Header info
        lbl_head = QLabel("<b>TỰ ĐỘNG TẠO NHIỀU PROFILE GPM LOGIN VỚI FINGERPRINT SẠCH</b><br>"
                          "<span style='color: #888;'>Hỗ trợ tạo tự động theo dải nhóm (US-45-1 -> US-50-5) hoặc dán danh sách tùy chỉnh. "
                          "Tự động bật chống Canvas, Font, WebRTC bảo vệ tài khoản.</span>")
        lbl_head.setWordWrap(True)
        layout.addWidget(lbl_head)

        # Tabs
        self.tabs = QTabWidget()
        
        # --- TAB 1: Theo dải nhóm & slot ---
        tab_range = QWidget()
        tab_range_layout = QVBoxLayout(tab_range)

        # Phân tích prefix mặc định từ default_profile
        def_prefix = "US-"
        def_from_group = 45
        def_to_group = 50
        def_slots = 5
        if default_profile and "-" in default_profile:
            parts = default_profile.split("-")
            if len(parts) >= 2:
                def_prefix = f"{parts[0]}-"
                try:
                    def_from_group = int(parts[1])
                    def_to_group = def_from_group + 5
                except Exception:
                    pass

        row_prefix = QHBoxLayout()
        row_prefix.addWidget(QLabel("<b>Tiền tố tên (Prefix):</b>"))
        self.input_prefix = QLineEdit()
        self.input_prefix.setText(def_prefix)
        self.input_prefix.setPlaceholderText("Ví dụ: US- hoặc PK-")
        row_prefix.addWidget(self.input_prefix)
        tab_range_layout.addLayout(row_prefix)

        row_group_range = QHBoxLayout()
        row_group_range.addWidget(QLabel("<b>Từ nhóm số:</b>"))
        self.spin_from = QSpinBox()
        self.spin_from.setRange(1, 99999)
        self.spin_from.setValue(def_from_group)
        row_group_range.addWidget(self.spin_from)

        row_group_range.addWidget(QLabel("<b>Đến nhóm số:</b>"))
        self.spin_to = QSpinBox()
        self.spin_to.setRange(1, 99999)
        self.spin_to.setValue(def_to_group)
        row_group_range.addWidget(self.spin_to)

        row_group_range.addWidget(QLabel("<b>Slot mỗi nhóm:</b>"))
        self.spin_slots = QSpinBox()
        self.spin_slots.setRange(1, 50)
        self.spin_slots.setValue(def_slots)
        row_group_range.addWidget(self.spin_slots)
        tab_range_layout.addLayout(row_group_range)

        self.lbl_preview = QLabel()
        self.lbl_preview.setStyleSheet("color: #ffb74d; font-weight: bold; margin-top: 4px;")
        tab_range_layout.addWidget(self.lbl_preview)
        self.update_preview_label()

        self.input_prefix.textChanged.connect(self.update_preview_label)
        self.spin_from.valueChanged.connect(self.update_preview_label)
        self.spin_to.valueChanged.connect(self.update_preview_label)
        self.spin_slots.valueChanged.connect(self.update_preview_label)

        self.tabs.addTab(tab_range, "🔢 Theo Dải Số (Nhóm & Slot)")

        # --- TAB 2: Danh sách tên tùy chỉnh ---
        tab_custom = QWidget()
        tab_custom_layout = QVBoxLayout(tab_custom)
        tab_custom_layout.addWidget(QLabel("<b>Dán danh sách tên profile cần tạo (mỗi dòng 1 tên):</b>"))
        self.text_custom_names = QTextEdit()
        self.text_custom_names.setPlaceholderText("Ví dụ:\nUS-45-1\nUS-45-2\nPK-10-1\n...")
        tab_custom_layout.addWidget(self.text_custom_names)
        self.tabs.addTab(tab_custom, "📝 Danh Sách Tên Tự Do")

        layout.addWidget(self.tabs)

        # Cấu hình Fingerprint & Nhóm GPM
        grp_settings = QGroupBox("Cấu hình Profile & Anti-Detect")
        grp_settings.setStyleSheet("QGroupBox { color: #80cbc4; font-weight: bold; border: 1px solid #333; margin-top: 6px; padding-top: 10px; }")
        grp_layout = QVBoxLayout(grp_settings)

        row_gpm_opt = QHBoxLayout()
        row_gpm_opt.addWidget(QLabel("<b>Nhóm GPM (Group):</b>"))
        self.combo_group = QComboBox()
        self.combo_group.setEditable(True)
        self.load_groups()
        row_gpm_opt.addWidget(self.combo_group, stretch=1)

        btn_refresh_groups = QPushButton("🔄")
        btn_refresh_groups.setToolTip("Lấy lại danh sách nhóm từ GPM")
        btn_refresh_groups.setStyleSheet("background-color: #37474f; color: white; padding: 6px 10px;")
        btn_refresh_groups.clicked.connect(self.load_groups)
        row_gpm_opt.addWidget(btn_refresh_groups)

        row_gpm_opt.addWidget(QLabel("<b>Proxy:</b>"))
        self.input_proxy = QLineEdit()
        self.input_proxy.setPlaceholderText("Để trống nếu không dùng")
        row_gpm_opt.addWidget(self.input_proxy, stretch=1)
        grp_layout.addLayout(row_gpm_opt)

        row_checks_1 = QHBoxLayout()
        self.cb_canvas = QCheckBox("Canvas Noise")
        self.cb_canvas.setChecked(True)
        self.cb_client_rect = QCheckBox("Client Rect Noise")
        self.cb_client_rect.setChecked(True)
        self.cb_webgl = QCheckBox("WebGL Image Noise")
        self.cb_webgl.setChecked(True)
        self.cb_audio = QCheckBox("Audio Noise")
        self.cb_audio.setChecked(True)

        row_checks_1.addWidget(self.cb_canvas)
        row_checks_1.addWidget(self.cb_client_rect)
        row_checks_1.addWidget(self.cb_webgl)
        row_checks_1.addWidget(self.cb_audio)
        grp_layout.addLayout(row_checks_1)

        row_checks_2 = QHBoxLayout()
        self.cb_font = QCheckBox("Fake Font")
        self.cb_font.setChecked(True)
        self.cb_webrtc = QCheckBox("WebRTC Protection")
        self.cb_webrtc.setChecked(True)
        self.cb_skip_existing = QCheckBox("Bỏ qua nếu đã tồn tại")
        self.cb_skip_existing.setChecked(True)

        row_checks_2.addWidget(self.cb_font)
        row_checks_2.addWidget(self.cb_webrtc)
        row_checks_2.addWidget(self.cb_skip_existing)
        grp_layout.addLayout(row_checks_2)

        layout.addWidget(grp_settings)

        # Tiến trình & Log
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setMaximumHeight(150)
        layout.addWidget(self.log_console)

        # Nút hành động
        row_actions = QHBoxLayout()
        self.btn_start = QPushButton("🚀 Bắt Đầu Tạo Profiles")
        self.btn_start.setStyleSheet("background-color: #2e7d32; color: white; font-size: 13px;")
        self.btn_start.clicked.connect(self.start_creation)

        self.btn_stop = QPushButton("⏹ Dừng Lại")
        self.btn_stop.setStyleSheet("background-color: #c62828; color: white; font-size: 13px;")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_creation)

        self.btn_close = QPushButton("Đóng")
        self.btn_close.setStyleSheet("background-color: #424242; color: white; font-size: 13px;")
        self.btn_close.clicked.connect(self.close)

        row_actions.addWidget(self.btn_start, stretch=2)
        row_actions.addWidget(self.btn_stop, stretch=1)
        row_actions.addWidget(self.btn_close, stretch=1)
        layout.addLayout(row_actions)

    def update_preview_label(self):
        prefix = self.input_prefix.text().strip()
        f_group = self.spin_from.value()
        t_group = self.spin_to.value()
        slots = self.spin_slots.value()
        if f_group > t_group:
            self.lbl_preview.setText("⚠️ Số nhóm bắt đầu không được lớn hơn số nhóm kết thúc!")
            return
        total_groups = (t_group - f_group + 1)
        total_profiles = total_groups * slots
        start_name = f"{prefix}{f_group}-1"
        end_name = f"{prefix}{t_group}-{slots}"
        self.lbl_preview.setText(f"📊 Dự kiến tạo: {total_profiles} profiles ({start_name} -> {end_name})")

    def load_groups(self):
        cur_text = self.combo_group.currentText().strip()
        self.combo_group.clear()
        groups = fetch_gpm_groups(self.api_url)
        for g in groups:
            self.combo_group.addItem(g)
        if cur_text:
            idx = self.combo_group.findText(cur_text)
            if idx >= 0:
                self.combo_group.setCurrentIndex(idx)
            else:
                self.combo_group.setEditText(cur_text)
        else:
            idx = self.combo_group.findText("US")
            if idx >= 0:
                self.combo_group.setCurrentIndex(idx)

    def append_log(self, text):
        self.log_console.append(text)
        self.log_console.moveCursor(QTextCursor.MoveOperation.End)

    def start_creation(self):
        names_to_create = []
        if self.tabs.currentIndex() == 0:
            prefix = self.input_prefix.text().strip()
            f_group = self.spin_from.value()
            t_group = self.spin_to.value()
            slots = self.spin_slots.value()
            if f_group > t_group:
                QMessageBox.warning(self, "Lỗi dải số", "Nhóm bắt đầu phải nhỏ hơn hoặc bằng nhóm kết thúc!")
                return
            for g in range(f_group, t_group + 1):
                for s in range(1, slots + 1):
                    names_to_create.append(f"{prefix}{g}-{s}")
        else:
            raw = self.text_custom_names.toPlainText().strip()
            for line in raw.splitlines():
                c = line.strip()
                if c and not c.startswith("#"):
                    names_to_create.append(c)

        if not names_to_create:
            QMessageBox.warning(self, "Thông báo", "Vui lòng nhập hoặc cấu hình ít nhất 1 tên profile!")
            return

        group = self.combo_group.currentText().strip() or "All"
        proxy = self.input_proxy.text().strip()
        canvas = self.cb_canvas.isChecked()
        client_rect = self.cb_client_rect.isChecked()
        webgl = self.cb_webgl.isChecked()
        audio = self.cb_audio.isChecked()
        font = self.cb_font.isChecked()
        webrtc = self.cb_webrtc.isChecked()
        skip_existing = self.cb_skip_existing.isChecked()

        self.progress_bar.setRange(0, len(names_to_create))
        self.progress_bar.setValue(0)
        self.log_console.clear()
        self.append_log(f"🚀 Bắt đầu tạo {len(names_to_create)} profile trên GPM (Nhóm: {group})...")

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)

        self.worker = GpmBulkCreateWorker(
            api_url=self.api_url,
            profile_names=names_to_create,
            group=group,
            proxy=proxy,
            canvas=canvas,
            font=font,
            webrtc=webrtc,
            client_rect=client_rect,
            webgl=webgl,
            audio=audio,
            skip_existing=skip_existing
        )
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.on_worker_progress)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

    def on_worker_progress(self, current, total, msg):
        self.progress_bar.setValue(current)

    def on_worker_finished(self, created, skipped, failed):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.append_log("=" * 45)
        self.append_log(f"🎉 HOÀN TẤT: Tạo mới: {created} | Bỏ qua: {skipped} | Thất bại: {failed}")
        QMessageBox.information(
            self,
            "Hoàn tất tạo Profile",
            f"Đã hoàn thành tiến trình!\n\n"
            f"• Tạo mới thành công: {created}\n"
            f"• Bỏ qua (đã có sẵn): {skipped}\n"
            f"• Thất bại: {failed}"
        )

    def stop_creation(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.btn_stop.setEnabled(False)
            self.append_log("⏳ Đang dừng tiến trình, vui lòng đợi xong profile hiện tại...")


# --- GIAO DIỆN CHÍNH (GUI) ---
class MainWindow(QWidget):
    background_log_signal = pyqtSignal(str)
    vpn_gpm_finished_signal = pyqtSignal()
    gpm_api_detected_signal = pyqtSignal(str)
    otp_received_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.worker = None
        self.vpn_gpm_running = False
        self.background_log_signal.connect(self.update_log)
        self.vpn_gpm_finished_signal.connect(self.on_vpn_gpm_finished)
        self.gpm_api_detected_signal.connect(self.on_gpm_api_detected)
        self.otp_received_signal.connect(self.on_otp_received)
        self.initUI()

        # Tự động quét API / khởi động GPM & giải License sau 1.2s khi mở app
        QTimer.singleShot(1200, self.trigger_scan_or_launch_gpm)

    def on_gpm_api_detected(self, api_url):
        if api_url:
            self.gpm_api_input.setText(api_url)

    def trigger_scan_or_launch_gpm(self):
        import threading
        def run():
            auto_detect_or_launch_gpm(
                log_func=self.background_log_signal.emit,
                update_api_callback=self.gpm_api_detected_signal.emit
            )
        threading.Thread(target=run, daemon=True).start()

    def initUI(self):
        self.setWindowTitle(f'Get OAuth2 Token & Outlook Loader - Hao Automation (v{APP_VERSION})')
        self.setFixedWidth(750)
        self.setFixedHeight(770)
        self.setStyleSheet("""
            QWidget {
                background-color: #121212;
                color: #e0e0e0;
                font-family: 'Segoe UI', Arial;
                font-size: 13px;
            }
            QLabel { 
                color: #bb86fc; 
                font-weight: bold; 
            }
            QLineEdit {
                background-color: #1e1e1e;
                border: 1px solid #333;
                border-radius: 4px;
                padding: 8px;
                color: #ffffff;
            }
            QPushButton {
                border-radius: 4px;
                padding: 10px;
                font-weight: bold;
            }
        """)

        main_layout = QVBoxLayout()

        # Ô 1: Nhập tài khoản Hotmail cần lấy (tk|mk)
        acc_layout = QHBoxLayout()
        self.acc_input = QLineEdit()
        self.acc_input.setPlaceholderText("Nhập tk|mk Hotmail cần lấy OAuth2...")
        acc_layout.addWidget(QLabel("<b>Tài khoản cần lấy:</b>"))
        acc_layout.addWidget(self.acc_input)
        main_layout.addLayout(acc_layout)

        # Ô 2: Nhập / Chọn email khôi phục từ List
        recovery_layout = QHBoxLayout()
        self.recovery_combo = QComboBox()
        self.recovery_combo.setEditable(True)
        self.recovery_combo.lineEdit().setPlaceholderText("Chọn hoặc nhập email khôi phục: tk|mk|outh2|clientId")
        self.recovery_combo.setStyleSheet("""
            QComboBox { background-color: #1e1e1e; border: 1px solid #333; padding: 6px; border-radius: 4px; color: #03dac6; font-size: 12px; }
            QComboBox QAbstractItemView { background-color: #1e1e1e; color: #03dac6; selection-background-color: #00796b; }
        """)
        self.recovery_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.btn_manage_recovery = QPushButton("📋 Danh Sách Email")
        self.btn_manage_recovery.setStyleSheet("padding: 8px 12px; background-color: #00796b; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_manage_recovery.clicked.connect(self.open_recovery_dialog)

        recovery_layout.addWidget(QLabel("<b>Email Khôi Phục:</b>"))
        recovery_layout.addWidget(self.recovery_combo, stretch=1)
        recovery_layout.addWidget(self.btn_manage_recovery)
        main_layout.addLayout(recovery_layout)

        # Ô 3: Tên profile GPM cần mở sau khi VPN đã kết nối thành công
        gpm_layout = QHBoxLayout()
        self.gpm_profile_input = QLineEdit()
        self.gpm_profile_input.setPlaceholderText("Ví dụ: US-45-1")
        self.gpm_profile_input.setText("US-45-1")
        self.gpm_profile_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333; padding: 8px; border-radius: 4px; color: #ffcc80;")
        
        self.btn_bulk_create_gpm = QPushButton("⚡ Tạo Profile Auto")
        self.btn_bulk_create_gpm.setStyleSheet("padding: 8px 14px; background-color: #7b1fa2; color: white; border-radius: 4px; font-weight: bold;")
        self.btn_bulk_create_gpm.setToolTip("Mở công cụ tự động tạo profile GPM hàng loạt không cần bấm tay!")
        self.btn_bulk_create_gpm.clicked.connect(self.open_bulk_create_gpm_dialog)

        gpm_layout.addWidget(QLabel("<b>Profile GPM:</b>"))
        gpm_layout.addWidget(self.gpm_profile_input, stretch=1)
        gpm_layout.addWidget(self.btn_bulk_create_gpm)
        main_layout.addLayout(gpm_layout)

        # Ô 4: Local API GPM có thể thay đổi trực tiếp trên GUI
        gpm_api_layout = QHBoxLayout()
        self.gpm_api_input = QLineEdit()
        self.gpm_api_input.setPlaceholderText("Ví dụ: http://127.0.0.1:13600/api/v3")
        self.gpm_api_input.setText("http://127.0.0.1:13600/api/v3")
        self.gpm_api_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333; padding: 8px; border-radius: 4px; color: #80cbc4;")
        
        self.btn_scan_gpm_api = QPushButton("🔍 Quét API / Mở GPM")
        self.btn_scan_gpm_api.setStyleSheet("padding: 8px 12px; background-color: #00695c; color: white; border-radius: 4px; font-weight: bold;")
        self.btn_scan_gpm_api.setToolTip("Tự động quét cổng API hoặc mở GPMLogin & vượt License nếu chưa chạy!")
        self.btn_scan_gpm_api.clicked.connect(self.trigger_scan_or_launch_gpm)

        gpm_api_layout.addWidget(QLabel("<b>GPM Local API:</b>"))
        gpm_api_layout.addWidget(self.gpm_api_input, stretch=1)
        gpm_api_layout.addWidget(self.btn_scan_gpm_api)
        main_layout.addLayout(gpm_api_layout)

        # Cấu hình TikTok Profile (Đổi avatar & tên nick)
        tiktok_profile_layout = QHBoxLayout()
        
        self.cb_change_nickname = QCheckBox("Đổi tên nick TikTok")
        self.cb_change_nickname.setChecked(True)
        self.cb_change_nickname.setStyleSheet("color: #e0e0e0; font-weight: bold;")
        
        self.btn_manage_nicknames = QPushButton("📝 List Tên Nick")
        self.btn_manage_nicknames.setStyleSheet("padding: 6px 12px; background-color: #5c6bc0; color: white; border-radius: 4px; font-weight: bold;")
        self.btn_manage_nicknames.clicked.connect(self.open_nicknames_dialog)

        self.cb_change_avatar = QCheckBox("Đổi Avatar TikTok")
        self.cb_change_avatar.setChecked(True)
        self.cb_change_avatar.setStyleSheet("color: #e0e0e0; font-weight: bold; margin-left: 15px;")
        
        self.btn_open_avatars_folder = QPushButton("📁 Thư mục Avatar")
        self.btn_open_avatars_folder.setStyleSheet("padding: 6px 12px; background-color: #8d6e63; color: white; border-radius: 4px; font-weight: bold;")
        self.btn_open_avatars_folder.clicked.connect(self.open_avatars_folder)

        tiktok_profile_layout.addWidget(self.cb_change_nickname)
        tiktok_profile_layout.addWidget(self.btn_manage_nicknames)
        tiktok_profile_layout.addWidget(self.cb_change_avatar)
        tiktok_profile_layout.addWidget(self.btn_open_avatars_folder)
        tiktok_profile_layout.addStretch()
        main_layout.addLayout(tiktok_profile_layout)



        # --- 1. DÒNG 1: Ô KẾT QUẢ + COPY KẾT QUẢ + LẤY OTP ---
        result_container = QVBoxLayout()
        result_container.addWidget(QLabel("<b>Kết quả (userid|tk|mk|outh2|clientId):</b>"))
        
        row1 = QHBoxLayout()
        self.result_output = QLineEdit()
        self.result_output.setReadOnly(True)
        self.result_output.setStyleSheet("background-color: #1a2e1a; border: 1px solid #2e7d32; padding: 6px; border-radius: 4px; color: #81c784; font-weight: bold;")
        
        btn_copy_res = QPushButton("Copy kết quả")
        btn_copy_res.setStyleSheet("background-color: #2e7d32; color: white; padding: 6px 12px; border-radius: 4px; font-weight: bold;")
        btn_copy_res.clicked.connect(self.copy_result)

        btn_get_otp_top = QPushButton("Lấy OTP")
        btn_get_otp_top.setStyleSheet("background-color: #0288d1; color: white; padding: 6px 12px; border-radius: 4px; font-weight: bold;")
        btn_get_otp_top.clicked.connect(self.fetch_and_copy_otp)
        
        row1.addWidget(self.result_output)
        row1.addWidget(btn_copy_res)
        row1.addWidget(btn_get_otp_top)
        result_container.addLayout(row1)
        main_layout.addLayout(result_container)

        # --- 2. DÒNG 2: COPY TÀI KHOẢN + COPY MẬT KHẨU + COPY OTP + PLAY VPN+GPM + ĐỔI TÊN+AVATAR ---
        row2 = QHBoxLayout()
        
        btn_copy_tk = QPushButton("Copy Tài Khoản")
        btn_copy_tk.setStyleSheet("padding: 10px; background-color: #00897b; color: white; font-weight: bold; border-radius: 4px;")
        btn_copy_tk.clicked.connect(self.copy_account_only)

        btn_copy_mk = QPushButton("Copy Mật Khẩu")
        btn_copy_mk.setStyleSheet("padding: 10px; background-color: #5e35b1; color: white; font-weight: bold; border-radius: 4px;")
        btn_copy_mk.clicked.connect(self.copy_password_only)

        btn_copy_otp = QPushButton("Copy OTP")
        btn_copy_otp.setStyleSheet("padding: 10px; background-color: #d81b60; color: white; font-weight: bold; border-radius: 4px;")
        btn_copy_otp.clicked.connect(self.fetch_and_copy_otp)

        # Nút ở vị trí thứ 4: Copy Link & Đổi VPN sang US
        self.btn_play_vpn_gpm = QPushButton("Play VPN+GPM")
        self.btn_play_vpn_gpm.setStyleSheet("padding: 10px; background-color: #37474f; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_play_vpn_gpm.clicked.connect(self.copy_link_and_vpn)
        self.btn_play_vpn_gpm.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Nút ở vị trí thứ 5: Đổi Tên + Avatar TikTok
        self.btn_change_name_avatar = QPushButton("Đổi Tên + Avatar")
        self.btn_change_name_avatar.setStyleSheet("padding: 10px; background-color: #e65100; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_change_name_avatar.clicked.connect(self.trigger_change_name_avatar)
        self.btn_change_name_avatar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Đưa cả 5 nút vào row2 với stretch=1 để bằng nhau hoàn toàn
        row2.addWidget(btn_copy_tk, stretch=1)
        row2.addWidget(btn_copy_mk, stretch=1)
        row2.addWidget(btn_copy_otp, stretch=1)
        row2.addWidget(self.btn_play_vpn_gpm, stretch=1)
        row2.addWidget(self.btn_change_name_avatar, stretch=1)
        main_layout.addLayout(row2)

        # --- 3. DÒNG 3: PLAY + TẠM DỪNG + STOP + XÓA LOG + CẬP NHẬT ---
        row3 = QHBoxLayout()
        
        self.btn_start = QPushButton("PLAY")
        self.btn_start.setStyleSheet("padding: 10px; background-color: #4caf50; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_start.clicked.connect(self.start_process)

        self.btn_pause = QPushButton("TẠM DỪNG")
        self.btn_pause.setStyleSheet("padding: 10px; background-color: #ff9800; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_pause.clicked.connect(self.toggle_pause)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setStyleSheet("padding: 10px; background-color: #cf6679; color: white; font-weight: bold; border-radius: 4px;")
        self.btn_stop.clicked.connect(self.stop_process)

        self.btn_clear = QPushButton("XÓA LOG")
        self.btn_clear.setStyleSheet("padding: 10px; background-color: #333; color: #bbb; border-radius: 4px; font-weight: bold;")
        self.btn_clear.clicked.connect(lambda: self.log_output.clear())

        self.btn_update = QPushButton("🔄 CẬP NHẬT")
        self.btn_update.setStyleSheet("padding: 10px; background-color: #1565c0; color: white; border-radius: 4px; font-weight: bold;")
        self.btn_update.clicked.connect(self.manual_check_update)

        # Đưa cả 5 nút vào row3 với stretch=1 để khớp thẳng cột với row2
        row3.addWidget(self.btn_start, stretch=1)
        row3.addWidget(self.btn_pause, stretch=1)
        row3.addWidget(self.btn_stop, stretch=1)
        row3.addWidget(self.btn_clear, stretch=1)
        row3.addWidget(self.btn_update, stretch=1)
        main_layout.addLayout(row3)

        # Khu vực hiển thị Log
        main_layout.addWidget(QLabel("<b>Trạng thái hệ thống:</b>"))
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("""
            background-color: #000000; 
            color: #00ff00; 
            font-family: 'Consolas', monospace; 
            font-size: 10pt; 
            border-radius: 4px;
            padding: 10px;
            border: 1px solid #333;
        """)
        main_layout.addWidget(self.log_output)

        self.setLayout(main_layout)
        self.refresh_recovery_combo()

    def refresh_recovery_combo(self):
        current_text = self.recovery_combo.currentText().strip()
        self.recovery_combo.clear()
        emails = load_recovery_emails()
        for em in emails:
            self.recovery_combo.addItem(em)
        if current_text:
            idx = self.recovery_combo.findText(current_text)
            if idx >= 0:
                self.recovery_combo.setCurrentIndex(idx)
            else:
                self.recovery_combo.setEditText(current_text)

    def open_recovery_dialog(self):
        dlg = RecoveryEmailsDialog(self)
        if dlg.exec():
            self.refresh_recovery_combo()
            self.log_output.append(f"📋 Đã cập nhật danh sách email khôi phục ({len(load_recovery_emails())} email).")

    def open_nicknames_dialog(self):
        dlg = NicknamesDialog(self)
        if dlg.exec():
            nicks = load_nicknames()
            self.log_output.append(f"📝 Đã cập nhật danh sách tên nick ({len(nicks)} tên).")

    def open_bulk_create_gpm_dialog(self):
        current_profile = self.gpm_profile_input.text().strip()
        api_url = self.gpm_api_input.text().strip()
        dlg = GpmBulkCreatorDialog(api_url=api_url, default_profile=current_profile, parent=self)
        dlg.exec()

    def open_avatars_folder(self):
        os.makedirs(AVATARS_DIR, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(AVATARS_DIR)
            else:
                subprocess.run(["xdg-open", AVATARS_DIR])
            self.log_output.append(f"📁 Đã mở thư mục avatar: {AVATARS_DIR}")
        except Exception as e:
            self.log_output.append(f"⚠️ Không thể mở thư mục avatar: {e}")

    def get_recovery_list(self):
        current_input = self.recovery_combo.currentText().strip()
        emails = load_recovery_emails()
        
        result_list = []
        if current_input:
            result_list.append(current_input)
            
        for em in emails:
            if em not in result_list:
                result_list.append(em)
                
        return result_list

    def run_tiktok_profile_update(self, remote_address, chosen_nick, chosen_avatar):
        """Tự động hoàn toàn từ Trang chủ -> Vào Profile -> Bấm Edit profile -> Thay Avatar & Tên Nick -> Bấm Lưu qua CDP."""
        if not chosen_nick and not chosen_avatar:
            return

        def update_thread():
            try:
                self.background_log_signal.emit(f"🔄 [TikTok] Đang kết nối tới trình duyệt qua CDP ({remote_address})...")
                patch_playwright_driver()
                from playwright.sync_api import sync_playwright

                with sync_playwright() as p:
                    # 1. Kết nối trực tiếp vào GPM Chrome đang mở
                    browser = p.chromium.connect_over_cdp(remote_address)
                    contexts = browser.contexts
                    if not contexts:
                        self.background_log_signal.emit("⚠️ [TikTok] Không tìm thấy browser context trên GPM.")
                        return

                    context = contexts[0]
                    pages = context.pages

                    # Tìm tab TikTok đang mở hoặc lấy tab hiện tại
                    target_page = None
                    for pg in pages:
                        if "tiktok.com" in pg.url:
                            target_page = pg
                            break

                    if not target_page:
                        if pages:
                            target_page = pages[0]
                            self.background_log_signal.emit("🌐 [TikTok] Đang mở trang tiktok.com...")
                            target_page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=30000)
                        else:
                            target_page = context.new_page()
                            self.background_log_signal.emit("🌐 [TikTok] Đang mở trang tiktok.com...")
                            target_page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=30000)

                    # 2. BƯỚC 1: TỰ ĐỘNG VÀO TRANG CÁ NHÂN (PROFILE) TỪ TRANG CHỦ
                    self.background_log_signal.emit(f"🔍 [TikTok] Đang ở URL: {target_page.url}")
                    target_page.wait_for_timeout(2500)

                    # Kiểm tra xem đã ở sẵn trong Profile chưa (URL có chứa /@)
                    if "/@" not in target_page.url:
                        self.background_log_signal.emit("🚀 [Bước 1: Từ Trang chủ] Đang tự động tìm lối vào Profile...")
                        
                        # Cách 1: Nút Profile ở thanh điều hướng trái (theo đúng HTML người dùng gửi)
                        profile_btn = target_page.locator('a[data-e2e="nav-profile"]')
                        if not profile_btn.count():
                            profile_btn = target_page.locator('button[aria-label="Profile"]')
                        if not profile_btn.count():
                            profile_btn = target_page.locator('a[href*="/@"]')

                        clicked_nav = False
                        if profile_btn.count() > 0:
                            profile_href = profile_btn.first.get_attribute("href")
                            self.background_log_signal.emit(f"👆 [TikTok] Tìm thấy nút Profile ({profile_href or 'nav-profile'}). Đang chuyển hướng...")
                            try:
                                if profile_href:
                                    full_url = profile_href if profile_href.startswith("http") else f"https://www.tiktok.com{profile_href}"
                                    target_page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
                                    clicked_nav = True
                                else:
                                    profile_btn.first.click(timeout=5000)
                                    clicked_nav = True
                            except Exception:
                                try:
                                    profile_btn.first.click(timeout=5000)
                                    clicked_nav = True
                                except Exception:
                                    pass

                        # Cách 2: Nếu chưa vào được, bấm vào Avatar góc trên bên phải (header-more-menu-icon)
                        if not clicked_nav and "/@" not in target_page.url:
                            header_icon = target_page.locator('#header-more-menu-icon, div[data-e2e="profile-icon"]')
                            if header_icon.count() > 0:
                                self.background_log_signal.emit("👆 [TikTok] Nhấp vào icon Avatar góc trên bên phải để mở menu...")
                                try:
                                    header_icon.first.click(timeout=4000)
                                    target_page.wait_for_timeout(1000)
                                    view_prof = target_page.locator('a[href*="/@"], li:has-text("View profile"), div:has-text("View profile")')
                                    if view_prof.count() > 0:
                                        view_prof.first.click(timeout=4000)
                                except Exception as err:
                                    self.background_log_signal.emit(f"⚠️ [TikTok] Menu avatar: {err}")

                    # Chờ trang Profile tải xong
                    target_page.wait_for_timeout(3500)
                    self.background_log_signal.emit(f"📍 [Bước 2: Trang cá nhân] Đã vào: {target_page.url}")

                    # 3. BƯỚC 2: TỰ ĐỘNG BẤM NÚT 'EDIT PROFILE' (SỬA HỒ SƠ)
                    self.background_log_signal.emit("🔍 [TikTok] Đang tìm nút 'Edit profile' (Sửa hồ sơ)...")
                    edit_btn = target_page.locator('[data-e2e="edit-profile-entrance"], button:has-text("Edit profile"), button:has-text("Sửa hồ sơ"), [data-e2e="edit-profile-endpoint"], button:has-text("Edit")')
                    
                    # Nếu chưa thấy ngay, cuộn nhẹ trang để nạp
                    if not edit_btn.count():
                        target_page.mouse.wheel(0, 100)
                        target_page.wait_for_timeout(1000)
                        edit_btn = target_page.locator('[data-e2e="edit-profile-entrance"], button:has-text("Edit profile"), button:has-text("Sửa hồ sơ"), [data-e2e="edit-profile-endpoint"], button:has-text("Edit")')

                    if edit_btn.count() > 0:
                        self.background_log_signal.emit("👆 [TikTok] Đã tìm thấy nút 'Edit profile'. Đang mở popup chỉnh sửa...")
                        edit_btn.first.click(timeout=5000)
                    else:
                        self.background_log_signal.emit("⚠️ [TikTok] Không thấy nút 'Edit profile'. Đang thử tìm theo icon sửa...")
                        icon_edit = target_page.locator('[data-e2e="edit-profile-icon"]')
                        if icon_edit.count() > 0:
                            icon_edit.first.click(timeout=5000)

                    target_page.wait_for_timeout(2000)

                    # 4. BƯỚC 3: THAY ĐỔI AVATAR (NẾU CÓ)
                    if chosen_avatar and os.path.isfile(chosen_avatar):
                        abs_avatar = os.path.abspath(chosen_avatar)
                        self.background_log_signal.emit(f"🖼️ [Bước 3: Thay Avatar] Đang tải ảnh: '{os.path.basename(abs_avatar)}'...")
                        file_input = target_page.locator('input[type="file"]')
                        if file_input.count() > 0:
                            try:
                                file_input.first.set_input_files(abs_avatar, timeout=7000)
                                self.background_log_signal.emit("✅ [TikTok] Đã chọn file ảnh thành công! Đang chờ popup cắt ảnh...")
                                target_page.wait_for_timeout(2500)

                                # Tìm và bấm nút Apply của modal cắt ảnh
                                # Lưu ý: Tuyệt đối KHÔNG gộp "Save" vào đây vì Save là nút của form Edit Profile (đang bị disable).
                                self.background_log_signal.emit("👆 [TikTok] Tìm và bấm nút Apply (Cắt ảnh)...")
                                apply_btn = target_page.locator('div[role="dialog"] button:has-text("Apply"), div[role="dialog"] button:has-text("Áp dụng"), button:has-text("Apply"), button:has-text("Áp dụng")')
                                
                                clicked_apply = False
                                for _ in range(5):
                                    if apply_btn.count() > 0:
                                        try:
                                            apply_btn.first.click(force=True, timeout=3000)
                                            clicked_apply = True
                                            self.background_log_signal.emit("✅ [TikTok] Đã bấm nút Apply cắt ảnh (Playwright).")
                                            break
                                        except Exception:
                                            pass
                                    target_page.wait_for_timeout(1000)

                                if not clicked_apply:
                                    try:
                                        target_page.evaluate('''() => {
                                            const btns = Array.from(document.querySelectorAll('button'));
                                            for (const b of btns) {
                                                const t = (b.innerText || '').trim().toLowerCase();
                                                if (t === 'apply' || t === 'áp dụng') { b.click(); return; }
                                            }
                                        }''')
                                    except Exception:
                                        pass

                                # Chờ popup cắt ảnh đóng lại hoàn toàn để không che khuất các ô bên dưới
                                target_page.wait_for_timeout(3000)
                            except Exception as err:
                                self.background_log_signal.emit(f"⚠️ [TikTok] Lỗi khi upload/cắt avatar: {err}")
                        else:
                            self.background_log_signal.emit("⚠️ [TikTok] Không tìm thấy ô input[type='file'] để nạp avatar.")

                    # 5. BƯỚC 4: THAY ĐỔI TÊN NICK (NẾU CÓ)
                    if chosen_nick:
                        self.background_log_signal.emit(f"📝 [Bước 4: Thay Tên Nick] Đang điền tên: '{chosen_nick}'...")
                        nick_input = target_page.locator('input[placeholder="Name"], input[placeholder="Tên"], input[name="nickname"], div[data-e2e="edit-profile-name-input"] input')
                        if nick_input.count() > 0:
                            try:
                                nick_elem = nick_input.first
                                nick_elem.click(force=True, timeout=3000)
                                target_page.wait_for_timeout(500)

                                # Dùng tổ hợp phím thực tế để xóa và gõ tên mới (kích hoạt đầy đủ React state)
                                target_page.keyboard.press("Control+A")
                                target_page.keyboard.press("Backspace")
                                target_page.wait_for_timeout(300)
                                target_page.keyboard.type(chosen_nick, delay=35)
                                target_page.wait_for_timeout(500)

                                self.background_log_signal.emit(f"✅ [TikTok] Đã điền tên nick: '{chosen_nick}'.")
                            except Exception as err:
                                self.background_log_signal.emit(f"⚠️ [TikTok] Lỗi khi điền tên: {err}")
                        else:
                            self.background_log_signal.emit("⚠️ [TikTok] Không tìm thấy ô nhập tên nick.")

                    # 6. BƯỚC 5: BẤM NÚT LƯU (SAVE)
                    self.background_log_signal.emit("💾 [Bước 5: Lưu hồ sơ] Đang chờ nút Save sẵn sàng...")
                    save_btn = target_page.locator('button[data-e2e="edit-profile-save"], button:has-text("Save"), button:has-text("Lưu")')
                    if save_btn.count() > 0:
                        try:
                            # Đợi nút Save được kích hoạt (enabled) - tối đa 8 giây
                            save_ready = False
                            for _ in range(8):
                                try:
                                    if not save_btn.first.is_disabled():
                                        save_ready = True
                                        break
                                except Exception:
                                    pass
                                target_page.wait_for_timeout(1000)

                            if save_ready:
                                save_btn.first.click(force=True, timeout=5000)
                                self.background_log_signal.emit("✅ [TikTok] Đã bấm nút Save lưu thay đổi!")
                            else:
                                self.background_log_signal.emit("⚠️ [TikTok] Nút Save vẫn đang disabled, click cưỡng bức (force=True)...")
                                save_btn.first.click(force=True, timeout=4000)

                            target_page.wait_for_timeout(2000)

                            # Chờ và bấm modal xác nhận nếu có (Confirm tên chỉ được đổi 7 ngày 1 lần)
                            self.background_log_signal.emit("🔍 [TikTok] Kiểm tra modal xác nhận đổi tên (Confirm)...")
                            confirm_modal = target_page.locator('div[role="dialog"] button:has-text("Confirm"), div[role="dialog"] button:has-text("Xác nhận"), div[role="dialog"] button:has-text("Change"), div[role="dialog"] button:has-text("Đổi"), button:has-text("Confirm"), button:has-text("Xác nhận")')
                            
                            clicked_confirm = False
                            for _ in range(5):
                                if confirm_modal.count() > 0:
                                    try:
                                        self.background_log_signal.emit("👆 [TikTok] Đã phát hiện modal xác nhận. Đang bấm Confirm...")
                                        confirm_modal.first.click(force=True, timeout=3000)
                                        clicked_confirm = True
                                        break
                                    except Exception:
                                        pass
                                target_page.wait_for_timeout(1000)

                            if not clicked_confirm:
                                try:
                                    target_page.evaluate('''() => {
                                        const dialogs = document.querySelectorAll('div[role="dialog"]');
                                        for (const d of dialogs) {
                                            const btns = d.querySelectorAll('button');
                                            for (const b of btns) {
                                                const txt = (b.innerText || '').trim().toLowerCase();
                                                if (txt === 'confirm' || txt === 'xác nhận' || txt === 'change' || txt === 'đổi') {
                                                    b.click();
                                                    return true;
                                                }
                                            }
                                        }
                                        return false;
                                    }''')
                                except Exception:
                                    pass

                            # Chờ máy chủ TikTok xử lý lưu
                            self.background_log_signal.emit("⏳ [TikTok] Đang đợi TikTok lưu thông tin lên máy chủ...")
                            target_page.wait_for_timeout(4000)

                            # 7. BƯỚC 6: KIỂM TRA LẠI HTML VÀ TỰ ĐỘNG F5 (RELOAD) NẾU CHƯA THAY ĐỔI
                            self.background_log_signal.emit("🔍 [Bước 6: Kiểm tra HTML] Đang kiểm tra xem thông tin hồ sơ đã cập nhật chưa...")
                            
                            info_updated = False
                            for check_attempt in range(1, 4):
                                target_page.wait_for_timeout(2000)
                                
                                current_title = ""
                                try:
                                    current_title = target_page.title()
                                except Exception:
                                    pass

                                current_content = ""
                                try:
                                    current_content = target_page.content()
                                except Exception:
                                    pass

                                nick_matched = True
                                if chosen_nick:
                                    nick_matched = (chosen_nick.lower() in current_title.lower()) or (chosen_nick.lower() in current_content.lower())

                                if nick_matched:
                                    info_updated = True
                                    self.background_log_signal.emit(f"✅ [TikTok] Đã kiểm tra HTML: Thông tin đã đổi thành công! (Tên: '{chosen_nick}')")
                                    break
                                else:
                                    self.background_log_signal.emit(f"🔄 [TikTok] (Lần {check_attempt}/3) Kiểm tra HTML chưa thấy thông tin đổi, đang F5 (Reload) lại trang...")
                                    try:
                                        target_page.reload(wait_until="domcontentloaded", timeout=25000)
                                        target_page.wait_for_timeout(3500)
                                    except Exception as reload_err:
                                        self.background_log_signal.emit(f"⚠️ [TikTok] Lỗi khi F5 reload: {reload_err}")

                            if not info_updated and chosen_nick:
                                self.background_log_signal.emit(f"⚠️ [TikTok] Lưu ý: Đã F5 3 lần nhưng chưa thấy tên '{chosen_nick}' xuất hiện trên giao diện. TikTok có thể đang kiểm duyệt hoặc máy chủ đồng bộ chậm.")

                            self.background_log_signal.emit("🎉 [TikTok] QUY TRÌNH ĐỔI TÊN & AVATAR ĐÃ HOÀN TẤT THÀNH CÔNG!")
                        except Exception as err:
                            self.background_log_signal.emit(f"⚠️ [TikTok] Lỗi khi bấm Lưu: {err}")
                    else:
                        self.background_log_signal.emit("⚠️ [TikTok] Không tìm thấy nút Save. Vui lòng kiểm tra lại modal.")

            except Exception as ex:
                self.background_log_signal.emit(f"⚠️ [TikTok] Lỗi quy trình cập nhật hồ sơ: {str(ex)}")

        threading.Thread(target=update_thread, daemon=True).start()

    def connect_vpn_us_background(self, profile_name, api_url):
        """Đổi VPN sang US; chỉ khi thành công mới mở đúng profile GPM."""
        self.log_output.append("🌐 [VPN] Bắt đầu tiến trình đổi IP sang US qua ExpressVPN...")
        
        def run_vpn():
            try:
                cmd_path = r'cd /d "C:\Program Files\ExpressVPN" && .\expressvpnctl'
                
                # Danh sách server US mặc định hoặc đọc từ file countries.txt
                us_slugs = ["usa-new-york", "usa-los-angeles-1", "usa-san-francisco", "usa-miami", "usa-chicago", "usa-dallas"]
                random.shuffle(us_slugs)
                
                # Thử kết nối lần lượt
                for attempt in range(min(len(us_slugs), 3)):
                    server_slug = us_slugs[attempt]
                    self.background_log_signal.emit(f"🌐 [Lần {attempt+1}] Đang kết nối ExpressVPN tới: {server_slug.upper()}...")
                    
                    subprocess.run(f'{cmd_path} disconnect', shell=True, capture_output=True)
                    time.sleep(1.5)
                    subprocess.run(f'{cmd_path} connect "{server_slug}"', shell=True, capture_output=True)
                    
                    # Kiểm tra trạng thái trong 8 giây
                    is_connected = False
                    for _ in range(4):
                        time.sleep(2)
                        res = subprocess.run(f'{cmd_path} status', shell=True, capture_output=True, text=True)
                        status_text = res.stdout.strip()
                        if "Connected to" in status_text or "connectionstate: Connected" in status_text or "Connected" in status_text:
                            is_connected = True
                            break
                    
                    if is_connected:
                        self.background_log_signal.emit(f"🌍 [Bước 2] VPN đã kết nối US ({server_slug.upper()}) thành công!")
                        self.open_gpm_profile_and_tiktok(profile_name, api_url)
                        return
                    else:
                        self.background_log_signal.emit(f"⚠️ [VPN] Server {server_slug} phản hồi chậm, đang thử cụm khác...")
                 
                self.background_log_signal.emit("❌ [VPN] Không thể kết nối tới các cụm IP US; không mở GPM.")
            except Exception as e:
                self.background_log_signal.emit(f"❌ [VPN] Lỗi thực thi ExpressVPN: {str(e)}")
            finally:
                self.vpn_gpm_finished_signal.emit()

        # Chạy bằng luồng phụ tránh đơ app
        import threading
        threading.Thread(target=run_vpn, daemon=True).start()

    def open_gpm_profile_and_tiktok(self, profile_name, api_url):
        """Tìm profile theo đúng tên, mở bằng GPM API v3 rồi mở TikTok qua CDP."""
        if not is_gpm_running():
            self.background_log_signal.emit("🚀 Phát hiện GPMLogin chưa mở -> Đang tự động mở và giải License...")
            new_api = auto_detect_or_launch_gpm(log_func=self.background_log_signal.emit)
            if new_api:
                api_url = new_api
                self.gpm_api_detected_signal.emit(new_api)

        api_url = api_url.rstrip('/')
        try:
            self.background_log_signal.emit(f"🧭 [Bước 3] Đang tìm profile GPM: {profile_name}")
            target = find_gpm_profile(api_url, profile_name)
            if not target:
                self.background_log_signal.emit(f"⚡ Profile '{profile_name}' chưa có trên GPM -> Đang tự động gọi API tạo mới...")
                # Tự đoán group từ prefix tên (vd US-45-1 -> US, RU-10-1 -> RU, PK-1-1 -> PK)
                guess_group = "All"
                if "-" in profile_name:
                    p = profile_name.split("-")[0].strip()
                    if p:
                        guess_group = p
                new_pid = create_single_gpm_profile(api_url, profile_name, group=guess_group, canvas=True, font=True, webrtc=True)
                if new_pid:
                    self.background_log_signal.emit(f"✅ Đã tự động tạo profile '{profile_name}' (ID: {new_pid[:8]}...) thành công!")
                    target = {"id": new_pid, "name": profile_name}
                else:
                    raise RuntimeError(f"Không tìm thấy và không thể tự động tạo profile: {profile_name}")

            response = requests.get(
                f"{api_url}/profiles/start/{target['id']}", timeout=30
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success"):
                raise RuntimeError(payload.get("message", "GPM từ chối mở profile"))

            remote_address = (payload.get("data") or {}).get("remote_debugging_address")
            if not remote_address:
                raise RuntimeError("GPM không trả về remote_debugging_address")

            if not remote_address.startswith(("http://", "https://")):
                remote_address = f"http://{remote_address}"

            # Chrome DevTools HTTP endpoint mở URL trong đúng profile vừa bật.
            time.sleep(2)
            open_url = f"{remote_address}/json/new?{urllib.parse.quote('https://www.tiktok.com/', safe=':/')}"
            tab_response = requests.put(open_url, timeout=15)
            tab_response.raise_for_status()
            self.background_log_signal.emit(
                f"✅ Đã mở profile {profile_name} và vào tiktok.com thành công."
            )

            # Mô típ kiểm tra đổi tên nick & avatar TikTok
            do_nick = self.cb_change_nickname.isChecked()
            do_avatar = self.cb_change_avatar.isChecked()

            if do_nick or do_avatar:
                chosen_nick = get_random_nickname() if do_nick else None
                chosen_avatar = get_random_avatar() if do_avatar else None

                if do_nick:
                    if chosen_nick:
                        self.background_log_signal.emit(f"🎯 [TikTok] Tên nick ngẫu nhiên: '{chosen_nick}'")
                    else:
                        self.background_log_signal.emit("ℹ️ [TikTok] File nicknames.txt trống, bỏ qua đổi tên.")

                if do_avatar:
                    if chosen_avatar:
                        avatar_name = os.path.basename(chosen_avatar)
                        self.background_log_signal.emit(f"🖼️ [TikTok] Ảnh avatar ngẫu nhiên: '{avatar_name}'")
                    else:
                        self.background_log_signal.emit("ℹ️ [TikTok] Thư mục avatars/ trống, bỏ qua đổi avatar.")

                self.run_tiktok_profile_update(remote_address, chosen_nick, chosen_avatar)
        except Exception as e:
            self.background_log_signal.emit(f"❌ [GPM] {str(e)}")

    def on_vpn_gpm_finished(self):
        self.vpn_gpm_running = False
        self.btn_play_vpn_gpm.setEnabled(True)

    def copy_link_and_vpn(self):
        """Bước 1 đổi VPN, bước 2 xác nhận, bước 3 mở GPM và TikTok."""
        profile_name = self.gpm_profile_input.text().strip()
        api_url = self.gpm_api_input.text().strip()
        if not profile_name:
            self.log_output.append("⚠️ Vui lòng nhập tên profile GPM, ví dụ US-45-1.")
            return
        if not api_url:
            self.log_output.append("⚠️ Vui lòng nhập địa chỉ GPM Local API.")
            return
        if not api_url.startswith(("http://", "https://")):
            self.log_output.append("⚠️ GPM Local API phải bắt đầu bằng http:// hoặc https://.")
            return
        if self.vpn_gpm_running:
            self.log_output.append("⚠️ Quy trình VPN+GPM đang chạy.")
            return

        self.vpn_gpm_running = True
        self.btn_play_vpn_gpm.setEnabled(False)
        self.log_output.append(
            f"🚀 [Bước 1] Bắt đầu VPN+GPM với profile: {profile_name} | API: {api_url}"
        )
        self.connect_vpn_us_background(profile_name, api_url)

    def trigger_change_name_avatar(self, *args):
        """Kích hoạt đổi Tên Nick và Avatar trên Profile GPM đang mở hoặc mở mới."""
        try:
            profile_name = self.gpm_profile_input.text().strip()
            api_url = self.gpm_api_input.text().strip()
            if not profile_name:
                self.log_output.append("⚠️ Vui lòng nhập tên profile GPM, ví dụ US-45-1.")
                return
            if not api_url:
                self.log_output.append("⚠️ Vui lòng nhập địa chỉ GPM Local API.")
                return
            if not api_url.startswith(("http://", "https://")):
                self.log_output.append("⚠️ GPM Local API phải bắt đầu bằng http:// hoặc https://.")
                return

            do_nick = self.cb_change_nickname.isChecked()
            do_avatar = self.cb_change_avatar.isChecked()

            if not do_nick and not do_avatar:
                self.log_output.append("⚠️ Cả 2 tùy chọn 'Đổi tên nick' và 'Đổi Avatar' đều đang tắt. Hãy tích chọn ít nhất 1 mục!")
                return

            chosen_nick = get_random_nickname() if do_nick else None
            chosen_avatar = get_random_avatar() if do_avatar else None

            if do_nick and not chosen_nick and do_avatar and not chosen_avatar:
                self.log_output.append("⚠️ File nicknames.txt và thư mục avatars/ đều trống!")
                return

            self.log_output.append(f"🚀 [Bấm Đổi Tên + Avatar] Profile: {profile_name}...")
            if do_nick:
                if chosen_nick:
                    self.log_output.append(f"🎯 [TikTok] Tên nick ngẫu nhiên: '{chosen_nick}'")
                else:
                    self.log_output.append("ℹ️ [TikTok] File nicknames.txt trống, không đổi tên.")

            if do_avatar:
                if chosen_avatar:
                    self.log_output.append(f"🖼️ [TikTok] Ảnh avatar ngẫu nhiên: '{os.path.basename(chosen_avatar)}'")
                else:
                    self.log_output.append("ℹ️ [TikTok] Thư mục avatars/ trống, không đổi avatar.")

            def run_change():
                nonlocal api_url
                try:
                    if not is_gpm_running():
                        self.background_log_signal.emit("⚠️ Phát hiện GPMLogin chưa mở -> Đang tự động mở và giải License...")
                        new_api = auto_detect_or_launch_gpm(log_func=self.background_log_signal.emit)
                        if new_api:
                            api_url = new_api
                            self.gpm_api_detected_signal.emit(new_api)

                    api = api_url.rstrip('/')
                    self.background_log_signal.emit(f"🧭 [GPM] Đang kiểm tra profile: {profile_name}...")
                    
                    # Tìm profile
                    target = find_gpm_profile(api, profile_name)
                    if not target:
                        self.background_log_signal.emit(f"❌ [GPM] Không tìm thấy profile: {profile_name}")
                        return

                    # Mở profile nếu chưa mở để lấy remote_debugging_address
                    self.background_log_signal.emit(f"🚀 [GPM] Đang kết nối tới profile {profile_name}...")
                    start_res = requests.get(f"{api}/profiles/start/{target['id']}", timeout=30)
                    start_res.raise_for_status()
                    start_payload = start_res.json()
                    if not start_payload.get("success"):
                        raise RuntimeError(start_payload.get("message", "GPM từ chối mở profile"))

                    remote_address = (start_payload.get("data") or {}).get("remote_debugging_address")
                    if not remote_address:
                        raise RuntimeError("GPM không trả về remote_debugging_address")

                    if not remote_address.startswith(("http://", "https://")):
                        remote_address = f"http://{remote_address}"

                    self.run_tiktok_profile_update(remote_address, chosen_nick, chosen_avatar)
                except Exception as ex:
                    self.background_log_signal.emit(f"❌ [Đổi Tên+Avatar] Lỗi: {str(ex)}")

            threading.Thread(target=run_change, daemon=True).start()
        except Exception as err:
            self.log_output.append(f"❌ [Lỗi giao diện]: {str(err)}")

    def advance_gpm_profile(self):
        """US-45-1 ... US-45-5 -> US-46-1; luôn đọc giá trị hiện tại trên GUI."""
        current = self.gpm_profile_input.text().strip()
        match = re.fullmatch(r"(.+)-(\d+)-(\d+)", current)
        if not match:
            self.log_output.append(
                f"⚠️ Không tăng profile '{current}': cần định dạng như US-45-1."
            )
            return

        prefix, group_text, slot_text = match.groups()
        group_number = int(group_text)
        slot_number = int(slot_text)
        if slot_number < 1 or slot_number > 5:
            self.log_output.append("⚠️ Số cuối profile phải nằm trong khoảng 1 đến 5.")
            return

        if slot_number < 5:
            next_profile = f"{prefix}-{group_text}-{slot_number + 1}"
        else:
            next_group = str(group_number + 1).zfill(len(group_text))
            next_profile = f"{prefix}-{next_group}-1"

        self.gpm_profile_input.setText(next_profile)
        self.log_output.append(f"🔁 Profile kế tiếp: {current} → {next_profile}")


    def start_process(self):
        target_acc = self.acc_input.text().strip()
        recovery_list = self.get_recovery_list()

        if not target_acc:
            self.log_output.append("⚠️ Vui lòng nhập tài khoản cần lấy OAuth2!")
            return

        email, password, _ = extract_email_and_password(target_acc)
        if not email:
            self.log_output.append("⚠️ Chuỗi tài khoản cần chứa email hợp lệ (có dấu hiệu @ và .com)!")
            return
            
        if not password:
            self.log_output.append(f"⚠️ Không tìm thấy mật khẩu email ở phía sau email '{email}'!")
            return

        if not recovery_list:
            self.log_output.append("⚠️ Vui lòng nhập hoặc thêm ít nhất một email khôi phục dạng tk|mk|outh2|clientId!")
            return

        self.btn_start.setEnabled(False)
        self.result_output.clear()
        
        self.worker = AutomationWorker(target_acc, recovery_list)
        self.worker.log_signal.connect(self.update_log)
        self.worker.result_signal.connect(self.show_result)
        self.worker.finished_signal.connect(lambda: self.btn_start.setEnabled(True))
        self.worker.start()
        
        self.log_output.append(f"🚀 Đã bấm PLAY -> Nhận diện Email: {email} | Danh sách Khôi phục: {len(recovery_list)} mail | Trình duyệt đang khởi động...")

    def toggle_pause(self):
        if not self.worker or not self.worker.isRunning():
            self.log_output.append("⚠️ Tiến trình chưa chạy để có thể tạm dừng!")
            return
            
        if self.btn_pause.text() == "TẠM DỪNG":
            self.worker.pause()
            self.btn_pause.setText("TIẾP TỤC")
            self.btn_pause.setStyleSheet("padding: 12px; background-color: #2196f3; color: white; font-weight: bold; border-radius: 4px;")
            self.log_output.append("⏸️ Đã TẠM DỪNG trình duyệt. Sếp có thể tha hồ soi HTML/Element trên Chrome!")
        else:
            self.worker.resume()
            self.btn_pause.setText("TẠM DỪNG")
            self.btn_pause.setStyleSheet("padding: 12px; background-color: #ff9800; color: white; font-weight: bold; border-radius: 4px;")
            self.log_output.append("▶️ Đã TIẾP TỤC chạy lại tiến trình!")

    def stop_process(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.terminate()
            self.btn_start.setEnabled(True)
            self.btn_pause.setText("TẠM DỪNG")
            self.btn_pause.setStyleSheet("padding: 12px; background-color: #ff9800; color: white; font-weight: bold; border-radius: 4px;")
            self.log_output.append("🛑 Đã bấm STOP, đã tắt tiến trình và đóng trình duyệt!")
        
        # Tự động ngắt kết nối ExpressVPN khi bấm Stop
        try:
            cmd_path = r'cd /d "C:\Program Files\ExpressVPN" && .\expressvpnctl'
            subprocess.run(f'{cmd_path} disconnect', shell=True, capture_output=True)
            self.log_output.append("🌐 [VPN] Đã tự động ngắt kết nối (Disconnect ExpressVPN).")
        except Exception as e:
            self.log_output.append(f"⚠️ [VPN] Không thể ngắt VPN tự động: {str(e)}")

        self.acc_input.clear()
        self.advance_gpm_profile()

    def copy_result(self):
        text_to_copy = self.result_output.text().strip()
        if text_to_copy:
            clipboard = QApplication.clipboard()
            clipboard.setText(text_to_copy)
            self.log_output.append("📋 Đã copy kết quả vào bộ nhớ tạm thành công!")
        else:
            self.log_output.append("⚠️ Chưa có kết quả để copy!")

    def copy_account_only(self):
        acc_text = self.acc_input.text().strip()
        email, _, _ = extract_email_and_password(acc_text)
        if email:
            QApplication.clipboard().setText(email)
            self.log_output.append(f"📋 Đã copy Tài khoản email: {email}")
        else:
            self.log_output.append("⚠️ Không tìm thấy tài khoản email hợp lệ để copy!")

    def copy_password_only(self):
        acc_text = self.acc_input.text().strip()
        _, password, _ = extract_email_and_password(acc_text)
        if password:
            QApplication.clipboard().setText(password)
            self.log_output.append(f"📋 Đã copy Mật khẩu email: {password}")
        else:
            self.log_output.append("⚠️ Không tìm thấy mật khẩu email để copy!")

    def on_otp_received(self, otp):
        """Nhận kết quả OTP từ luồng ngầm và copy vào clipboard an toàn trên main thread"""
        if otp:
            QApplication.clipboard().setText(otp)
            self.log_output.append(f"🎯 ĐÃ COPY MÃ OTP VÀO BỘ NHỚ TẠM: {otp}")
        else:
            self.log_output.append("❌ Không tìm thấy mã OTP nào từ tài khoản này!")

    def fetch_and_copy_otp(self):
        """Quét và lấy mã OTP qua API ngầm trong luồng riêng, không làm đơ/lag giao diện (GUI)"""
        # Lấy chuỗi từ ô kết quả (nếu chưa có thì thử lấy ở ô tài khoản nhập vào)
        result_acc = self.result_output.text().strip()
        if not result_acc or '|' not in result_acc:
            result_acc = self.acc_input.text().strip()
        
        if not result_acc or '|' not in result_acc:
            self.log_output.append("⚠️ Chưa có kết quả tài khoản hoặc chuỗi không hợp lệ để lấy OTP (cần có OAuth2|ClientID)!")
            return
        
        # Tách chuỗi kết quả: dạng userid|tk|mk|outh2|clientid hoặc tk|mk|outh2|clientid hoặc outh2|clientid
        parts = result_acc.split('|')
        
        # Kiểm tra xem chuỗi có đủ thông tin oauth2 và clientid không
        if len(parts) >= 4:
            # Lấy 2 phần cuối cùng làm oAuth2 và Client ID
            outh2 = parts[-2].strip()
            client_id = parts[-1].strip()
            target_recovery_str = f"dummy|dummy|{outh2}|{client_id}"
        elif len(parts) == 2:
            outh2 = parts[0].strip()
            client_id = parts[1].strip()
            target_recovery_str = f"dummy|dummy|{outh2}|{client_id}"
        else:
            self.log_output.append("❌ Chuỗi chưa đủ định dạng OAuth2 và Client ID để lấy OTP (cần tk|mk|oauth2|client_id)!")
            return
        
        self.log_output.append("🚀 Đang khởi chạy luồng quét OTP ngầm (giao diện không bị lag)...")
        
        def run_otp_worker():
            class ThreadSignal:
                def __init__(self, emit_fn):
                    self.emit_fn = emit_fn
                def emit(self, msg):
                    self.emit_fn(msg)

            sig = ThreadSignal(self.background_log_signal.emit)
            try:
                otp = get_outlook_otp_via_api(target_recovery_str, sig)
                self.otp_received_signal.emit(otp or "")
            except Exception as e:
                self.background_log_signal.emit(f"❌ Lỗi trong luồng lấy OTP: {str(e)}")
                self.otp_received_signal.emit("")

        threading.Thread(target=run_otp_worker, daemon=True).start()

    def update_log(self, message):
        self.log_output.append(message)
        self.log_output.moveCursor(QTextCursor.MoveOperation.End)

    def show_result(self, result_str):
        self.result_output.setText(result_str)

    def manual_check_update(self):
        self.btn_update.setEnabled(False)
        self.log_output.append("🔄 Đang kiểm tra cập nhật từ GitHub...")
        QApplication.processEvents()
        has_updated = check_auto_update(silent=False, parent_widget=self)
        if not has_updated:
            self.btn_update.setEnabled(True)

def main():
    # Tự động kiểm tra bản cập nhật mới từ GitHub mỗi lần khởi động tool
    check_auto_update(silent=False)

    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        engine_file = os.path.join(exe_dir, "logacc_engine.py")
        if os.path.isfile(engine_file):
            try:
                safe_print("⚡ Đang nạp phiên bản cập nhật mới nhất từ logacc_engine.py...")
                spec = importlib.util.spec_from_file_location("logacc_dynamic", engine_file)
                mod = importlib.util.module_from_spec(spec)
                sys.modules["logacc_dynamic"] = mod
                spec.loader.exec_module(mod)
                if hasattr(mod, "MainWindow"):
                    app = QApplication(sys.argv)
                    window = mod.MainWindow()
                    window.show()
                    sys.exit(app.exec())
            except Exception as e:
                safe_print(f"⚠️ Lỗi nạp engine cập nhật ({e}), chạy phiên bản mặc định...")

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
