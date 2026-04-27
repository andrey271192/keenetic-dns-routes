import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STORE_FILE = DATA_DIR / "store.json"

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8001"))
# Пустая строка в .env (ADMIN_PASSWORD=) не даёт дефолт через getenv — явно подставляем admin
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "admin").strip()
# Дефолт для роутеров без своих keenetic_* и без user:pass@ в URL (можно оставить пустым)
KEENETIC_LOGIN = (os.getenv("KEENETIC_LOGIN") or "admin").strip()
KEENETIC_PASSWORD = (os.getenv("KEENETIC_PASSWORD") or "").strip()

# Reverse SSH tunnel — для роутеров без белого IP
VPS_SSH_HOST = (os.getenv("VPS_SSH_HOST") or "").strip()
VPS_SSH_PORT = int(os.getenv("VPS_SSH_PORT") or "22")
VPS_SSH_USER = (os.getenv("VPS_SSH_USER") or "root").strip()
VPS_SSH_PASS = (os.getenv("VPS_SSH_PASS") or "").strip()
TUNNEL_PORT_START = int(os.getenv("TUNNEL_PORT_START") or "20100")
