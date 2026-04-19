# Telegram Statistics Bot

Python Telegram-бот для сбора сообщений, расчета дневной статистики и публикации ежедневного отчета.

## Структура проекта

```text
.
├── app/
│   ├── cleanup.py
│   ├── config.py
│   ├── db.py
│   ├── report.py
│   └── topics.py
├── deploy/
│   └── telegram-bot.service
├── bot.py
├── daily_report.py
├── requirements.txt
└── .env.example
```

## Деплой на Ubuntu VPS (без Docker)

Ниже пошаговая инструкция для Ubuntu 22.04+.

### 1. Установить системные пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git
```

### 2. Склонировать проект

```bash
sudo mkdir -p /opt/telegram-stats-bot
sudo chown -R $USER:$USER /opt/telegram-stats-bot
git clone <YOUR_REPO_URL> /opt/telegram-stats-bot
cd /opt/telegram-stats-bot
```

### 3. Создать и активировать venv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 4. Установить зависимости

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Настроить `.env`

```bash
cp .env.example .env
nano .env
```

Минимально заполнить:

```env
BOT_TOKEN=your-telegram-bot-token
ALLOWED_CHAT_ID=-1001234567890
BOT_PARSE_MODE=HTML
DB_PATH=data/bot.sqlite3
LOG_LEVEL=INFO
```

Примечание:
- `ALLOWED_CHAT_ID` для группы обычно отрицательный и начинается с `-100...`.

Опционально для LLM-тем (llama.cpp-compatible endpoint):

```env
USE_LLM_TOPICS=false
LLM_BACKEND=llama_cpp
LLM_MODEL=local-model
LLM_ENDPOINT=http://127.0.0.1:8080/v1/chat/completions
LLM_TIMEOUT=10
```

Как это работает:
- `USE_LLM_TOPICS=false`: всегда используется текущая rule-based логика из `app/topics.py`.
- `USE_LLM_TOPICS=true`: сначала пробуем LLM для блоков "Основные темы" и "Характер обсуждения".
- Если LLM недоступна/ошиблась/вернула невалидный JSON: автоматически включается fallback на rule-based без падения бота.

### 6. Проверить ручной запуск

```bash
source .venv/bin/activate
python bot.py
```

Если бот стартует без ошибок, остановите `Ctrl+C` и переходите к `systemd`.

## Постоянный запуск бота через systemd

### 1. Подготовить unit-файл

В проекте есть шаблон: `deploy/telegram-bot.service`.

Отредактируйте в нем:
- `User=YOUR_USER`
- `Group=YOUR_USER`
- `WorkingDirectory=/opt/telegram-stats-bot`
- `ExecStart=/opt/telegram-stats-bot/.venv/bin/python /opt/telegram-stats-bot/bot.py`

### 2. Установить unit в systemd

```bash
sudo cp deploy/telegram-bot.service /etc/systemd/system/telegram-bot.service
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot
```

### 3. Проверить статус

```bash
sudo systemctl status telegram-bot
```

## Логи

### Логи сервиса в реальном времени

```bash
sudo journalctl -u telegram-bot -f
```

### Последние 200 строк

```bash
sudo journalctl -u telegram-bot -n 200 --no-pager
```

## Ежедневный запуск отчета (cron)

### 1. Открыть crontab

```bash
crontab -e
```

### 2. Добавить задачу (пример: каждый день в 09:00)

```cron
0 9 * * * cd /opt/telegram-stats-bot && /opt/telegram-stats-bot/.venv/bin/python /opt/telegram-stats-bot/daily_report.py >> /opt/telegram-stats-bot/logs/daily_report.log 2>&1
```

Перед этим создайте папку логов:

```bash
mkdir -p /opt/telegram-stats-bot/logs
```

Пояснение:
- `daily_report.py` отправляет отчет за предыдущий день.
- Старые записи (старше 7 дней) удаляются только после успешной отправки отчета.

## Ручная проверка отчета

Однократный запуск:

```bash
cd /opt/telegram-stats-bot
source .venv/bin/activate
python daily_report.py
```

Просмотр логов отчета (если запускался через cron):

```bash
tail -n 200 /opt/telegram-stats-bot/logs/daily_report.log
```

## Полезные команды

Перезапуск бота:

```bash
sudo systemctl restart telegram-bot
```

Остановка/запуск:

```bash
sudo systemctl stop telegram-bot
sudo systemctl start telegram-bot
```
