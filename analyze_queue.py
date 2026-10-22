"""Classify the persistent queue without opening Firefox."""
import argparse
import os
import agent as a
from collection import Store,age_status
from alerts import AlertQueue
import llm


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--max-llm-calls',type=int,default=20)
    ap.add_argument('--batch-size',type=int,default=12)
    ap.add_argument('--discord',action='store_true')
    args=ap.parse_args()
    if args.max_llm_calls<1 or not 1<=args.batch_size<=20: ap.error('Budgets invalides')
    if not os.environ.get('OPENROUTER_API_KEY'): ap.error('OPENROUTER_API_KEY absente')
    if args.discord and not os.environ.get('DISCORD_WEBHOOK_URL'): ap.error('DISCORD_WEBHOOK_URL absente')
    try: llm.check_model(llm.DEFAULT_MODEL)
    except llm.LLMError as exc: ap.error(str(exc))
    store=Store(a.HERE/'collection.sqlite3'); alerts=AlertQueue(store.db)
    calls=0
    try:
        while calls<args.max_llm_calls:
            batch=store.pending(args.batch_size)
            if not batch: break
            todo=[]
            for p,t in batch:
                if age_status(p)!='recent': store.expire(p['id'])
                else: todo.append(p)
            if not todo: continue
            calls+=1
            try: ratings=a.score_batch(llm.DEFAULT_MODEL,a.GOAL,todo)
            except llm.NoAnswer:
                store.fail([p['id'] for p in todo])
                print('Notation indisponible ; lot conservé pour reprise.'); continue
            for p in todo:
                r=ratings[p['id']]
                selected=r['crypto']>=3 and r['quality']>=3 and r['category'] in {'news','analysis','education'} and age_status(p)=='recent'
                alert=selected and r['quality']>=4 and r['category'] in {'news','analysis'}
                store.finish(p['id'],r,selected,alerts if alert else None,p)
            store.export(a.RELEVANT)
            alerts.deliver(a.HERE/'alerts_preview.jsonl',send=args.discord)
            print(f'[analyse] {len(todo)} posts ; {calls}/{args.max_llm_calls} opérations LLM')
    finally:
        store.export(a.RELEVANT)
        alerts.deliver(a.HERE/'alerts_preview.jsonl',send=args.discord)  # drain the outbox even with nothing to rate
        store.close()


if __name__=='__main__': main()
