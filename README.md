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
├── requirements-system.txt
└── .env.example
```

## Деплой на Ubuntu VPS (без Docker)

Ниже пошаговая инструкция для Ubuntu 22.04+.

### 1. Установить системные пакеты

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
```

Или можно установить системные зависимости из файла:

```bash
sudo apt update
sudo xargs -a requirements-system.txt apt install -y
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
ENABLE_IMAGE_PROCESSING=true
MAX_IMAGE_FILE_SIZE_MB=20
```

Примечание:
- `ALLOWED_CHAT_ID` для группы обычно отрицательный и начинается с `-100...`.

Опционально для LLM-тем (llama.cpp-compatible endpoint):

```env
USE_LLM_TOPICS=false
LLM_BACKEND=llama_cpp
LLM_MODEL=local-model
LLM_ENDPOINT=http://127.0.0.1:8080/v1/chat/completions
LLM_CHAT_MODEL=local-model
LLM_CHAT_ENDPOINT=http://127.0.0.1:8080/v1/chat/completions
LLM_TIMEOUT=10
```

Как это работает:
- `USE_LLM_TOPICS=false`: всегда используется текущая rule-based логика из `app/topics.py`.
- `USE_LLM_TOPICS=true`: сначала пробуем LLM для блоков "Основные темы" и "Характер обсуждения".
- Если LLM недоступна/ошиблась/вернула невалидный JSON: автоматически включается fallback на rule-based без падения бота.
- `LLM_ENDPOINT`/`LLM_MODEL`: основная модель, которая сначала генерирует ответ для `/ask` и reply к боту.
- `LLM_CHAT_ENDPOINT`/`LLM_CHAT_MODEL`: опциональный второй этап "редактора" для чатовых ответов. Если отличаются от основных — бот отправляет сырой ответ в эту модель и просит переписать его в нормальный чатовый формат.

## Команды бота

Команды работают только в разрешенном `ALLOWED_CHAT_ID`.

- `/stats` — отчет на текущий момент за сегодня.
- `/dead` — сравнение активности сегодня и вчера.
- `/time` — примерная оценка времени, потраченного на переписку сегодня.
- `/when` — самый активный и самый тихий час за сегодня.
- `/mood` — краткая оценка характера обсуждения. При `USE_LLM_TOPICS=true` использует LLM, иначе возвращает простой fallback: активный/умеренный/тихий чат.
- `/svin` — просит локальную LLM сочинить короткий анекдот про свинью. Если LLM недоступна, команда просто ничего не отправляет.
- `/ask вопрос` — задает простой вопрос локальной LLM. Если LLM выключена или недоступна, бот отвечает коротким сообщением об этом.
- `/visual` — показывает топ визуальных кластеров: id, краткий label и количество повторов.
- `/seen` — показывает статус обработки фото/GIF/видео-превью/image-файла в reply-сообщении.
- `/llmroute` — показывает, какая модель/endpoint используются как primary и rewrite для чатовых ответов.

## Случайный "свин"

Если `USE_LLM_TOPICS=true`, бот иногда отвечает на обычные сообщения короткой ироничной фразой через LLM.

Условия:
- шанс ответа около 3%;
- минимум 10 сообщений за текущий день;
- не чаще одного раза в 10 сообщений;
- бот не отвечает на свои сообщения;
- если LLM недоступна или вернула ошибку, бот молчит.

## Визуальный контекст

Бот учитывает картинки без постоянного хранения файлов.

Включение визуальной обработки в рантайме:
- `ENABLE_IMAGE_PROCESSING=true` в `.env`;
- установлены Python-зависимости для изображений (`Pillow`, `imagehash`);
- для OCR дополнительно установлен системный `tesseract` (опционально).

Если одно из условий для image pipeline не выполнено, бот продолжит работать без падения, но визуальные команды/контекст будут недоступны.

Как это работает:
- при получении `photo` или image-документа бот скачивает файл во временную папку;
- считает perceptual hash (`phash`) через `Pillow` + `imagehash`;
- если доступен `pytesseract` и системный `tesseract`, пытается извлечь OCR-текст;
- ищет похожий визуальный кластер по расстоянию hash;
- сохраняет только метаданные в SQLite;
- после обработки удаляет временный файл.

Добавленные таблицы:
- `image_events` — отдельные события картинок: hash, cluster_id, OCR, статус обработки, факт удаления файла.
- `image_clusters` — постоянный словарь визуальных шаблонов: canonical_hash, usage_count, first_seen_at, last_seen_at, cluster_summary.

Важно:
- недельная очистка удаляет только старые записи из `messages`;
- `image_clusters` не удаляются автоматически и остаются как долговременный словарь шаблонов;
- `image_events` тоже не чистятся текущей недельной очисткой;
- daily/weekly аналитика может брать данные через `get_image_events_by_day(...)` и `get_top_image_clusters_by_day(...)`.

OCR использует Python-пакет `pytesseract` и системный бинарник `tesseract`.
Python-пакет ставится через `requirements.txt`, а системный OCR на Ubuntu нужно поставить отдельно:

```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-rus
```

Если системный `tesseract` не установлен, бот всё равно сохраняет hash и cluster, но текст на картинке читать не будет.
OCR-текст и short label хранятся как внутренние признаки. Они не выводятся пользователю как отдельные "фрагменты" в обычных отчетах.
Если `USE_LLM_TOPICS=true`, при повторных встречах похожей картинки бот собирает caption/OCR/контекст и обновляет `cluster_summary`.
Так постепенно появляется человекопонятная метка вроде "мем про сервер" или "реакция со свиньей".

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

Emergency switch for image processing while keeping the bot and commands alive:

```env
ENABLE_IMAGE_PROCESSING=false
```

After changing `.env`:

```bash
sudo systemctl restart telegram-bot
sudo journalctl -u telegram-bot -n 100 --no-pager
```

GIF support note:
- Telegram `animation` messages are handled through their thumbnail when Telegram provides one.
- This keeps GIF recognition lightweight and avoids adding ffmpeg or a heavy video pipeline.
- If Telegram sends an animation without a thumbnail, the bot stores the normal message but skips visual hashing for that file.

Video/voice note:
- Telegram `video` messages are handled via their thumbnail (preview frame), not by full video decoding.
- Voice messages are not processed by `/seen` (no ASR pipeline in this project right now).

Visual safety/context notes:
- `MAX_IMAGE_FILE_SIZE_MB` limits media downloads before processing, so very large image documents are skipped safely.
- Image processing is throttled to one file at a time, so a burst of many images does not start many downloads/hash jobs in parallel.
- `image_events.context_text` stores captions/context separately from weekly `messages`, so visual cluster labels can improve over roughly the last two months of image events.
- Context backfill is gradual: only a small batch of old image events is enriched on each bot start, not the whole database at once.
- `/ask` and direct replies/mentions can use known visual context from the replied image, but the bot does not run a full vision model.
- `/seen` can be used as a reply to an image/GIF to check whether the file has already been studied, which cluster it belongs to, and whether it has a label.
