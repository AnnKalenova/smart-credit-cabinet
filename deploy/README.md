# Публикация на сервере с доменом и HTTPS

Здесь по шагам: как с нуля настроить VDS, направить на него домен и поднять кабинет с HTTPS.

На сервере работает тот же `docker compose`, что и на компьютере, только перед ним стоит [Caddy](https://caddyserver.com). Caddy принимает запросы на 80 и 443, сам получает сертификат Let's Encrypt, сам его продлевает и перекидывает `http://` на `https://`. Руками с сертификатами ничего делать не нужно.

```
посетитель ──https──► Caddy :443 ──► nginx (web) ──► API, воркер, PostgreSQL, сервис внешних систем
                      сертификат      интерфейс и /api
```

Наружу открыты только 80 и 443. Всё остальное слушает `127.0.0.1` и доступно только с самого сервера.

## Что понадобится

- VDS с Ubuntu 24.04 или 22.04: 2 vCPU, 2–4 ГБ памяти, от 20 ГБ диска. На 1 ГБ тоже заведётся, если добавить подкачку (шаг 7).
- Домен или поддомен, например `credit.example.ru`.
- SSH-ключ (шаг 1).

Ниже `IP` — адрес сервера, `ann` — имя пользователя (можно своё), `credit.example.ru` — ваш домен.

## 1. SSH-ключ на своём компьютере

В PowerShell (Windows) или терминале (macOS, Linux):

```powershell
ssh-keygen -t ed25519 -C "credit-vds"
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub      # macOS и Linux: cat ~/.ssh/id_ed25519.pub
```

Строку `ssh-ed25519 …` вставьте в панели хостинга при создании сервера, в поле «SSH-ключ». Если сервер уже создан с паролем — ничего страшного, ключ добавим на шаге 3.

## 2. Первый вход и обновления

```bash
ssh root@IP
apt update && apt -y full-upgrade
apt -y install ufw fail2ban unattended-upgrades git unzip
timedatectl set-timezone Europe/Moscow
reboot                     # если обновилось ядро; через минуту зайти снова: ssh root@IP
```

## 3. Свой пользователь вместо root

```bash
adduser ann                                         # пароль понадобится для sudo
usermod -aG sudo ann
rsync --archive --chown=ann:ann ~/.ssh /home/ann    # тот же ключ, с которым вы зашли под root
```

Если под root вы заходили по паролю и ключа на сервере нет, добавьте его со своего компьютера:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | ssh ann@IP "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```

Проверьте в новом окне: `ssh ann@IP` пускает без пароля, а `sudo whoami` отвечает `root`.

## 4. Вход только по ключу и без root

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf > /dev/null <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

Текущее окно не закрывайте, пока в новом не получится `ssh ann@IP`, — иначе можно закрыть себе доступ к серверу.

## 5. Брандмауэр

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443
sudo ufw --force enable
sudo ufw status
```

## 6. Защита от подбора пароля и автообновления

```bash
sudo systemctl enable --now fail2ban
sudo dpkg-reconfigure -f noninteractive unattended-upgrades
```

fail2ban блокирует адреса, которые пытаются подобрать SSH. Обновления безопасности будут ставиться сами.

## 7. Подкачка — если памяти 2 ГБ или меньше

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

Без неё сборка образов на маленьком сервере может упасть из-за нехватки памяти.

## 8. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ann
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
sudo systemctl restart docker
exit                      # перезайти, чтобы заработала группа docker
```

```bash
ssh ann@IP
docker run --rm hello-world
```

`daemon.json` ограничивает логи контейнеров, иначе за несколько недель они могут съесть диск.

Если `get.docker.com` недоступен, поставьте Docker из пакетов Ubuntu: `sudo apt -y install docker.io docker-compose-v2`.

## 9. Домен → сервер

В панели, где куплен домен, добавьте A-запись:

| Тип | Имя | Значение |
|---|---|---|
| A | `credit` (для поддомена) или `@` (для самого домена) | IP сервера |

Обычно запись начинает работать через 5–30 минут. Проверка со своего компьютера: `nslookup credit.example.ru` должен показать IP сервера.

## 10. Код на сервер

Если репозиторий публичный:

```bash
git clone https://github.com/ВАШ-ЛОГИН/smart-credit-cabinet.git
```

Если приватный, сделайте ключ только для чтения:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N ""
cat ~/.ssh/github.pub      # GitHub → репозиторий → Settings → Deploy keys → Add deploy key (галочку записи не ставить)
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n' >> ~/.ssh/config
git clone git@github.com:ВАШ-ЛОГИН/smart-credit-cabinet.git     # на вопрос про отпечаток ответить yes
```

Или просто загрузите архив со своего компьютера:

```powershell
scp $env:USERPROFILE\Downloads\smart-credit-cabinet.zip ann@IP:~
ssh ann@IP "unzip -q smart-credit-cabinet.zip"
```

## 11. Запуск

```bash
cd ~/smart-credit-cabinet
bash deploy/init.sh                    # спросит домен и почту, пароли придумает сам
bash deploy/compose.sh up -d --build   # первая сборка — 3–7 минут
```

Проверка:

```bash
bash deploy/compose.sh ps                                   # все сервисы — running (healthy)
bash deploy/compose.sh logs caddy | grep -i "certificate obtained"
curl -I http://credit.example.ru                            # 308 и Location: https://…
```

Дальше откройте в браузере `https://credit.example.ru`: должен быть замок и страница входа с демо-учётками. Swagger — по адресу `https://credit.example.ru/api/docs`.

## Каждый день

| Что | Команда |
|---|---|
| Обновить после новых коммитов | `git pull && bash deploy/compose.sh up -d --build` |
| Посмотреть, что запущено | `bash deploy/compose.sh ps` |
| Логи API или Caddy | `bash deploy/compose.sh logs -f api` или `bash deploy/compose.sh logs -f caddy` |
| Вернуть демо-данные к исходным | `bash deploy/reset-demo.sh` |
| Убрать старые образы после обновлений | `docker image prune -f` |
| Остановить | `bash deploy/compose.sh down` |

`reset-demo.sh` стирает заявки и кредиты, которые насоздавали посетители, и возвращает параметры политики. Сертификаты и пароли он не трогает. Можно сбрасывать каждую ночь в 4:00:

```bash
(crontab -l 2>/dev/null; echo "0 4 * * * cd $HOME/smart-credit-cabinet && bash deploy/reset-demo.sh >> $HOME/reset-demo.log 2>&1") | crontab -
```

Не запускайте `down -v`: эта команда удалит и том с сертификатами, а Let's Encrypt выдаёт одному домену не больше 5 одинаковых сертификатов в неделю.

## Настройки

Файл `deploy/.env` создаёт `deploy/init.sh`, в git он не попадает.

| Переменная | Зачем |
|---|---|
| `DOMAIN` | домен сайта, на него Caddy получает сертификат |
| `ACME_EMAIL` | почта для писем Let's Encrypt, например если сертификат не удалось продлить |
| `JWT_SECRET` | секрет для токенов входа, случайная строка |
| `POSTGRES_PASSWORD` | пароль базы. Задаётся при первом запуске, пока база пустая |
| `DEMO_MODE` | `true` — вход в один клик и коды подписи прямо в окне, как удобно жюри |
| `WEB_PORT` | `127.0.0.1:8080` — без Caddy интерфейс открывается только с самого сервера |

Остальные настройки такие же, как в корневом `.env.example`, их можно дописать в `deploy/.env`.

## Если что-то не так

- **Нет сертификата, в логах Caddy ошибка `acme`.** Проверьте, что `nslookup` домена показывает IP сервера, а порты 80 и 443 открыты: Let's Encrypt проверяет домен запросом на порт 80. Потом `bash deploy/compose.sh restart caddy`.
- **Не скачиваются образы (`403` или `timeout` от `registry-1.docker.io`).** С серверов в России Docker Hub иногда недоступен. Включите зеркало и запустите сборку ещё раз:
  ```bash
  sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
  {
    "registry-mirrors": ["https://mirror.gcr.io"],
    "log-driver": "json-file",
    "log-opts": { "max-size": "10m", "max-file": "3" }
  }
  EOF
  sudo systemctl restart docker
  ```
  У многих хостингов есть своё зеркало Docker Hub — адрес обычно есть в их документации.
- **Сборка падает с `Killed` или `exit code 137`.** Не хватает памяти — добавьте подкачку (шаг 7).
- **API не подключается к базе после смены `POSTGRES_PASSWORD`.** Пароль применяется только к пустой базе. Чтобы поставить новый, удалите том базы (демо-данные загрузятся заново): `bash deploy/compose.sh down && docker volume rm smart-credit-cabinet_db-data && bash deploy/compose.sh up -d`.
- **Ошибка `Pool overlaps` при запуске.** Подсеть `172.30.250.0/24` уже занята. Поменяйте её на свободную в `deploy/docker-compose.prod.yml` и `deploy/nginx/real-ip.conf`.

## Как это устроено

- `docker-compose.prod.yml` дополняет основной `docker-compose.yml`:
  - добавляет Caddy;
  - подключает интерфейс к сети `edge` с фиксированной подсетью;
  - монтирует `nginx/real-ip.conf`.
- `nginx/real-ip.conf` нужен, чтобы ограничение попыток входа считалось для каждого посетителя, а не для всех сразу. nginx берёт адрес посетителя из заголовка `X-Forwarded-For`, только если заголовок пришёл от Caddy из сети `edge`. Подставить чужой адрес снаружи не выйдет: Caddy заменяет такой заголовок настоящим адресом соединения.
- `Caddyfile` — домен, переадресация на HTTPS и заголовок `Strict-Transport-Security`. Остальные заголовки безопасности (CSP и другие) ставит nginx, как и на компьютере.
