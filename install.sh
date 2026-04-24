#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
apt-get update -qq && apt-get install -y python3 python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env && echo "Создан .env — задай ADMIN_PASSWORD; при желании KEENETIC_* как дефолт для роутеров."; fi
mkdir -p data

# Старые клоны без update.sh в репозитории — создаём рядом с install.sh
if [ ! -f "$DIR/update.sh" ]; then
  cat >"$DIR/update.sh" <<'EOS'
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
EOS
  chmod +x "$DIR/update.sh"
  echo "Создан $DIR/update.sh"
fi

if [ "$(id -u)" = 0 ]; then
  sed "s|WorkingDirectory=.*|WorkingDirectory=$DIR|" keenetic-dns-routes.service | \
    sed "s|ExecStart=.*|ExecStart=$DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001|" \
    > /etc/systemd/system/keenetic-dns-routes.service
  systemctl daemon-reload
  systemctl enable keenetic-dns-routes
  systemctl restart keenetic-dns-routes
  echo "Сервис keenetic-dns-routes запущен"
else
  echo "Запусти install.sh от root для systemd, или локально:"
  echo "  cd $DIR && source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8001"
fi
echo "Интерфейс: http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 127.0.0.1):8001"
echo "Дальнейшее обновление кода: cd $DIR && sudo bash update.sh"
