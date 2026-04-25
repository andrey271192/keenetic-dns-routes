# Keenetic DNS Routes

Мини-сервис для роутеров **без HydraRoute Neo**: централизованное редактирование списков **US / RU** (домены и IP/CIDR, по одной строке) и применение на роутеры через **NDM RCI** — те же `object-group fqdn` и `dns-proxy route`, что создаёт веб-интерфейс «Маршруты DNS» ([документация Keenetic](https://support.keenetic.com/carrier/kn-1711/en/51150-dns-based-routes.html)).

С **Keenetic Unified** и **domen_hydra** не смешивается: другой порт (**8001**), другие роутеры в своём списке, отдельный `data/store.json`.

Логика синхронизации списков с роутером совместима с подходом [gokeenapi](https://github.com/Noksa/gokeenapi) (`GET /rci/object-group/fqdn`, `GET /rci/dns-proxy/route`, `POST /rci/` с массивом `{"parse":"…"}`).

## Требования

- KeeneticOS **≥ 5.0.1** (DNS-based routes).
- Доступ к RCI с VPS: **KeenDNS** + **HTTP Proxy** для API (четвёртый уровень `rci.…`, порт **79**): [инструкция Keenetic](https://support.keenetic.com/hero/kn-1012/en/55035-using-api-methods-through-the-http-proxy-service.html).
- Пользователю роутера выданы права на **HTTP Proxy**. Учётка API: по умолчанию **`KEENETIC_*` в `.env`**, либо у каждого роутера свои поля / один раз URL `http(s)://логин:пароль@хост:порт` (при сохранении логин/пароль переносятся в поля).

## Установка (Ubuntu)

```bash
git clone https://github.com/andrey271192/keenetic-dns-routes.git /opt/keenetic-dns-routes
cd /opt/keenetic-dns-routes
sudo bash install.sh
nano .env   # ADMIN_PASSWORD + при желании KEENETIC_LOGIN / KEENETIC_PASSWORD (дефолт для роутеров без своих полей)
sudo systemctl restart keenetic-dns-routes
```

Интерфейс: `http://IP_СЕРВЕРА:8001`

## Настройка

1. В **Interface ID** для US/RU — имя интерфейса (`Wireguard0`, `PPPoE0`…). Кнопка **«Сканировать…»** подгружает список с роутера; опция **«Только WireGuard»** сужает выбор до WG-туннелей.
2. В списках — **одна строка = один домен или IPv4/IPv6/CIDR**. Пустые строки и строки с `#` в начале игнорируются.
3. Добавь роутеры: **base URL** прокси KeenDNS (`http(s)://хост:порт`, без пути `/rci/...`). Логин/пароль — в полях или в URL `логин:пароль@хост`; если пусто — из `KEENETIC_*` в `.env`.
4. **Сохранить на сервер** — только JSON на VPS.
5. **Применить на всех legacy** или отметь галочками и **Только на выбранных** — пошлёт на каждый RCI дифф: удалит лишние `include`, добавит новые, обновит `dns-proxy route` при смене интерфейса, в конце `system configuration save`.

### API (скрипты)

Все запросы с заголовком `X-Admin-Password`.

- `PUT /api/data` — полное или частичное обновление (`groups` и/или `routers`).
- `POST /api/groups/{US|RU}/lines` — тело `{"add":["a.com"],"remove":["b.com"]}`: правка списка **на сервере** без пересылки всего textarea (порядок: сначала удаления, затем добавления в конец).
- `POST /api/apply` — `{"mode":"all"|"selected","router_ids":["id1"]}`.
- `GET /api/keenetic-env` — дефолтный логин из `.env` и флаг «KEENETIC_PASSWORD задан».
- `GET /api/routers/{id}/interfaces` — список интерфейсов (`GET /rci/show/interface`); query `wireguard_only=1` — только WireGuard.
- `PATCH /api/routers/{id}` — правка имени, URL, `keenetic_login` / `keenetic_password`.

## Ограничения

- На один object-group Keenetic заводит лимит по числу записей (ориентир **~300** доменов на группу — как в gokeenapi). При превышении роутер может вернуть ошибку RCI.
- Строки без точки (не похожие на домен и не на IP/CIDR) отбрасываются при применении.

## Обновление

```bash
cd /opt/keenetic-dns-routes && sudo bash update.sh
```

Если **`update.sh: No such file or directory`**: подтяни свежий `install.sh` с репозитория и один раз выполни `sudo bash install.sh` — он **создаст** `update.sh`, если файла нет. Либо вручную:

```bash
cd /opt/keenetic-dns-routes && git pull --ff-only
source venv/bin/activate && pip install -r requirements.txt
sudo systemctl restart keenetic-dns-routes
```

Без git: скопируй каталог проекта поверх, затем снова `sudo bash update.sh` или команды выше.

## Удаление с сервера (одной командой)

Останавливается `keenetic-dns-routes`, удаляется unit и каталог **`/opt/keenetic-dns-routes`**:

```bash
curl -fsSL https://raw.githubusercontent.com/andrey271192/keenetic-dns-routes/main/uninstall.sh | sudo bash
```

Из каталога установки: `sudo bash uninstall.sh`

## Поддержка

- **GitHub:** [andrey271192](https://github.com/andrey271192)
- **Boosty:** [Поддержка](https://boosty.to/andrey27/donate) 
- **Поддержка проекта (Ozon Bank, СБП):** [ссылка](https://finance.ozon.ru/apps/sbp/ozonbankpay/019dc200-2a5d-7931-a619-782d285f6798)
- **Telegram:** [@Iot_andrey](https://t.me/Iot_andrey)

## Связь

Проект рядом по смыслу с [keenetic-unified](https://github.com/andrey271192/keenetic-unified) (Neo + дашборд) и [domen_hydra](https://github.com/andrey271192/domen_hydra) (только Neo-конфиг), но предназначен **только** для встроенной DNS-маршрутизации без Neo.
