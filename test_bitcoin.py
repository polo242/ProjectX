import unittest
import capture
from test_capture import CaptureTests

class BitcoinTests(unittest.TestCase):
    def test_priority_before_discovery(self):
        queries, count = capture.prioritized_bitcoin_queries(capture.a.HERE/'sources.json')
        self.assertEqual(count,10)
        self.assertEqual(sum(q.count('from:') for q in queries[:count]),48)
        self.assertTrue(all(capture.BITCOIN_QUERY in q for q in queries[:count]))
        self.assertTrue(all('-filter:replies' not in q for q in queries[:count]))
        self.assertEqual(queries[count:],capture.bitcoin_queries())
        for q in queries:
            self.assertEqual(capture.a.validate('search',{'query':q})[1]['query'],q)

    def test_matching(self):
        for text in ['Bitcoin update','#bitcoin','$BTC rallies','BTC/USD','₿','ＢＴＣ','比特币 Bitcoin']:
            self.assertTrue(capture.mentions_bitcoin(text),text)
        for text in ['ETH only','BTCST token','bitcoinist site','@bitcoin_news','']:
            self.assertFalse(capture.mentions_bitcoin(text),text)
    def test_searches_are_broad(self):
        self.assertFalse(any('from:' in q or 'min_faves:' in q or '-filter:replies' in q for q in capture.bitcoin_queries()))

if __name__=='__main__': unittest.main()
