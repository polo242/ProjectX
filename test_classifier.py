import unittest
import classifier as c

class ClassifierTests(unittest.TestCase):
    def test_author_split_and_no_text_overlap(self):
        rows=[dict(author='author'+str(i),text='Unique document number '+str(i),label=c.LABELS[i%5]) for i in range(100)]
        train,test=c.split_authors(rows)
        self.assertTrue(train and test)
        self.assertFalse({r['author'] for r in train}&{r['author'] for r in test})
        duplicate=dict(rows[0]); duplicate['author']='another_author'
        train,test=c.split_authors(rows+[duplicate])
        self.assertFalse({' '.join(c.tokens(r['text'])) for r in train}&{' '.join(c.tokens(r['text'])) for r in test})

    def test_model_and_metrics(self):
        rows=[dict(label=label,text=('topic'+str(i)+' ')*10) for i,label in enumerate(c.LABELS)]
        model=c.fit(rows)
        for r in rows: self.assertEqual(c.classify(model,r['text']),r['label'])
        self.assertEqual(c.evaluate(model,rows)['macro_f1'],1)

    def test_no_author_or_url_tokens(self):
        self.assertNotIn('famousaccount',c.tokens('@famousaccount Bitcoin https://example.com'))
        self.assertNotIn('example',c.tokens('@famousaccount Bitcoin https://example.com'))

if __name__=='__main__': unittest.main()
