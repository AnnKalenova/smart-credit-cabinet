# Публикация на сервере с доменом и HTTPS

На сервере запускается тот же `docker compose`, что и локально. Перед ним стоит [Caddy](https://caddyserver.com): он принимает запросы на портах 80 и 443, сам получает сертификат Let's Encrypt, продлевает его и перенаправляет `http://` на `https://`. Сертификаты руками выпускать и продлевать не нужно.

```
посетитель ──https──► Caddy :443 ──► nginx (web) ──► API, воркер, PostgreSQL, сервис интеграций
                      сертификат      интерфейс и /api
```

Наружу открыты только порты 80 и 443. Порты интерфейса, API и сервиса интеграций привязаны к `127.0.0.1` и доступны только с самого сервера.

## Что понадобится

- Сервер (VDS) с Ubuntu 22.04 или 24.04: 2 vCPU, 2 ГБ памяти, 20 ГБ диска. На 1 ГБ тоже работает — добавьте подкачку (шаг 2).
- Домен или поддомен, например `credit.example.ru`.
- Доступ к серверу по SSH.

## 1. Направить домен на сервер

В панели, где куплен домен, добавьте A-запись:

| Тип | Имя | Значение |
|---|---|---|
| A | `credit` (для поддомена) или `@` (для самого домена) | IP-адрес сервера |

Проверка (обычно запись появляется за 5–30 минут):

```bash
ping credit.example.ru
```

Ответить должен IP вашего сервера.

## 2. Подготовить сервер

```bash
ssh root@IP-сервера

# Docker с Compose (если get.docker.com недоступен: apt-get update && apt-get install -y docker.io docker-compose-v2)
curl -fsSL https://get.docker.com | sh

# брандмауэр: SSH, HTTP и HTTPS
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443 && ufw --force enable

# только если памяти 1 ГБ: подкачка 2 ГБ, чтобы хватило на сборку образов
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile \
  && echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

## 3. Загрузить код

Из GitHub:

```bash
git clone https://github.com/ВАШ-ЛОГИН/smart-credit-cabinet.git
cd smart-credit-cabinet
```

Или из архива — с компьютера, где он лежит (PowerShell или терминал):

```bash
scp smart-credit-cabinet.zip root@IP-сервера:~
ssh root@IP-сервера "apt-get install -y unzip && unzip -q smart-credit-cabinet.zip"
```

## 4. Настроить и запустить

```bash
cd ~/smart-credit-cabinet
bash deploy/init.sh                 # спросит домен и почту, пароли сгенерирует сам
bash deploy/compose.sh up -d --build
```

Первая сборка занимает 3–7 минут. Проверка:

```bash
bash deploy/compose.sh ps                         # все сервисы — running (healthy)
bash deploy/compose.sh logs caddy | grep -i "certificate obtained"
```

Откройте `https://credit.example.ru` — должен быть замок в адресной строке и страница входа с демо-учётками. Swagger — `https://credit.example.ru/api/docs`.

## Каждый день

| Что | Команда |
|---|---|
| Обновить после новых коммитов | `git pull && bash deploy/compose.sh up -d --build` |
| Посмотреть, что запущено | `bash deploy/compose.sh ps` |
| Логи API или Caddy | `bash deploy/compose.sh logs -f api` · `bash deploy/compose.sh logs -f caddy` |
| Вернуть демо-данные к исходным | `bash deploy/reset-demo.sh` |
| Остановить | `bash deploy/compose.sh down` |

Сброс демо-данных удаляет заявки и кредиты, которые насоздавали посетители, и возвращает параметры политики. Сертификаты и пароли он не трогает. Сбрасывать каждую ночь в 4:00:

```bash
(crontab -l 2>/dev/null; echo "0 4 * * * cd $HOME/smart-credit-cabinet && bash deploy/reset-demo.sh >> $HOME/reset-demo.log 2>&1") | crontab -
```

Не запускайте `down -v`: эта команда удалит и том с сертификатами, а Let's Encrypt выпускает одному домену не больше 5 одинаковых сертификатов в неделю.

## Настройки

Файл `deploy/.env` создаёт `deploy/init.sh`. Он не попадает в git.

| Переменная | Зачем |
|---|---|
| `DOMAIN` | домен сайта: на него Caddy получает сертификат |
| `ACME_EMAIL` | почта для писем Let's Encrypt (например, если сертификат не удалось продлить) |
| `JWT_SECRET` | секрет подписи токенов входа, случайная строка |
| `POSTGRES_PASSWORD` | пароль базы; применяется при первом запуске, когда база ещё пустая |
| `DEMO_MODE` | `true` — вход в один клик и коды подписи в окне, как нужно жюри |
| `WEB_PORT` | `127.0.0.1:8080` — интерфейс без Caddy доступен только с самого сервера |

Остальные настройки — как в корневом `.env.example`: их можно дописать в `deploy/.env`.

## Если что-то не так

- **Нет сертификата, в логах Caddy ошибка `acme`.** Проверьте, что `ping домен` показывает IP сервера и порты 80 и 443 открыты: Let's Encrypt проверяет домен запросом на порт 80. После исправления: `bash deploy/compose.sh restart caddy`.
- **Сборка не может скачать образы (`403`, `timeout` от `registry-1.docker.io`).** Docker Hub бывает недоступен с серверов в России. Включите зеркало и повторите сборку:
  ```bash
  echo '{"registry-mirrors": ["https://mirror.gcr.io"]}' > /etc/docker/daemon.json && systemctl restart docker
  ```
  У многих хостингов есть своё зеркало Docker Hub — адрес указан в их документации.
- **Сборка прерывается из-за памяти (`Killed`, `exit code 137`).** Добавьте подкачку (шаг 2).
- **API не подключается к базе после смены `POSTGRES_PASSWORD`.** Пароль применяется только к пустой базе. Чтобы задать новый, удалите том базы (демо-данные загрузятся заново): `bash deploy/compose.sh down && docker volume rm smart-credit-cabinet_db-data && bash deploy/compose.sh up -d`.
- **Ошибка `Pool overlaps` при запуске.** Подсеть `172.30.250.0/24` уже занята на сервере. Поменяйте её в `deploy/docker-compose.prod.yml` и `deploy/nginx/real-ip.conf` на свободную.

## Как это устроено

- `docker-compose.prod.yml` дополняет основной `docker-compose.yml`:
  - добавляет Caddy;
  - подключает интерфейс к сети `edge` с фиксированной подсетью;
  - монтирует `nginx/real-ip.conf`.
- `nginx/real-ip.conf` нужен, чтобы ограничения на попытки входа работали для каждого посетителя, а не для всех сразу. nginx берёт адрес посетителя из заголовка `X-Forwarded-For` — но только если заголовок пришёл от Caddy из сети `edge`. Подставить чужой адрес снаружи нельзя: Caddy заменяет такой заголовок настоящим адресом соединения.
- `Caddyfile` — домен, перенаправление на HTTPS и заголовок `Strict-Transport-Security`. Остальные заголовки безопасности (CSP и другие) ставит nginx, как и локально.
