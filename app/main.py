"""Keenetic DNS Routes — встроенные списки KeeneticOS (без Neo), порт 8001."""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import socket
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import config
from .models import ApplyRequest, AuthBody, RouterSpec, StoreData
from .rci import KeeneticRCI, KeeneticRCIError, test_connection
from .rci_url import parse_rci_url, sanitize_router_dict
from .store import ensure_store, load_store, new_router_id, save_store

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s"
)
logger = logging.getLogger("kdns")

TPL = Path(__file__).resolve().parent.parent / "templates"


def _chk(pwd: str) -> None:
    if (pwd or "").strip() != config.ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")


def router_rci_context(r: dict) -> tuple[str, str, str]:
    """Чистый base URL и логин/пароль: поля роутера → user:pass@ в URL → .env (KEENETIC_*)."""
    base, u_url, p_url = parse_rci_url(r.get("rci_base_url") or "")
    login = (
        (r.get("keenetic_login") or "").strip()
        or u_url
        or (config.KEENETIC_LOGIN or "").strip()
    )
    password = (
        (r.get("keenetic_password") or "").strip()
        or p_url
        or (config.KEENETIC_PASSWORD or "").strip()
    )
    return base, login, password


def _require_router_rci_creds(r: dict) -> tuple[str, str, str]:
    base, login, password = router_rci_context(r)
    if not base:
        raise HTTPException(
            400,
            "Некорректный base URL прокси (нужен http(s)://хост:порт, при необходимости с user:pass@).",
        )
    if not login or not password:
        raise HTTPException(
            400,
            "Нет логина/пароля для API: укажи у роутера, или в URL "
            "http(s)://логин:пароль@хост:порт, или задай KEENETIC_LOGIN и KEENETIC_PASSWORD в .env.",
        )
    return base, login, password


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_store()
    yield


app = FastAPI(title="Keenetic DNS Routes", version="1.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (TPL / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.post("/api/auth")
async def api_auth(b: AuthBody):
    if (b.password or "").strip() == config.ADMIN_PASSWORD:
        return {"ok": True}
    raise HTTPException(401, "Wrong password")


@app.get("/api/data")
async def get_data(x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    if cur.get("routers"):
        cur = {
            **cur,
            "routers": [
                sanitize_router_dict(dict(r)) if isinstance(r, dict) else r
                for r in cur["routers"]
            ],
        }
    return cur


@app.get("/api/keenetic-env")
async def keenetic_env(x_admin_password: str = Header("")):
    """Дефолт из .env для роутеров без своих полей (пароль не отдаём)."""
    _chk(x_admin_password)
    return {
        "mode": "env_or_router",
        "login": config.KEENETIC_LOGIN,
        "password_configured": bool(config.KEENETIC_PASSWORD),
        "hint": "Два варианта: (1) KEENETIC_LOGIN / KEENETIC_PASSWORD в .env — для роутеров без своих полей; "
        "(2) у каждого роутера свои поля или один раз URL http(s)://логин:пароль@хост:порт (учётка уйдёт в поля).",
    }


@app.get("/api/routers/{rid}/interfaces")
async def router_interfaces(
    rid: str,
    wireguard_only: bool = Query(False, description="Только интерфейсы с type Wireguard"),
    x_admin_password: str = Header(""),
):
    _chk(x_admin_password)
    cur = load_store()
    r = next((x for x in cur.get("routers") or [] if x.get("id") == rid), None)
    if not r:
        raise HTTPException(404, "Роутер не найден")
    r = sanitize_router_dict(r)
    _require_router_rci_creds(r)

    def _run():
        base, lg, pw = router_rci_context(r)
        k = KeeneticRCI(base, lg, pw)
        return k.list_interfaces()

    try:
        items = await asyncio.to_thread(_run)
    except KeeneticRCIError as e:
        raise HTTPException(502, str(e)) from e
    except Exception as e:
        logger.exception("router_interfaces rid=%s", rid)
        raise HTTPException(502, f"RCI: {e}") from e
    if wireguard_only:
        items = [
            it
            for it in items
            if "wireguard" in str(it.get("type") or "").lower()
        ]
    return {"interfaces": items}


class PutDataBody(BaseModel):
    groups: dict[str, dict] | None = None
    routers: list[dict] | None = None


@app.put("/api/data")
async def put_data(b: PutDataBody, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    if b.groups is not None:
        cur["groups"] = b.groups
    if b.routers is not None:
        cur["routers"] = [
            sanitize_router_dict(dict(x)) if isinstance(x, dict) else x
            for x in b.routers
        ]
    try:
        StoreData.from_json(cur)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    save_store(cur)
    return {"ok": True}


class AddRouterBody(BaseModel):
    name: str = Field(..., min_length=1)
    rci_base_url: str = Field(..., min_length=8)
    keenetic_login: str = ""
    keenetic_password: str = ""


@app.post("/api/routers")
async def add_router(b: AddRouterBody, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    base, u_url, p_url = parse_rci_url(b.rci_base_url)
    login = (b.keenetic_login or "").strip() or u_url or config.KEENETIC_LOGIN
    password = (b.keenetic_password or "").strip() or p_url or config.KEENETIC_PASSWORD
    if not base:
        raise HTTPException(400, "Некорректный URL прокси")
    if not login or not password:
        raise HTTPException(
            400,
            "Нужны логин и пароль: поля ниже, или в URL http(s)://логин:пароль@хост:порт, или KEENETIC_* в .env",
        )
    r = RouterSpec(
        id=new_router_id(),
        name=b.name.strip(),
        rci_base_url=base,
        enabled=True,
        keenetic_login=login,
        keenetic_password=password,
    )
    lst = list(cur.get("routers") or [])
    lst.append(r.model_dump())
    cur["routers"] = lst
    save_store(cur)
    return r.model_dump()


@app.delete("/api/routers/{rid}")
async def del_router(rid: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    cur["routers"] = [r for r in cur.get("routers") or [] if r.get("id") != rid]
    save_store(cur)
    return {"ok": True}


class PatchRouterBody(BaseModel):
    name: str | None = None
    rci_base_url: str | None = None
    keenetic_login: str | None = None
    keenetic_password: str | None = None
    enabled: bool | None = None


@app.patch("/api/routers/{rid}")
async def patch_router(rid: str, b: PatchRouterBody, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    lst = list(cur.get("routers") or [])
    idx = next((i for i, x in enumerate(lst) if x.get("id") == rid), -1)
    if idx < 0:
        raise HTTPException(404, "Роутер не найден")
    r = dict(lst[idx])
    patch = b.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        r["name"] = str(patch["name"]).strip() or r["name"]
    if "rci_base_url" in patch and patch["rci_base_url"] is not None:
        r["rci_base_url"] = str(patch["rci_base_url"]).strip()
    if "keenetic_login" in patch and patch["keenetic_login"] is not None:
        r["keenetic_login"] = str(patch["keenetic_login"]).strip()
    if "keenetic_password" in patch and patch["keenetic_password"] is not None:
        r["keenetic_password"] = str(patch["keenetic_password"])
    if "enabled" in patch and patch["enabled"] is not None:
        r["enabled"] = bool(patch["enabled"])
    r = sanitize_router_dict(r)
    lst[idx] = r
    cur["routers"] = lst
    try:
        StoreData.from_json(cur)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    save_store(cur)
    return r


def _gen_ed25519_keypair(rid: str) -> tuple[str, str]:
    """Сгенерировать ed25519 keypair на VPS через ssh-keygen. Возвращает (private_pem, public_openssh)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        keyfile = os.path.join(tmpdir, "k")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", keyfile, "-N", "", "-q",
             "-C", f"kdns-tunnel-{rid}"],
            check=True, timeout=10,
        )
        priv = Path(keyfile).read_text()
        pub = Path(keyfile + ".pub").read_text().strip()
    return priv, pub


def _add_pubkey_to_authorized_keys(rid: str, pubkey: str) -> Path:
    """Добавить pubkey в ~/.ssh/authorized_keys (де-дуп по комменту kdns-tunnel-{rid})."""
    auth_dir = Path.home() / ".ssh"
    auth_dir.mkdir(mode=0o700, exist_ok=True)
    auth_path = auth_dir / "authorized_keys"
    comment = f"kdns-tunnel-{rid}"
    lines: list[str] = []
    if auth_path.exists():
        lines = [l for l in auth_path.read_text().splitlines() if comment not in l and l.strip()]
    lines.append(pubkey)
    auth_path.write_text("\n".join(lines) + "\n")
    auth_path.chmod(0o600)
    try:
        auth_dir.chmod(0o700)
    except OSError:
        pass
    return auth_path


@app.get("/api/routers/{rid}/tunnel-cmd")
async def tunnel_cmd(rid: str, x_admin_password: str = Header("")):
    """Назначить порт + сгенерить keypair + вернуть короткую curl|sh команду для роутера."""
    _chk(x_admin_password)
    if not config.VPS_SSH_HOST:
        raise HTTPException(400, "VPS_SSH_HOST не задан в .env — укажи публичный IP/домен VPS")

    cur = load_store()
    routers = list(cur.get("routers") or [])
    idx = next((i for i, x in enumerate(routers) if x.get("id") == rid), -1)
    if idx < 0:
        raise HTTPException(404, "Роутер не найден")

    r = dict(routers[idx])

    # Порт
    if r.get("tunnel_port"):
        port = int(r["tunnel_port"])
    else:
        used = {int(x.get("tunnel_port")) for x in routers if x.get("tunnel_port")}
        port = config.TUNNEL_PORT_START
        while port in used:
            port += 1
        r["tunnel_port"] = port

    # Keypair (один раз на роутер; ssh-keygen на VPS — он точно есть на Linux-сервере)
    if not r.get("tunnel_priv_key") or not r.get("tunnel_pub_key"):
        try:
            priv, pub = await asyncio.to_thread(_gen_ed25519_keypair, rid)
        except FileNotFoundError as e:
            raise HTTPException(500, "ssh-keygen не найден на VPS — установи openssh-client") from e
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"ssh-keygen упал: {e}") from e
        r["tunnel_priv_key"] = priv
        r["tunnel_pub_key"] = pub
        await asyncio.to_thread(_add_pubkey_to_authorized_keys, rid, pub)

    # Одноразовый токен (10 мин) — для скачивания скрипта
    reg_token = secrets.token_urlsafe(32)
    r["tunnel_reg_token"] = reg_token
    r["tunnel_reg_token_exp"] = int(time.time()) + 600

    routers[idx] = r
    cur["routers"] = routers
    save_store(cur)

    http_url = f"http://{config.VPS_SSH_HOST}:{config.PORT}"
    one_liner = f"curl -fsS '{http_url}/api/routers/{rid}/tunnel-script?token={reg_token}' | sh"

    return {
        "tunnel_port": port,
        "rci_url": f"http://localhost:{port}",
        "cmd": one_liner,
    }


@app.get("/api/routers/{rid}/tunnel-script")
async def tunnel_script(rid: str, token: str):
    """Установочный скрипт для роутера (с приватным ключом внутри). Auth — одноразовый токен."""
    cur = load_store()
    routers = list(cur.get("routers") or [])
    idx = next((i for i, x in enumerate(routers) if x.get("id") == rid), -1)
    if idx < 0:
        raise HTTPException(404, "Роутер не найден")
    r = dict(routers[idx])

    saved = r.get("tunnel_reg_token")
    if not saved or not secrets.compare_digest(saved, token or ""):
        raise HTTPException(403, "Неверный или израсходованный токен")
    if time.time() > int(r.get("tunnel_reg_token_exp") or 0):
        raise HTTPException(403, "Токен истёк (10 мин). Открой модалку заново.")

    if not r.get("tunnel_priv_key") or not r.get("tunnel_port"):
        raise HTTPException(500, "Тоннель не подготовлен — открой модалку заново")

    # Токен — одноразовый, расходуем сейчас
    r.pop("tunnel_reg_token", None)
    r.pop("tunnel_reg_token_exp", None)
    routers[idx] = r
    cur["routers"] = routers
    save_store(cur)

    port = int(r["tunnel_port"])
    priv_key = r["tunnel_priv_key"].strip()
    vps_host = config.VPS_SSH_HOST
    vps_port = config.VPS_SSH_PORT
    vps_user = config.VPS_SSH_USER

    script = f"""#!/bin/sh
set -e
export PATH="/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin:$PATH"

echo '[1/4] autossh...'
opkg install autossh openssh-client 2>/dev/null || true
command -v autossh >/dev/null 2>&1 || {{ echo 'ОШИБКА: autossh не установлен. Запусти opkg update и повтори.'; exit 1; }}

echo '[2/4] Приватный ключ...'
mkdir -p /opt/etc
cat > /opt/etc/kdns_tk <<'KEYEOF'
{priv_key}
KEYEOF
chmod 600 /opt/etc/kdns_tk

echo '[3/4] Скрипт тоннеля + автозапуск...'
cat > /opt/bin/kdns_tun <<'RUNEOF'
#!/bin/sh
PATH="/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin:$PATH"
exec autossh -M 0 \\
  -i /opt/etc/kdns_tk \\
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \\
  -o ExitOnForwardFailure=yes -o IdentitiesOnly=yes \\
  -N -R {port}:localhost:81 {vps_user}@{vps_host} -p {vps_port}
RUNEOF
chmod +x /opt/bin/kdns_tun

cat > /opt/etc/init.d/S99kdns_tun <<'INITEOF'
#!/bin/sh
case "$1" in
  start)   killall -0 autossh 2>/dev/null || nohup /opt/bin/kdns_tun >/dev/null 2>&1 & ;;
  stop)    killall autossh 2>/dev/null ;;
  restart) killall autossh 2>/dev/null; sleep 1; nohup /opt/bin/kdns_tun >/dev/null 2>&1 & ;;
esac
INITEOF
chmod +x /opt/etc/init.d/S99kdns_tun

echo '[4/4] Запуск...'
killall autossh 2>/dev/null || true
sleep 1
nohup /opt/bin/kdns_tun >/dev/null 2>&1 &
sleep 3
if killall -0 autossh 2>/dev/null; then
  echo
  echo '=== OK ==='
  echo 'Тоннель: localhost:81 (роутер) -> VPS:{port}'
  echo 'Возвращайся в браузер и жми "Проверить связь"'
else
  echo
  echo '=== ОШИБКА: autossh не запустился ==='
  echo 'Тест вручную: /opt/bin/kdns_tun  (Ctrl+C для выхода)'
  exit 1
fi
"""
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")


@app.delete("/api/routers/{rid}/tunnel")
async def tunnel_remove(rid: str, x_admin_password: str = Header("")):
    """Снять назначение тоннельного порта с роутера."""
    _chk(x_admin_password)
    cur = load_store()
    routers = list(cur.get("routers") or [])
    idx = next((i for i, x in enumerate(routers) if x.get("id") == rid), -1)
    if idx < 0:
        raise HTTPException(404, "Роутер не найден")
    r = dict(routers[idx])
    r.pop("tunnel_port", None)
    routers[idx] = r
    cur["routers"] = routers
    save_store(cur)
    return {"ok": True}


@app.get("/api/routers/{rid}/tunnel-status")
async def tunnel_status(rid: str, x_admin_password: str = Header("")):
    """Проверить: слушает ли тоннельный порт на localhost VPS прямо сейчас."""
    _chk(x_admin_password)
    cur = load_store()
    r = next((x for x in cur.get("routers") or [] if x.get("id") == rid), None)
    if not r:
        raise HTTPException(404, "Роутер не найден")
    port = r.get("tunnel_port")
    if not port:
        return {"active": False, "reason": "tunnel_port не назначен"}

    def _check() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
                return True
        except OSError:
            return False

    active = await asyncio.to_thread(_check)
    return {"active": active, "tunnel_port": port}


@app.post("/api/test-router/{rid}")
async def test_router(rid: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    r = next((x for x in cur.get("routers") or [] if x.get("id") == rid), None)
    if not r:
        raise HTTPException(404, "Роутер не найден")
    r = sanitize_router_dict(r)
    try:
        base, lg, pw = _require_router_rci_creds(r)
    except HTTPException:
        raise
    ok, msg = await asyncio.to_thread(test_connection, base, lg, pw)
    return {"ok": ok, "message": msg}


class GroupLinesPatch(BaseModel):
    """Инкрементально изменить строки группы на сервере (без полной перезаписи textarea)."""

    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


@app.post("/api/groups/{name}/lines")
async def patch_group_lines(
    name: str, b: GroupLinesPatch, x_admin_password: str = Header("")
):
    _chk(x_admin_password)
    if name not in ("US", "RU"):
        raise HTTPException(400, "Допустимы только группы US и RU")
    cur = load_store()
    groups = cur.setdefault("groups", {})
    g = dict(groups.get(name) or {"interface_id": "", "lines": []})
    lines = [str(x).strip() for x in (g.get("lines") or []) if str(x).strip()]
    for rm in b.remove:
        t = (rm or "").strip()
        lines = [x for x in lines if x != t]
    for ad in b.add:
        t = (ad or "").strip()
        if t and t not in lines:
            lines.append(t)
    g["lines"] = lines
    groups[name] = g
    try:
        StoreData.from_json(cur)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    save_store(cur)
    return {"ok": True, "lines": lines}


@app.post("/api/apply")
async def apply_dns(b: ApplyRequest, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    data = StoreData.from_json(cur)
    routers = data.routers
    if b.mode == "selected":
        sel = set(b.router_ids or [])
        routers = [r for r in routers if r.id in sel]
    else:
        routers = [r for r in routers if r.enabled]

    if not routers:
        raise HTTPException(400, "Нет роутеров для применения")

    raw_by_id = {
        x.get("id"): sanitize_router_dict(dict(x))
        for x in (cur.get("routers") or [])
        if isinstance(x, dict) and x.get("id")
    }
    for r in routers:
        raw = raw_by_id.get(r.id)
        if not raw:
            raise HTTPException(400, f"Роутер {r.name}: нет записи в хранилище")
        try:
            _require_router_rci_creds(raw)
        except HTTPException as e:
            raise HTTPException(
                400,
                f"{r.name}: нет логина/пароля — поля роутера, или URL user:pass@, или KEENETIC_* в .env.",
            ) from e

    groups_dump = {k: v.model_dump() for k, v in data.groups.items()}
    group_keys = tuple(data.groups.keys())

    results: list[dict] = []

    def _one(r: RouterSpec) -> dict:
        raw = raw_by_id.get(r.id) or {}
        base, lg, pw = router_rci_context(raw)
        k = KeeneticRCI(base, lg, pw)
        try:
            log = k.apply_groups(groups_dump, group_names=group_keys)
            return {"router": r.name, "id": r.id, "ok": True, "log": log}
        except KeeneticRCIError as e:
            return {"router": r.name, "id": r.id, "ok": False, "error": str(e)}
        except Exception as e:
            logger.exception("apply %s", r.name)
            return {"router": r.name, "id": r.id, "ok": False, "error": str(e)}

    for r in routers:
        results.append(await asyncio.to_thread(_one, r))

    return {"results": results}
