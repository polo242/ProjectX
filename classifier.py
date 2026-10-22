"""Human-labelled baseline: content type, not truthfulness or author credibility.
Commands: prepare, annotate, train, predict. Standard library only.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

HERE = Path(__file__).resolve().parent
LABELS = ('sourced_information', 'reasoned_analysis', 'opinion_prediction', 'promotion', 'unsubstantiated_claim')
DATA = HERE / 'annotations.jsonl'
MODEL = HERE / 'content_model.json'


def tokens(text):
    # No author, follower count, LLM score or collection target as model input.
    text = re.sub(r'https?://\S+', ' URL ', text.lower())
    text = re.sub(r'@[\w]+', ' USER ', text)
    return re.findall(r'[^\W_]{2,}', text, flags=re.UNICODE)[:4000]


def read_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def save_rows(path, rows):
    temp = path.with_suffix('.tmp')
    temp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    temp.replace(path)


def prepare():
    rows = read_rows(DATA)
    known = {r['id'] for r in rows}
    dbpath = HERE / 'collection.sqlite3'
    if not dbpath.exists():
        raise ValueError('Base collection.sqlite3 absente. Lancez d’abord une collecte.')
    with sqlite3.connect(f'{dbpath.as_uri()}?mode=ro',uri=True) as db:
        for raw, rating in db.execute('SELECT payload,rating FROM posts ORDER BY rowid'):
            p=json.loads(raw)
            if p['id'] in known or not p.get('text','').strip():
                continue
            rows.append({'id':p['id'],'author':p.get('author',''), 'published_at':p.get('published_at'),
                         'url':p.get('url',''),'text':p['text'],
                         'llm_suggestion':json.loads(rating) if rating else None,
                         'label':None,'evidence_url':'','note':'','human_reviewed':False})
            known.add(p['id'])
    save_rows(DATA,rows)
    print(f'{len(rows)} publications disponibles dans {DATA}')


def annotate():
    rows=read_rows(DATA)
    if not rows:
        raise ValueError('Lancez prepare avant annotate.')
    print('Catégories :')
    for i,label in enumerate(LABELS,1): print(i,label)
    print('Une information sourcée comporte une référence identifiable ; cela ne prouve pas sa vérité.\n'
          'Une affirmation non étayée n’est pas nécessairement fausse. s = passer, q = quitter.\n'
          'Les suggestions LLM sont masquées pour limiter leur influence.')
    for row in rows:
        if row.get('human_reviewed'): continue
        print('\n@'+row['author']+' | '+str(row.get('published_at'))+'\n'+row['text']+'\n'+row.get('url',''))
        choice=input('Catégorie [1-5/s/q] : ').strip().lower()
        if choice=='q': break
        if choice=='s': continue
        if choice not in ('1','2','3','4','5'):
            print('Choix invalide ; publication laissée à annoter.'); continue
        label=LABELS[int(choice)-1]
        evidence=input('Lien justificatif/source (facultatif, obligatoire pour information sourcée) : ').strip()
        if label=='sourced_information' and not evidence.startswith(('https://','http://')):
            print('Référence requise ; publication laissée à annoter.'); continue
        row.update(label=label,evidence_url=evidence,note=input('Note facultative : ').strip(),human_reviewed=True)
        save_rows(DATA,rows)
    save_rows(DATA,rows)
    print('Annotations enregistrées.')


def fit(rows):
    docs=Counter(); words={label:Counter() for label in LABELS}; vocab=set()
    for row in rows:
        label=row['label']; docs[label]+=1
        ts=tokens(row['text']); words[label].update(ts); vocab.update(ts)
    return {'labels':list(LABELS),'docs':dict(docs),'words':{k:dict(v) for k,v in words.items()},
            'totals':{k:sum(v.values()) for k,v in words.items()},'vocabulary_size':len(vocab),
            'purpose':'Content category baseline; not a truth, credibility or expertise score.'}


def classify(model,text):
    scores={}; frequencies=Counter(tokens(text)); v=max(1,model['vocabulary_size'])
    total=sum(model['docs'].values())
    for label in model['labels']:
        value=math.log((model['docs'].get(label,0)+1)/(total+len(model['labels'])))
        ws=model['words'][label]; denominator=model['totals'][label]+v
        value+=sum(n*math.log((ws.get(w,0)+1)/denominator) for w,n in frequencies.items())
        scores[label]=value
    return max(scores,key=scores.get)


def split_authors(rows):
    # Fixed split by author; exact normalized text cannot cross the boundary.
    train=[]; test=[]
    for row in rows:
        author=row['author'].lower().lstrip('@')
        bucket=int(hashlib.sha256(author.encode()).hexdigest()[:8],16)%5
        (test if bucket==0 else train).append(row)
    train_texts={' '.join(tokens(r['text'])) for r in train}
    test=[r for r in test if ' '.join(tokens(r['text'])) not in train_texts]
    return train,test


def evaluate(model, rows):
    matrix={label:{other:0 for other in LABELS} for label in LABELS}
    for row in rows: matrix[row['label']][classify(model,row['text'])]+=1
    f1s=[]
    for label in LABELS:
        tp=matrix[label][label]; fp=sum(matrix[x][label] for x in LABELS if x!=label)
        fn=sum(matrix[label][x] for x in LABELS if x!=label)
        f1s.append(2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0)
    accuracy=sum(matrix[l][l] for l in LABELS)/len(rows)
    return {'test_size':len(rows),'accuracy':accuracy,'macro_f1':sum(f1s)/len(f1s),'confusion_matrix':matrix}


def train():
    rows=[r for r in read_rows(DATA) if r.get('human_reviewed') is True and r.get('label') in LABELS and r.get('author')]
    # Remove exact duplicates globally before splitting or training.
    unique={}
    for r in rows: unique.setdefault(' '.join(tokens(r['text'])),r)
    rows=list(unique.values())
    counts=Counter(r['label'] for r in rows)
    if len(rows)<100 or any(counts[l]<10 for l in LABELS):
        raise ValueError(f'Pas assez d’annotations : {len(rows)} uniques, {dict(counts)}. Minimum expérimental : 100 et 10 par catégorie ; viser 300–500.')
    training,test=split_authors(rows)
    if any(sum(r['label']==l for r in training)<5 or sum(r['label']==l for r in test)<2 for l in LABELS):
        raise ValueError('Répartition insuffisante entre auteurs : chaque catégorie doit avoir >=5 exemples entraînement et >=2 test. Annoter davantage d’auteurs.')
    provisional=fit(training); report=evaluate(provisional,test)
    majority=Counter(r['label'] for r in training).most_common(1)[0][0]
    report.update(majority_baseline_accuracy=sum(r['label']==majority for r in test)/len(test),
                  training_size=len(training),split='Held-out authors; exact duplicate text removed',
                  limitation='No temporal holdout yet; no production performance guarantee.')
    (HERE/'training_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    # Persist evaluated model; do not silently refit on held-out examples.
    MODEL.write_text(json.dumps(provisional,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('prepare','annotate','train'): sub.add_parser(command)
    prediction=sub.add_parser('predict'); prediction.add_argument('text')
    args=parser.parse_args()
    try:
        if args.command=='prepare': prepare()
        elif args.command=='annotate': annotate()
        elif args.command=='train': train()
        else:
            if not MODEL.exists(): raise ValueError('Aucun modèle entraîné ; annoter puis lancer train.')
            model=json.loads(MODEL.read_text(encoding='utf-8'))
            print(json.dumps({'category':classify(model,args.text),'experimental':True,'verified':False},ensure_ascii=False))
    except ValueError as exc: parser.exit(1,str(exc)+'\n')


if __name__=='__main__': main()
