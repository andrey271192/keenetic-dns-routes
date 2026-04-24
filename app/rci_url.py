"""Разбор RCI base URL: отдельно host:port и учётные данные из user:pass@."""
from __future__ import annotations

from urllib.parse import unquote, urlparse


def parse_rci_url(url: str) -> tuple[str, str, str]:
    """
    Возвращает (чистый base без userinfo, логин из URL или "", пароль из URL или "").
    Поддерживает http(s)://user:pass@host:port/...
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return "", "", ""
    p = urlparse(u.replace(" ", ""))
    scheme = (p.scheme or "http").lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    user = unquote(p.username) if p.username else ""
    pw = unquote(p.password) if p.password else ""
    host = p.hostname
    if not host:
        return "", user, pw
    port = p.port
    netloc = f"{host}:{port}" if port else host
    base = f"{scheme}://{netloc}"
    return base, user, pw


def sanitize_router_dict(r: dict) -> dict:
    """Убирает user:pass из rci_base_url и при необходимости переносит в keenetic_*."""
    out = dict(r)
    raw = str(out.get("rci_base_url") or "")
    base, u_url, p_url = parse_rci_url(raw)
    if base:
        out["rci_base_url"] = base
    if not (out.get("keenetic_login") or "").strip() and u_url:
        out["keenetic_login"] = u_url
    if not (out.get("keenetic_password") or "").strip() and p_url:
        out["keenetic_password"] = p_url
    return out
