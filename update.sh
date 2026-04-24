#!/bin/bash
# Обновление keenetic-dns-routes: git pull, pip, перезапуск systemd.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
echo "==> keenetic-dns-routes: $DIR"
if [ -d .git ]; then git pull --ff-only; else echo "(!) Нет .git — скопируй файлы проекта поверх, затем снова update.sh."; fi
if [ ! -d venv ]; then python3 -m venv venv; fi
# shellcheck source=/dev/null
source venv/bin/activate
pip install -q -r requirements.txt
if [ "$(id -u)" = 0 ] && [ -f /etc/systemd/system/keenetic-dns-routes.service ]; then
  systemctl daemon-reload
  systemctl restart keenetic-dns-routes
  echo "==> systemd: keenetic-dns-routes перезапущен"
else
  echo "Перезапуск (root): sudo systemctl restart keenetic-dns-routes"
fi
echo "==> Готово. Обнови страницу в браузере (Ctrl+F5)."
