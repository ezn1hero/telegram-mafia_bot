# Mafia Community Bot

Telegram community bot for the Mafia game: coin economy, perk shop, multilingual UI,
Day/Night GIF sharing, and engagement events. Backed by PostgreSQL.

## Quick start (local, SQLite)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env: set BOT_TOKEN and DATABASE_URL=sqlite+aiosqlite:///./mafia.db
python main.py
```

## Production (Postgres + PgBouncer + Redis)

```powershell
docker compose up -d
# DATABASE_URL=postgresql+asyncpg://mafia:mafia@localhost:6432/mafia
python main.py
```

On first run the bot auto-creates tables and seeds the perk catalog.

## Bot commands

- `/start` — register, pick language, grant 100 starter coins
- `/language` — switch UI language (en, es, fr, de, zh)
- `/balance` — show coin balance
- `/shop` — list perks
- `/buy <perk_code>` — purchase a perk
- `/inventory` — list owned perks
- `/daily` — claim daily +50 coins (streak-aware)
- `/clip day` / `/clip night` — post Day/Night GIF, earns +5 coins (3/day cap)
- `/brag` — share a formatted achievement card
- `/translate` — reply to a message to translate it (stub)
- `/report` — reply to flag a message to moderators
- `/rules` — show community rules in your language

## Project layout

```
main.py                 entry point
bot/                    aiogram handlers, keyboards, middlewares
db/                     SQLAlchemy models and session
services/               business logic (economy, perks, i18n)
locales/                en/es/fr/de/zh translations
docker-compose.yml      Postgres + PgBouncer + Redis
```
