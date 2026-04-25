#!/bin/bash
set -e
echo "Удаление keenetic-dns-routes…"
systemctl stop keenetic-dns-routes 2>/dev/null || true
systemctl disable keenetic-dns-routes 2>/dev/null || true
rm -f /etc/systemd/system/keenetic-dns-routes.service
systemctl daemon-reload 2>/dev/null || true
rm -rf /opt/keenetic-dns-routes
echo "Готово."
