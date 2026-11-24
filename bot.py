"""Volyn 3.1: public source -> validated PNG grid -> Telegram."""
import argparse
import io
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from PIL import Image

BASE = 'https://api-voe-poweron.inneti.net'
SOURCE = 'https://energy.volyn.ua/spozhyvacham/perervy-u-elektropostachanni/hrafik-vidkliuchen/'
TZ = ZoneInfo('Europe/Kyiv')
STATE = Path('.state/state.json')
ON, OFF, WHITE = (185, 200, 215), (68, 110, 155), (255, 255, 255)


def get(url):
    with urlopen(Request(url, headers={'User-Agent': 'VolynPersonalSchedule/1.0'}), timeout=25) as r:
        data = r.read(12_000_001)
    if len(data) > 12_000_000:
        raise ValueError('Source too large')
    return data


def api(path):
    return json.loads(get(BASE + '/api/' + path))['hydra:member']


def intervals(slots):
    result = []
    start = None
    for i, off in enumerate(list(slots) + [False]):
        if off and start is None:
            start = i * 30
        elif not off and start is not None:
            result.append([start, i * 30])
            start = None
    return result


def parse_png(data, queue='3.1'):
    im = Image.open(io.BytesIO(data)).convert('RGB')
    if im.size != (1750, 2250):
        raise ValueError('Unknown graph size; manual review required')
    # Guard the known template: palette, legend, grid and both half-day tables.
    if im.getpixel((570, 2110)) != ON or im.getpixel((920, 2110)) != OFF:
        raise ValueError('Unknown legend')
    rows = []
    for base_y in (419, 1309):
        half = []
        for row in range(12):
            y = base_y + row * 60 + 30
            values = []
            for col in range(24):
                x = 175 + col * 65 + 32
                if im.getpixel((x, base_y + row * 60 - 1)) != WHITE:
                    raise ValueError('Grid changed')
                if im.getpixel((174 + col * 65, y)) != WHITE:
                    raise ValueError('Grid columns changed')
                samples = [im.getpixel((x + dx, y + dy)) for dx, dy in ((0, 0), (-8, -8), (8, 8))]
                if len(set(samples)) != 1 or samples[0] not in (ON, OFF):
                    raise ValueError('Unknown cell colour')
                values.append(samples[0] == OFF)
            half.append(values)
        rows.append(half)
    group, sub = map(int, queue.split('.'))
    if not 1 <= group <= 6 or sub not in (1, 2):
        raise ValueError('Invalid queue')
    index = (group - 1) * 2 + sub - 1
    return intervals(rows[0][index] + rows[1][index])


def clock(minutes):
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def describe(spans):
    return ', '.join(f'{clock(a)}–{clock(b)}' for a, b in spans) or 'відключень за графіком немає'


def media_url(path):
    if not isinstance(path, str) or not path.startswith('/media/') or '..' in path:
        raise ValueError('Unexpected image path')
    return BASE + path


def schedules(now):
    # Match current image to its archive record to verify the graph date.
    records = api('archive_gpv_graphs?page=1')
    result = []
    for offset, name in ((0, 'today'), (1, 'tomorrow')):
        expected = (now.date() + timedelta(days=offset)).isoformat()
        options = api('options?option_key=pw_gpv_image_' + name)
        path = options[0].get('value') if options else None
        if not path or len(path) < 2:
            if offset == 0:
                raise ValueError('Today graph missing')
            continue
        record = next((r for r in records if r.get('imageUrl', {}).get('contentUrl') == path), None)
        if not record or record.get('dateGraph', '')[:10] != expected:
            raise ValueError('Graph date not confirmed')
        spans = parse_png(get(media_url(path)))
        result.append({'date': expected, 'spans': spans, 'url': media_url(path)})
    return result


def send(text):
    token, chat = os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID']
    data = json.dumps({'chat_id': chat, 'text': text, 'link_preview_options': {'is_disabled': True}}).encode()
    # Never print request URLs or exceptions: Telegram URL contains a secret.
    try:
        req = Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=25) as r:
            ok = json.load(r).get('ok')
        if not ok:
            raise RuntimeError()
    except Exception:
        raise RuntimeError('Не вдалося надіслати повідомлення в Telegram') from None


def run(now, current, state, deliver=send):
    known = state.setdefault('days', {})
    reminded = state.setdefault('reminded', [])
    for item in current:
        day, spans = item['date'], item['spans']
        previous = known.get(day)
        if previous != spans:
            title = 'Графік оновлено' if previous is not None else 'Графік опубліковано'
            text = f'⚡ Підчерга 3.1 · {day}\n{title}\nВідключення: {describe(spans)}'
            if previous is not None:
                text += f'\nРаніше: {describe(previous)}'
            text += f'\nПеревірено: {now:%d.%m %H:%M}\n{item["url"]}'
            deliver(text)
            known[day] = spans
        for start, end in spans:
            at = datetime.fromisoformat(day).replace(tzinfo=TZ) + timedelta(minutes=start)
            remaining = (at - now).total_seconds() / 60
            key = f'{day}:{start}'
            if 0 < remaining <= 30 and key not in reminded:
                deliver(f'⏰ Підчерга 3.1: відключення через ~{int(remaining)} хв.\n'
                        f'{day} · {clock(start)}–{clock(end)}\nЗа останнім перевіреним графіком.')
                reminded.append(key)
    cutoff = (now.date() - timedelta(days=2)).isoformat()
    state['days'] = {k: v for k, v in known.items() if k >= cutoff}
    state['reminded'] = [k for k in reminded if k[:10] >= cutoff]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--image', help='Перевірити локальний PNG без доступу до мережі та Telegram')
    args = parser.parse_args()
    if args.image:
        print(describe(parse_png(Path(args.image).read_bytes())))
        return
    now = datetime.now(TZ)
    if args.dry_run:
        print(json.dumps(schedules(now), ensure_ascii=False, indent=2))
        return
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    status = 0
    try:
        run(now, schedules(now), state)
        if state.pop('error', False):
            send('✅ Отримання графіка 3.1 відновлено.')
        state.pop('last_error_alert', None)
    except Exception:
        status = 1
        last = state.get('last_error_alert', 0)
        state['error'] = True
        if now.timestamp() - last >= 6 * 3600:
            try:
                send('⚠️ Не вдалося перевірити графік 3.1. Останні дані можуть бути застарілими.\n' + SOURCE)
                state['last_error_alert'] = now.timestamp()
            except Exception:
                pass
        print('Помилка перевірки або надсилання; неперевірений графік не надіслано.', file=sys.stderr)
    finally:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False))
    sys.exit(status)


if __name__ == '__main__':
    main()
