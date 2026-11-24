"""Run locally after sending /start to your bot. Token input stays hidden."""
from getpass import getpass
import json
from urllib.request import urlopen

token = getpass('Токен від BotFather (введення приховане): ').strip()
try:
    with urlopen(f'https://api.telegram.org/bot{token}/getUpdates', timeout=25) as r:
        data = json.load(r)
    chats = {m['message']['chat']['id'] for m in data.get('result', [])
             if m.get('message', {}).get('chat', {}).get('type') == 'private'}
    if not chats:
        print('Спочатку надішліть /start своєму боту, а потім повторіть.')
    else:
        print('TELEGRAM_CHAT_ID:', ', '.join(map(str, sorted(chats))))
except Exception:
    print('Не вдалося отримати chat_id. Перевірте токен і з’єднання.')
