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

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QTextEdit, QLineEdit, QLabel, QSizePolicy, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor

from playwright.async_api import async_playwright   

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

# --- CẤU HÌNH PHIÊN BẢN & TỰ ĐỘNG CẬP NHẬT TỪ GITHUB ---
APP_VERSION = "1.0.0"
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


async def process_single_account(email, password, recovery_acc, log_signal, result_signal, pause_event, user_id=""):
    await pause_event.wait()

    CLIENT_ID = "b849bc72-dc2c-492f-b087-71e84e926496"
    REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"
    SCOPES = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read https://outlook.office.com/IMAP.AccessAsUser.All"
    
    TARGET_URL = f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id={CLIENT_ID}&response_type=code&redirect_uri={REDIRECT_URI}&response_mode=query&scope={SCOPES.replace(' ', '%20')}&prompt=consent"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False) 
        context = await browser.new_context(viewport={'width': 500, 'height': 650})
        
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
            # BƯỚC 3: CHECK KHUNG BẢO MẬT / THÊM EMAIL KHÔI PHỤC & OTP
            # ==========================================
            log_signal.emit(f"🛡️ [Bước 3] Kiểm tra khung bảo mật / email khôi phục...")
            for _ in range(15):
                await pause_event.wait()
                await asyncio.sleep(1)
                try:
                    content = await page.content()
                except:
                    break

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
                    recovery_email = recovery_acc.split('|')[0].strip()
                    log_signal.emit("🛡️ Phát hiện luồng bảo mật Fluent mới...")
                    try:
                        # Ở trang Help protect, phải bấm Add email để mở ô nhập.
                        if not await new_recovery_input.is_visible():
                            add_email_button = page.get_by_role(
                                "button", name=re.compile(r"^Add email$", re.I)
                            )
                            await add_email_button.click(timeout=10000)
                            await page.wait_for_selector(
                                "#floatingLabelInput10",
                                state="visible",
                                timeout=15000
                            )

                        log_signal.emit(f"📧 Đang thêm email khôi phục: {recovery_email}")
                        await new_recovery_input.fill(recovery_email)

                        # HTML hiện tại: <button data-testid="primaryButton">Next</button>
                        # Dùng data-testid ổn định, không dùng class Fluent (hay thay đổi).
                        await new_primary_button.wait_for(state="visible", timeout=10000)
                        await new_primary_button.click(timeout=10000)

                        await page.wait_for_selector(
                            "#codeEntry-0",
                            state="visible",
                            timeout=15000
                        )
                        log_signal.emit("⏳ Đang đợi OTP cho luồng bảo mật mới...")
                        otp_code = get_outlook_otp_via_api(recovery_acc, log_signal)
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

                            log_signal.emit("✅ Đã điền OTP vào 6 ô của giao diện mới.")
                            await asyncio.sleep(4)
                        else:
                            log_signal.emit("❌ Không lấy được OTP cho giao diện bảo mật mới.")
                    except Exception as e:
                        log_signal.emit(f"⚠️ Lỗi xử lý luồng Add email mới: {str(e)}")
                    break
                 
                # Kiểm tra nếu dính màn hình bắt buộc nhập email khôi phục ("Let's protect your account")
                if "protect your account" in content.lower() or await page.is_visible("#EmailAddress"):
                    log_signal.emit(f"🛡️ Phát hiện yêu cầu thêm email khôi phục. Đang điền...")
                    try:
                        recovery_email = recovery_acc.split('|')[0].strip()
                        log_signal.emit(f"📧 Email khôi phục: {recovery_email}")
                        await page.wait_for_selector("#EmailAddress", state="visible", timeout=10000)
                        await page.fill("#EmailAddress", recovery_email)
                        await asyncio.sleep(1)
                        await page.click("#iNext")
                        await asyncio.sleep(3)
                        
                        # Bắt mã OTP
                        log_signal.emit(f"⏳ Đang đợi mã OTP từ email khôi phục...")
                        for _ in range(20):
                            await pause_event.wait()
                            await asyncio.sleep(1)
                            if await page.is_visible("input[type='tel'], input[name='otc'], input[id*='otc']"):
                                otp_code = get_outlook_otp_via_api(recovery_acc, log_signal)
                                if otp_code:
                                    await page.fill("input[type='tel'], input[name='otc'], input[id*='otc']", otp_code)
                                    await page.click("#iNext, #idSIButton9")
                                    await asyncio.sleep(3)
                                    break
                    except Exception as e:
                        log_signal.emit(f"⚠️ Lỗi xử lý bảo mật: {str(e)}")
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
                    if user_id:
                        final_result = f"{user_id}|{email.strip()}|{password.strip()}|{refresh_token}|{CLIENT_ID}"
                    else:
                        if user_id:
                            final_result = f"{user_id}|{email.strip()}|{password.strip()}|{refresh_token}|{CLIENT_ID}"
                        else:
                            final_result = f"{email.strip()}|{password.strip()}|{refresh_token}|{CLIENT_ID}"
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

    def __init__(self, target_acc, recovery_acc):
        super().__init__()
        self.target_acc = target_acc
        self.recovery_acc = recovery_acc
        self.loop = None
        self.pause_event = None

    def run(self):
        parts = [p.strip() for p in self.target_acc.split('|') if p.strip()]
        if len(parts) >= 3:
            user_id = parts[0]
            email = parts[1]
            password = parts[2]
        elif len(parts) == 2:
            user_id = ""
            email = parts[0]
            password = parts[1]
        else:
            self.log_signal.emit("❌ Tài khoản cần lấy phải có dạng tk|mk hoặc userid|tk|mk...!")
            self.finished_signal.emit()
            return

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        self.pause_event = asyncio.Event()
        self.pause_event.set() 

        try:
            loop.run_until_complete(process_single_account(email, password, self.recovery_acc, self.log_signal, self.result_signal, self.pause_event, user_id))
        except Exception as e:
            self.log_signal.emit(f"❌ Lỗi luồng: {str(e)}")
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

# --- GIAO DIỆN CHÍNH (GUI) ---
class MainWindow(QWidget):
    background_log_signal = pyqtSignal(str)
    vpn_gpm_finished_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.worker = None
        self.vpn_gpm_running = False
        self.background_log_signal.connect(self.update_log)
        self.vpn_gpm_finished_signal.connect(self.on_vpn_gpm_finished)
        self.initUI()

    def initUI(self):
        self.setWindowTitle(f'Get OAuth2 Token & Outlook Loader - Hao Automation (v{APP_VERSION})')
        self.setFixedWidth(750)
        self.setFixedHeight(720)
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

        # Ô 2: Nhập email khôi phục (GIỮ NGUYÊN KHI STOP)
        recovery_layout = QHBoxLayout()
        self.recovery_input = QLineEdit()
        self.recovery_input.setPlaceholderText("Nhập email khôi phục dạng: tk|mk|outh2|clientId")
        self.recovery_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333; padding: 8px; border-radius: 4px; color: #03dac6;")
        recovery_layout.addWidget(QLabel("<b>Email Khôi Phục:</b>"))
        recovery_layout.addWidget(self.recovery_input)
        main_layout.addLayout(recovery_layout)

        # Ô 3: Tên profile GPM cần mở sau khi VPN đã kết nối thành công
        gpm_layout = QHBoxLayout()
        self.gpm_profile_input = QLineEdit()
        self.gpm_profile_input.setPlaceholderText("Ví dụ: US-45-1")
        self.gpm_profile_input.setText("US-45-1")
        self.gpm_profile_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333; padding: 8px; border-radius: 4px; color: #ffcc80;")
        gpm_layout.addWidget(QLabel("<b>Profile GPM:</b>"))
        gpm_layout.addWidget(self.gpm_profile_input)
        main_layout.addLayout(gpm_layout)

        # Ô 4: Local API GPM có thể thay đổi trực tiếp trên GUI
        gpm_api_layout = QHBoxLayout()
        self.gpm_api_input = QLineEdit()
        self.gpm_api_input.setPlaceholderText("Ví dụ: http://127.0.0.1:13600/api/v3")
        self.gpm_api_input.setText("http://127.0.0.1:13600/api/v3")
        self.gpm_api_input.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333; padding: 8px; border-radius: 4px; color: #80cbc4;")
        gpm_api_layout.addWidget(QLabel("<b>GPM Local API:</b>"))
        gpm_api_layout.addWidget(self.gpm_api_input)
        main_layout.addLayout(gpm_api_layout)



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

        # --- 2. DÒNG 2: COPY TÀI KHOẢN + COPY MẬT KHẨU + COPY OTP + COPY LINK ---
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

        # Đưa cả 4 nút vào row2 với stretch=1 để chúng to bằng nhau và bằng 4 nút ở hàng dưới
        row2.addWidget(btn_copy_tk, stretch=1)
        row2.addWidget(btn_copy_mk, stretch=1)
        row2.addWidget(btn_copy_otp, stretch=1)
        row2.addWidget(self.btn_play_vpn_gpm, stretch=1)
        main_layout.addLayout(row2)

        # --- 3. DÒNG 3: PLAY + TẠM DỪNG + STOP + XÓA LOG ---
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

        row3.addWidget(self.btn_start)
        row3.addWidget(self.btn_pause)
        row3.addWidget(self.btn_stop)
        row3.addWidget(self.btn_clear)
        row3.addWidget(self.btn_update)
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
        api_url = api_url.rstrip('/')
        try:
            self.background_log_signal.emit(f"🧭 [Bước 3] Đang tìm profile GPM: {profile_name}")
            profiles = []
            page_number = 1
            page_size = 50

            while True:
                response = requests.get(
                    f"{api_url}/profiles",
                    params={"page": page_number, "page_size": page_size},
                    timeout=15
                )
                response.raise_for_status()
                payload = response.json()
                if not payload.get("success"):
                    raise RuntimeError(payload.get("message", "GPM API trả về thất bại"))

                batch = payload.get("data") or []
                profiles.extend(batch)
                total = (payload.get("pagination") or {}).get("total", 0)
                if not batch or len(batch) < page_size or (total and len(profiles) >= total):
                    break
                page_number += 1

            target = next(
                (profile for profile in profiles
                 if str(profile.get("name", "")).strip() == profile_name.strip()),
                None
            )
            if not target:
                raise RuntimeError(f"Không tìm thấy profile có tên chính xác: {profile_name}")

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
        recovery_acc = self.recovery_input.text().strip()

        parts = target_acc.split('|')
        if not target_acc or '|' not in target_acc or len(parts) < 2:
            self.log_output.append("⚠️ Vui lòng nhập tài khoản đúng định dạng (có chứa dấu |)!")
            return
            
        if not recovery_acc or '|' not in recovery_acc:
            self.log_output.append("⚠️ Vui lòng nhập email khôi phục dạng tk|mk|outh2|clientId!")
            return

        self.btn_start.setEnabled(False)
        self.result_output.clear()
        
        self.worker = AutomationWorker(target_acc, recovery_acc)
        self.worker.log_signal.connect(self.update_log)
        self.worker.result_signal.connect(self.show_result)
        self.worker.finished_signal.connect(lambda: self.btn_start.setEnabled(True))
        self.worker.start()
        
        self.log_output.append("🚀 Đã bấm PLAY, trình duyệt đang khởi động...")

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
        if acc_text and '|' in acc_text:
            parts = [p.strip() for p in acc_text.split('|') if p.strip()]
            if len(parts) >= 3:
                tk_val = parts[1] # Từ 3 biến trở lên thì tài khoản luôn ở vị trí thứ 2 (index 1)
            elif len(parts) == 2:
                tk_val = parts[0] # 2 biến thì tài khoản ở đầu (index 0)
            else:
                return
            QApplication.clipboard().setText(tk_val)
            self.log_output.append(f"📋 Đã copy Tài khoản: {tk_val}")
            return
        self.log_output.append("⚠️ Chưa có định dạng tài khoản hợp lệ để copy!")

    def copy_password_only(self):
        acc_text = self.acc_input.text().strip()
        if acc_text and '|' in acc_text:
            parts = [p.strip() for p in acc_text.split('|') if p.strip()]
            if len(parts) >= 3:
                mk_val = parts[2] # Từ 3 biến trở lên thì mật khẩu luôn ở vị trí thứ 3 (index 2)
            elif len(parts) == 2:
                mk_val = parts[1] # 2 biến thì mật khẩu ở vị trí thứ 2 (index 1)
            else:
                return
            QApplication.clipboard().setText(mk_val)
            self.log_output.append(f"📋 Đã copy Mật khẩu: {mk_val}")
            return
        self.log_output.append("⚠️ Chưa có mật khẩu để copy!")

    def fetch_and_copy_otp(self):
        # Lấy chuỗi từ ô kết quả thay vì ô email khôi phục
        result_acc = self.result_output.text().strip()
        
        if not result_acc or '|' not in result_acc:
            self.log_output.append("⚠️ Chưa có kết quả tài khoản hoặc chuỗi kết quả không hợp lệ để lấy OTP!")
            return
        
        # Tách chuỗi kết quả: dạng userid|tk|mk|outh2|clientid hoặc tk|mk|outh2|clientid
        parts = result_acc.split('|')
        
        # Kiểm tra xem chuỗi có đủ thông tin oauth2 và clientid ở đuôi không (thường >= 4 phần)
        if len(parts) >= 4:
            # Lấy 2 phần cuối cùng làm oAuth2 và Client ID
            outh2 = parts[-2].strip()
            client_id = parts[-1].strip()
            
            # Lấy thông tin tk/mk đứng trước đó để làm định dạng chuẩn cho hàm API đọc
            # Nếu có userid ở đầu thì tk là phần áp chót, còn không thì lùi về trước
            tk = parts[-3].strip() # Hoặc phần tk tương ứng
            mk = parts[-4].strip() if len(parts) >= 5 else parts[0].strip()
            
            # Gom lại thành chuỗi chuẩn tk|mk|outh2|clientId mà hàm get_outlook_otp_via_api yêu cầu
            target_recovery_str = f"dummy|dummy|{outh2}|{client_id}"
        else:
            self.log_output.append("❌ Chuỗi kết quả chưa đủ định dạng OAuth2 và Client ID để lấy OTP!")
            return
        
        self.log_output.append("🔄 Đang chủ động quét OTP từ tài khoản kết quả...")
        
        class DummySignal:
            def __init__(self, log_widget): self.log = log_widget
            def emit(self, msg): self.log.append(msg)
            
        dummy_sig = DummySignal(self.log_output)
        otp = get_outlook_otp_via_api(target_recovery_str, dummy_sig)
        if otp:
            QApplication.clipboard().setText(otp)
            self.log_output.append(f"🎯 ĐÃ COPY MÃ OTP VÀO BỘ NHỚ TẠM: {otp}")
        else:
            self.log_output.append("❌ Không tìm thấy mã OTP nào từ tài khoản này!")

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
