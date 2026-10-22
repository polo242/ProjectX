import tempfile
from pathlib import Path
from collections import Counter
from datetime import timedelta
import unittest
import agent
from collection import Store,utcnow
from capture import capture,export_raw

class Driver:
    def __init__(self,posts,blocked=False): self.posts=posts; self.blocked=blocked
    def execute_script(self,script,*args):
        if script==agent.OBSERVE_JS: return {'url':'https://x.com/search','body':'verify you are human' if self.blocked else ''}
        return self.posts

class CaptureTests(unittest.TestCase):
    def test_500_unique_and_persistent_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db'); counts=Counter(); seen=set()
            posts=[{'id':str(i),'author':'test','text':'Bitcoin announcement','published_at':(utcnow()-timedelta(minutes=10)).isoformat()} for i in range(500)]
            self.assertEqual(capture(Driver(posts),store,'target',seen,counts),500)
            self.assertEqual(capture(Driver(posts),store,'target',seen,counts),0)
            self.assertEqual(capture(Driver(posts),store,'target',set(),Counter()),0)
            out=Path(folder)/'out.jsonl'; self.assertEqual(export_raw(store,out),500)
            self.assertEqual(len(out.read_text(encoding='utf-8').splitlines()),500)
            store.close()

    def test_invalid_old_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'db'); counts=Counter()
            posts=[dict(id='1',author='a',text='BTC',published_at=(utcnow()-timedelta(hours=25)).isoformat()),dict(id='2',author='a',text='BTC',published_at=None)]
            self.assertEqual(capture(Driver(posts),store,'target',set(),counts),0)
            self.assertEqual(counts['old'],1); self.assertEqual(counts['invalid_date'],1)
            with self.assertRaises(agent.NeedsHuman): capture(Driver([],True),store,'target',set(),counts)
            store.close()

if __name__=='__main__': unittest.main()
