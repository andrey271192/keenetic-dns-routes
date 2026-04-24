#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
apt-get update -qq && apt-get install -y python3 python3-venv python3-pip
python3 -m venv venv
source venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env && echo "Создан .env — задай ADMIN_PASSWORD и KEENETIC_PASSWORD"; fi
mkdir -p data
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
