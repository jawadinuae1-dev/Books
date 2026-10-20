# NEURAL MIND BOT

Telegram bot: roz subah viral book ka idea, roz shaam psychology topic. English + Urdu, real-life apply steps, quiz polls, score tracking.

## Setup (15 minute)

**1. Telegram token**
Telegram mein `@BotFather` → `/newbot` → naam do → token copy karo.

**2. Gemini free key**
https://aistudio.google.com/apikey → Create API key → copy.

**3. GitHub**
Naya repo banao, in 4 files upload karo: `bot.py`, `content.py`, `requirements.txt`, `railway.json`.

**4. Railway**
railway.com → New Project → Deploy from GitHub repo → apna repo chuno.
Phir service → **Variables** mein ye add karo:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | BotFather wala token |
| `GEMINI_API_KEY` | Gemini key |
| `BOT_TZ` | `Asia/Dubai` (optional) |
| `BOOK_TIME` | `08:00` (optional) |
| `PSYCH_TIME` | `20:00` (optional) |
| `DATA_DIR` | `/data` (sirf agar Volume lagaya) |
| `GEMINI_MODEL` | optional, default `gemini-2.5-flash` |

Deploy hote hi bot chal jayega. Telegram mein bot kholo → `/start`.

## Commands
`/start` · `/book` · `/psych` · `/quiz` · `/score` · `/stop`

## Zaroori baatein
- Bot sirf **ek jagah** chalna chahiye. Railway pe chal raha ho to local ya kisi aur server pe dobara na chalayein, warna conflict error aayega.
- Railway ka disk redeploy pe reset hota hai. Subscribers aur scores bachane ke liye service mein **Volume** add karo (mount path `/data`) aur `DATA_DIR=/data` set karo. Volume ke baghair redeploy ke baad dobara `/start` dabana padega.
- Railway free trial ka credit khatam ho to bot ruk jayega. Usage dashboard mein check karte rahein.
- Daily topic list `content.py` mein hai. Apni pasand ki books ya topics wahan add kar sakte hain.
