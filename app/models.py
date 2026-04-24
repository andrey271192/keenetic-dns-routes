from typing import Any

from pydantic import BaseModel, Field


class GroupSpec(BaseModel):
    interface_id: str = ""
    lines: list[str] = Field(default_factory=list)


class RouterSpec(BaseModel):
    id: str
    name: str
    rci_base_url: str = Field(
        ...,
        description="Базовый URL HTTP Proxy (http(s)://хост:порт), без путей /rci/...",
    )
    enabled: bool = True
    keenetic_login: str = Field(default="", description="Логин Keenetic для HTTP Proxy / RCI")
    keenetic_password: str = Field(default="", description="Пароль Keenetic для RCI")


class StoreData(BaseModel):
    groups: dict[str, GroupSpec] = Field(
        default_factory=lambda: {
            "US": GroupSpec(),
            "RU": GroupSpec(),
        }
    )
    routers: list[RouterSpec] = Field(default_factory=list)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "StoreData":
        g: dict[str, GroupSpec] = {"US": GroupSpec(), "RU": GroupSpec()}
        for k, v in (raw.get("groups") or {}).items():
            if isinstance(v, dict):
                g[k] = GroupSpec(**v)
        rlist = []
        from .rci_url import sanitize_router_dict

        for r in raw.get("routers") or []:
            if isinstance(r, dict) and r.get("id"):
                rlist.append(RouterSpec(**sanitize_router_dict(r)))
        return cls(groups=g, routers=rlist)

    def to_json(self) -> dict[str, Any]:
        return {
            "groups": {k: v.model_dump() for k, v in self.groups.items()},
            "routers": [r.model_dump() for r in self.routers],
        }


class ApplyRequest(BaseModel):
    mode: str = "all"  # all | selected
    router_ids: list[str] = Field(default_factory=list)


class AuthBody(BaseModel):
    password: str
