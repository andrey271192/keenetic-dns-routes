"""Keenetic DNS Routes — встроенные списки KeeneticOS (без Neo), порт 8001."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import config
from .models import ApplyRequest, AuthBody, RouterSpec, StoreData
from .rci import KeeneticRCI, KeeneticRCIError, test_connection
from .store import ensure_store, load_store, new_router_id, save_store

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s"
)
logger = logging.getLogger("kdns")

TPL = Path(__file__).resolve().parent.parent / "templates"


def _chk(pwd: str) -> None:
    if (pwd or "").strip() != config.ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_store()
    yield


app = FastAPI(title="Keenetic DNS Routes", version="1.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (TPL / "index.html").read_text(encoding="utf-8")


@app.post("/api/auth")
async def api_auth(b: AuthBody):
    if (b.password or "").strip() == config.ADMIN_PASSWORD:
        return {"ok": True}
    raise HTTPException(401, "Wrong password")


@app.get("/api/data")
async def get_data(x_admin_password: str = Header("")):
    _chk(x_admin_password)
    return load_store()


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
        cur["routers"] = b.routers
    try:
        StoreData.from_json(cur)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    save_store(cur)
    return {"ok": True}


class AddRouterBody(BaseModel):
    name: str = Field(..., min_length=1)
    rci_base_url: str = Field(..., min_length=8)


@app.post("/api/routers")
async def add_router(b: AddRouterBody, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cur = load_store()
    r = RouterSpec(
        id=new_router_id(),
        name=b.name.strip(),
        rci_base_url=b.rci_base_url.strip().rstrip("/"),
        enabled=True,
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


@app.post("/api/test-router/{rid}")
async def test_router(rid: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    if not config.KEENETIC_PASSWORD:
        raise HTTPException(400, "Задайте KEENETIC_PASSWORD в .env")
    cur = load_store()
    r = next((x for x in cur.get("routers") or [] if x.get("id") == rid), None)
    if not r:
        raise HTTPException(404, "Роутер не найден")
    ok, msg = await asyncio.to_thread(
        test_connection,
        r["rci_base_url"],
        config.KEENETIC_LOGIN,
        config.KEENETIC_PASSWORD,
    )
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
    if not config.KEENETIC_PASSWORD:
        raise HTTPException(400, "Задайте KEENETIC_PASSWORD в .env")
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

    groups_dump = {k: v.model_dump() for k, v in data.groups.items()}
    group_keys = tuple(data.groups.keys())

    results: list[dict] = []

    def _one(r: RouterSpec) -> dict:
        k = KeeneticRCI(
            r.rci_base_url, config.KEENETIC_LOGIN, config.KEENETIC_PASSWORD
        )
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
