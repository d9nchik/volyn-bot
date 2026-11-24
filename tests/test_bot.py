import io
from pathlib import Path
import unittest
from datetime import datetime
from PIL import Image
import bot

FIXTURE = Path(__file__).parent / 'fixtures/2026-11-24.png'

class BotTests(unittest.TestCase):
    def test_real_graph_31(self):
        self.assertEqual(bot.parse_png(FIXTURE.read_bytes()), [[480, 660]])

    def test_real_graph_other_rows_and_midnight(self):
        self.assertEqual(bot.parse_png(FIXTURE.read_bytes(), '1.2'), [[1350, 1440]])
        self.assertEqual(bot.parse_png(FIXTURE.read_bytes(), '4.1'), [[840, 930]])
        self.assertEqual(bot.parse_png(FIXTURE.read_bytes(), '6.2'), [[1140, 1350]])

    def test_unknown_colour_rejected(self):
        im = Image.open(FIXTURE).convert('RGB')
        im.putpixel((207, 689), (255, 0, 0))
        out = io.BytesIO()
        im.save(out, format='PNG')
        with self.assertRaises(ValueError):
            bot.parse_png(out.getvalue())

    def test_intervals_across_noon(self):
        self.assertEqual(bot.intervals([False] * 23 + [True] * 3 + [False] * 22), [[690, 780]])
        self.assertEqual(bot.intervals([True] * 48), [[0, 1440]])
        self.assertEqual(bot.intervals([False] * 48), [])

    def test_changes_and_reminder_only_once(self):
        now = datetime(2026, 11, 24, 7, 35, tzinfo=bot.TZ)
        item = {'date': '2026-11-24', 'spans': [[480, 660]], 'url': bot.BASE}
        state, messages = {}, []
        bot.run(now, [item], state, messages.append)
        self.assertEqual(len(messages), 2)
        bot.run(now, [item], state, messages.append)
        self.assertEqual(len(messages), 2)
        item['spans'] = []
        bot.run(now, [item], state, messages.append)
        self.assertEqual(len(messages), 3)
        self.assertIn('Раніше: 08:00–11:00', messages[-1])
        self.assertIn('відключень за графіком немає', messages[-1])

    def test_failed_send_retried(self):
        state = {}
        def fail(text):
            raise RuntimeError('Delivery failed')
        with self.assertRaises(RuntimeError):
            bot.run(datetime(2026, 11, 24, 12, tzinfo=bot.TZ),
                    [{'date': '2026-11-24', 'spans': [], 'url': bot.BASE}], state, fail)
        self.assertNotIn('2026-11-24', state['days'])

if __name__ == '__main__':
    unittest.main()
