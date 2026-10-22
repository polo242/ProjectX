import unittest
from capture import HistoryProgress

class HistoryTests(unittest.TestCase):
    def test_duplicates_with_movement_do_not_stop(self):
        p=HistoryProgress()
        for i in range(30):
            self.assertFalse(p.update(dict(y=i*500,height=20000,viewport=800),0))

    def test_bottom_requires_repeated_stall(self):
        p=HistoryProgress(); m=dict(y=1200,height=2000,viewport=800)
        self.assertFalse(p.update(m,0))
        for _ in range(5): self.assertFalse(p.update(m,0))
        self.assertTrue(p.update(m,0))
        self.assertFalse(p.update(m,1))

    def test_loading_does_not_count_as_exhaustion(self):
        p=HistoryProgress(); m=dict(y=1200,height=2000,viewport=800,loading=True)
        for _ in range(20): self.assertFalse(p.update(m,0))

if __name__=='__main__': unittest.main()
