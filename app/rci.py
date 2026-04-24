"""Keenetic NDMS RCI — авторизация как в gokeenapi / Keenetic Unified."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("kdns.rci")

_MAX_PARSE = 90  # ниже лимита gokeenapi (100), с запасом под save


def _norm_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _is_ipish(s: str) -> bool:
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}(/\d+)?$", s):
        return True
    if "/" in s and re.match(r"^[0-9a-fA-F:.]+/\d+$", s):
        return True
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}-\d{1,3}(\.\d{1,3}){3}$", s):
        return True
    return False


def _valid_entry(s: str) -> bool:
    if _is_ipish(s):
        return True
    if "." in s and not s.startswith(".") and ".." not in s:
        return True
    return False


class KeeneticRCIError(RuntimeError):
    pass


class KeeneticRCI:
    def __init__(self, base_url: str, login: str, password: str):
        bu = base_url.rstrip("/")
        # user:pass@ в base_url ломает httpx (дубли с NDMS-auth) и даёт 500 / странные ответы
        rest = bu.split("://", 1)[-1] if "://" in bu else bu
        if "@" in rest:
            raise KeeneticRCIError(
                "В base URL не должно быть user:pass@ — сохрани роутер ещё раз "
                "(логин/пароль только в полях или перенесутся из URL при сохранении)."
            )
        self.base_url = bu
        self.login = login
        self.password = password
        self._client: httpx.Client | None = None

    def _client_ctx(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            verify=False,
            timeout=httpx.Timeout(60.0),
            follow_redirects=True,
        )

    def _auth(self, client: httpx.Client) -> None:
        r = client.get("/auth")
        if r.status_code == 200:
            return
        if r.status_code == 404:
            raise KeeneticRCIError(
                "/auth HTTP 404: на этом адресе нет NDMS /auth — проверь хост и порт прокси "
                "(часто нужен явный порт, например :81 или :443 для https), без лишнего пути в base URL."
            )
        # 401 — стандартный challenge; 403 иногда даёт прокси до входа, но с теми же заголовками
        if r.status_code not in (401, 403):
            raise KeeneticRCIError(f"/auth HTTP {r.status_code}")
        realm = r.headers.get("X-NDM-Realm", "") or r.headers.get("x-ndm-realm", "")
        challenge = r.headers.get("X-NDM-Challenge", "") or r.headers.get("x-ndm-challenge", "")
        set_cookie = r.headers.get("Set-Cookie") or r.headers.get("set-cookie") or ""
        cookie_pair = set_cookie.split(";")[0].strip()
        if not realm or not challenge or not cookie_pair:
            if r.status_code == 403:
                raise KeeneticRCIError(
                    "/auth HTTP 403 без NDMS challenge: доступ к HTTP Proxy с IP этого сервера "
                    "запрещён в настройках роутера, либо открыт не тот сервис. "
                    "В веб-интерфейсе Keenetic: разрешённые адреса для API / прокси — добавь IP VPS."
                )
            raise KeeneticRCIError("Нет заголовков X-NDM-Realm / Challenge или Set-Cookie")
        md5_hex = hashlib.md5(
            f"{self.login}:{realm}:{self.password}".encode()
        ).hexdigest()
        sha_hex = hashlib.sha256(f"{challenge}{md5_hex}".encode()).hexdigest()
        # Не писать Cookie в headers вручную — httpx иначе не подмешивает новую сессию из
        # Set-Cookie после успешного POST /auth, и /rci/* отвечает 401.
        client.cookies.update(r.cookies)
        if not client.cookies and "=" in cookie_pair:
            host = urlparse(self.base_url).hostname or ""
            name, _, value = cookie_pair.partition("=")
            client.cookies.set(name.strip(), value.strip(), domain=host)
        r2 = client.post(
            "/auth",
            json={"login": self.login, "password": sha_hex},
        )
        if r2.status_code in (401, 403):
            raise KeeneticRCIError(
                "Неверный логин или пароль Keenetic (POST /auth). "
                "Проверь учётку с доступом к HTTP Proxy / API и base URL (хост и порт как в настройках KeenDNS)."
            )
        if r2.status_code not in (200, 201, 202):
            raise KeeneticRCIError(f"POST /auth HTTP {r2.status_code}")
        client.cookies.update(r2.cookies)

    def list_interfaces(self) -> list[dict[str, Any]]:
        """GET /rci/show/interface — id, type, description, state (как gokeenapi)."""
        with self._client_ctx() as client:
            self._auth(client)
            r = client.get("/rci/show/interface")
            if r.status_code != 200:
                raise KeeneticRCIError(f"show/interface HTTP {r.status_code}")
            try:
                data = r.json()
            except ValueError as e:
                raise KeeneticRCIError(
                    f"show/interface: ответ не JSON (возможно неверный URL прокси). "
                    f"Начало тела: {r.text[:160]!r}"
                ) from e
            if not isinstance(data, dict):
                raise KeeneticRCIError("show/interface: ожидался объект JSON")
            rows: list[dict[str, Any]] = []
            for key, body in data.items():
                if not isinstance(body, dict) or str(key).startswith("_"):
                    continue
                iid = str(body.get("id") or body.get("Id") or key)
                typ = str(body.get("type") or body.get("Type") or "")
                desc = str(body.get("description") or body.get("Description") or "")
                state = str(body.get("state") or body.get("State") or "")
                link = str(body.get("link") or body.get("Link") or "")
                conn = str(body.get("connected") or body.get("Connected") or "")
                addr = str(body.get("address") or body.get("Address") or "")
                rows.append(
                    {
                        "id": iid,
                        "type": typ,
                        "description": desc,
                        "state": state,
                        "link": link,
                        "connected": conn,
                        "address": addr,
                        "label": f"{iid} — {desc or typ or 'интерфейс'}",
                    }
                )
            rows.sort(key=lambda x: x["id"].lower())
            return rows

    def _parse_fqdn_response(self, data: dict[str, Any]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name, body in data.items():
            if not isinstance(body, dict) or name.startswith("_"):
                continue
            inc = body.get("include") or body.get("Include") or []
            addrs: list[str] = []
            for item in inc:
                if isinstance(item, dict):
                    a = item.get("address") or item.get("Address")
                    if a:
                        addrs.append(str(a))
                elif item:
                    addrs.append(str(item))
            out[name] = addrs
        return out

    def get_fqdn_groups(self, client: httpx.Client) -> dict[str, list[str]]:
        r = client.get("/rci/object-group/fqdn")
        if r.status_code != 200:
            raise KeeneticRCIError(f"object-group/fqdn HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        if not isinstance(data, dict):
            raise KeeneticRCIError("object-group/fqdn: не JSON-объект")
        return self._parse_fqdn_response(data)

    def get_dns_routes(self, client: httpx.Client) -> dict[str, str]:
        r = client.get("/rci/dns-proxy/route")
        if r.status_code != 200:
            raise KeeneticRCIError(f"dns-proxy/route HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, list):
            raise KeeneticRCIError("dns-proxy/route: ожидался массив")
        out: dict[str, str] = {}
        for row in data:
            if not isinstance(row, dict):
                continue
            g = row.get("group") or row.get("Group")
            iface = row.get("interface") or row.get("Interface")
            if g and iface:
                out[str(g)] = str(iface)
        return out

    def _post_parse(self, client: httpx.Client, commands: list[str]) -> list[dict[str, Any]]:
        all_resp: list[dict[str, Any]] = []
        for i in range(0, len(commands), _MAX_PARSE):
            chunk = commands[i : i + _MAX_PARSE]
            body = [{"parse": c} for c in chunk]
            r = client.post("/rci/", json=body)
            if r.status_code != 200:
                raise KeeneticRCIError(f"POST /rci/ HTTP {r.status_code}: {r.text[:500]}")
            part = r.json()
            if not isinstance(part, list):
                raise KeeneticRCIError("POST /rci/: ответ не массив")
            all_resp.extend(part)
            for item in part:
                p = item.get("parse") or item.get("Parse") or {}
                for s in p.get("status") or p.get("Status") or []:
                    if not isinstance(s, dict):
                        continue
                    sv = (s.get("status") or s.get("Status") or "").lower()
                    if sv == "error":
                        raise KeeneticRCIError(
                            f"RCI: {s.get('code')} {s.get('ident', '')} — {s.get('message', '')}"
                        )
        return all_resp

    def apply_groups(
        self,
        groups: dict[str, dict[str, Any]],
        *,
        group_names: tuple[str, ...] = ("US", "RU"),
    ) -> list[str]:
        """
        Синхронизирует object-group fqdn + dns-proxy route для указанных групп.
        Логика как в gokeenapi AddDnsRoutingGroups (инкрементально).
        """
        log: list[str] = []
        with self._client_ctx() as client:
            self._auth(client)
            existing = self.get_fqdn_groups(client)
            routes = self.get_dns_routes(client)
            cmds: list[str] = []

            for gname in group_names:
                spec = groups.get(gname) or {}
                iface = (spec.get("interface_id") or "").strip()
                raw_lines = spec.get("lines") or []
                if not isinstance(raw_lines, list):
                    raw_lines = []
                want = [x for x in _norm_lines([str(x) for x in raw_lines]) if _valid_entry(x)]
                if not iface:
                    if want:
                        log.append(f"{gname}: пропуск — не задан interface_id")
                    continue
                if not want:
                    log.append(f"{gname}: пропуск — пустой список строк")
                    continue

                have = set(existing.get(gname, []))
                want_set = set(want)

                if gname not in existing:
                    cmds.append(f"object-group fqdn {gname}")

                for ex in existing.get(gname, []):
                    if ex not in want_set:
                        cmds.append(f"no object-group fqdn {gname} include {ex}")

                for w in want:
                    if w not in have:
                        cmds.append(f"object-group fqdn {gname} include {w}")

                cur_if = routes.get(gname)
                if cur_if != iface:
                    if cur_if:
                        cmds.append(f"no dns-proxy route object-group {gname} {cur_if}")
                    cmds.append(f"dns-proxy route object-group {gname} {iface} auto")

            if not cmds:
                log.append("Изменений нет (уже совпадает с роутером)")
                return log

            cmds.append("system configuration save")
            logger.info("RCI %s команд на %s", len(cmds), self.base_url)
            self._post_parse(client, cmds)
            log.append(f"Применено команд: {len(cmds)}")
        return log


def test_connection(base_url: str, login: str, password: str) -> tuple[bool, str]:
    try:
        with httpx.Client(
            base_url=base_url.rstrip("/"),
            verify=False,
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        ) as c:
            k = KeeneticRCI(base_url, login, password)
            k._auth(c)
            r = c.get("/rci/show/version")
            if r.status_code != 200:
                if r.status_code == 401:
                    return (
                        False,
                        "RCI /rci/show/version → 401: сессия не принята "
                        "(часто неверный логин/пароль или устаревший клиент; обновите сервис).",
                    )
                return False, f"version HTTP {r.status_code}"
            try:
                j = r.json()
            except ValueError:
                return False, f"version: ответ не JSON (проверь URL прокси): {r.text[:120]!r}"
            title = j.get("title") or j.get("Title") or "?"
            return True, str(title)
    except Exception as e:
        return False, str(e)
