# ============================================================
# run.py  —  khởi động bot + web server Mini App (1 tiến trình)
# ============================================================
import os, threading, requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, Update
from telegram.ext import CommandHandler

from bot import build_application, init_db, BOT_TOKEN

WEBAPP_URL = os.environ.get("WEBAPP_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8080"))


async def cmd_app(update, context):
    if not WEBAPP_URL:
        return await update.message.reply_text("Chưa cấu hình WEBAPP_URL để mở Mini App.")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🌱 Mở Nông Trại",
                             web_app=WebAppInfo(url=f"{WEBAPP_URL}/app"))]])
    await update.message.reply_text("Mở Mini App 👇", reply_markup=kb)


def set_menu_button():
    if not WEBAPP_URL:
        print("⚠️ Chưa có WEBAPP_URL — bỏ qua nút menu.")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton", timeout=10,
                          json={"menu_button": {"type": "web_app", "text": "🌱 Mở App",
                                                "web_app": {"url": f"{WEBAPP_URL}/app"}}})
        print("setChatMenuButton:", r.json().get("ok"))
    except Exception as e:
        print("set_menu_button lỗi:", e)


def main():
    import webapp
    init_db()
    if not BOT_TOKEN:
        raise SystemExit("❌ Thiếu TELEGRAM_BOT_TOKEN.")
    webapp._ensure_tables()
    threading.Thread(target=webapp.run_web, daemon=True).start()
    print(f"🌐 Web/Mini App cổng {PORT}  (public: {WEBAPP_URL or 'chưa đặt WEBAPP_URL'})")
    set_menu_button()
    app = build_application()
    app.add_handler(CommandHandler("app", cmd_app))
    print("🤖 Bot đang chạy... (Ctrl+C để dừng)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def _is_notebook():
    try:
        get_ipython  # noqa: F821
        return True
    except NameError:
        return False


if __name__ == "__main__" and not _is_notebook():
    main()
requirements.txt

python-telegram-bot[rate-limiter]>=21,<23
python-dotenv>=1.0
flask>=3.0
requests>=2.31
