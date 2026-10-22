"""Offline checks: persistence, freshness and strict LLM rating contracts."""
import json
from pathlib import Path
import tempfile
import unittest
from datetime import timedelta

import collection as c
import llm


class CollectionTests(unittest.TestCase):
    def post(self, ident='1', age=1):
        return {'id': ident, 'author': 'test', 'text': 'Bitcoin protocol analysis',
                'published_at': (c.utcnow()-timedelta(hours=age)).isoformat(),
                'url': 'https://x.com/test/status/'+ident, 'collected_at': c.utcnow().isoformat()}

    def test_age_boundaries(self):
        now=c.utcnow()
        for hours, expected in [(0,'recent'), (23.999,'recent'), (24,'old'), (25,'old'), (-1,'future_date')]:
            p={'published_at':(now-timedelta(hours=hours)).isoformat()}
            self.assertEqual(c.age_status(p,now), expected)
        for stamp in [None, 'bad', '2026-10-09T12:00:00']:
            self.assertEqual(c.age_status({'published_at':stamp},now),'invalid_date')

    def test_resume_and_atomic_export(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'test.db'; out=Path(folder)/'out.jsonl'
            s=c.Store(db); p=self.post()
            self.assertTrue(s.add(p,'bitcoin','pending')); s.close()
            s=c.Store(db)
            self.assertFalse(s.add(p,'ethereum','pending'))
            self.assertEqual(len(s.pending()),1)
            rating={'crypto':5,'quality':4,'category':'analysis','reason':'Specific information'}
            s.finish('1',rating,True)
            old=self.post('2',25); s.add(old,'bitcoin','pending'); s.finish('2',rating,True)
            s.export(out); s.export(out)
            rows=[json.loads(x) for x in out.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['id'],'1')
            self.assertEqual(rows[0]['quality'],4)
            self.assertIn('url',rows[0]); self.assertEqual(s.pending(),[])
            s.close()

    def test_ratings_matched_by_id(self):
        posts=[self.post('1'),self.post('2')]
        row=lambda ident:dict(id=ident,crypto=5,quality=4,category='news',reason='Information')
        result=c.validate_ratings(posts,{'ratings':[row('2'),row('1')]})
        self.assertEqual(set(result),{'1','2'})
        for reply in [{'ratings':[row('1')]},{'ratings':[row('1'),row('1')]},
                      {'ratings':[row('1'),row('3')]}, {'ratings':[{**row('1'),'quality':True},row('2')]}]:
            with self.assertRaises(llm.NoAnswer): c.validate_ratings(posts,reply)


if __name__=='__main__': unittest.main()
