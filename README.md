# Keenetic DNS Routes

Мини-сервис для роутеров **без HydraRoute Neo**: централизованное редактирование списков **US / RU** (домены и IP/CIDR, по одной строке) и применение на роутеры через **NDM RCI** — те же `object-group fqdn` и `dns-proxy route`, что создаёт веб-интерфейс «Маршруты DNS» ([документация Keenetic](https://support.keenetic.com/carrier/kn-1711/en/51150-dns-based-routes.html)).

С **Keenetic Unified** и **domen_hydra** не смешивается: другой порт (**8001**), другие роутеры в своём списке, отдельный `data/store.json`.

Логика синхронизации списков с роутером совместима с подходом [gokeenapi](https://github.com/Noksa/gokeenapi) (`GET /rci/object-group/fqdn`, `GET /rci/dns-proxy/route`, `POST /rci/` с массивом `{"parse":"…"}`).

## Требования

- KeeneticOS **≥ 5.0.1** (DNS-based routes).
- Доступ к RCI с VPS: **KeenDNS** + **HTTP Proxy** для API (четвёртый уровень `rci.…`, порт **79**): [инструкция Keenetic](https://support.keenetic.com/hero/kn-1012/en/55035-using-api-methods-through-the-http-proxy-service.html).
- Пользователю роутера выданы права на **HTTP Proxy**; логин/пароль одинаковые для всех legacy-роутеров (задаются в `.env` сервиса).

## Установка (Ubuntu)

```bash
git clone https://github.com/andrey271192/keenetic-dns-routes.git /opt/keenetic-dns-routes
cd /opt/keenetic-dns-routes
sudo bash install.sh
nano .env   # ADMIN_PASSWORD, KEENETIC_LOGIN, KEENETIC_PASSWORD
sudo systemctl restart keenetic-dns-routes
```

Интерфейс: `http://IP_СЕРВЕРА:8001`

## Настройка

1. В **Interface ID** для US/RU укажи внутреннее имя интерфейса Keenetic (как в CLI: `Wireguard0`, `GigabitEthernet0`, `PPPoE0` и т.д.). Узнать можно в веб-интерфейсе или через `show interface` / утилиту [gokeenapi](https://github.com/Noksa/gokeenapi) `show-interfaces`.
2. В списках — **одна строка = один домен или IPv4/IPv6/CIDR**. Пустые строки и строки с `#` в начале игнорируются.
3. Добавь роутеры: **RCI URL** вида `http://rci.имя.keenetic.pro:79` (без слэша в конце).
4. **Сохранить на сервер** — только JSON на VPS.
5. **Применить на всех legacy** или отметь галочками и **Только на выбранных** — пошлёт на каждый RCI дифф: удалит лишние `include`, добавит новые, обновит `dns-proxy route` при смене интерфейса, в конце `system configuration save`.

### API (скрипты)

Все запросы с заголовком `X-Admin-Password`.

- `PUT /api/data` — полное или частичное обновление (`groups` и/или `routers`).
- `POST /api/groups/{US|RU}/lines` — тело `{"add":["a.com"],"remove":["b.com"]}`: правка списка **на сервере** без пересылки всего textarea (порядок: сначала удаления, затем добавления в конец).
- `POST /api/apply` — `{"mode":"all"|"selected","router_ids":["id1"]}`.

## Ограничения

- На один object-group Keenetic заводит лимит по числу записей (ориентир **~300** доменов на группу — как в gokeenapi). При превышении роутер может вернуть ошибку RCI.
- Строки без точки (не похожие на домен и не на IP/CIDR) отбрасываются при применении.

## Обновление

```bash
cd /opt/keenetic-dns-routes && git pull && sudo systemctl restart keenetic-dns-routes
```

## Связь

Проект рядом по смыслу с [keenetic-unified](https://github.com/andrey271192/keenetic-unified) (Neo + дашборд) и [domen_hydra](https://github.com/andrey271192/domen_hydra) (только Neo-конфиг), но предназначен **только** для встроенной DNS-маршрутизации без Neo.
