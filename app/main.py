"""Keenetic DNS Routes — встроенные списки KeeneticOS (без Neo), порт 8001."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
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
