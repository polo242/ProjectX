"""Collect first, classify later. No stealth or restriction bypass."""
import argparse
from collections import Counter
import json
import re
import unicodedata
from pathlib import Path
import time

import agent as a
from collection import Store, age_status, utcnow
from watchlist import load_queries

BITCOIN_QUERY = '(bitcoin OR BTC OR #bitcoin OR #BTC OR $BTC OR ₿)'


def mentions_bitcoin(text):
    text = unicodedata.normalize('NFKC', text or '')
    return bool(re.search(r'(?<![A-Za-z0-9_])(?:bitcoin|btc)(?![A-Za-z0-9_])|₿', text, re.I))


def bitcoin_queries():
    # Broad multilingual discovery first; split terms only after exhausting it.
    base = [BITCOIN_QUERY, 'bitcoin', '(BTC OR #BTC OR $BTC)', '₿']
    # Each live search stops after a limited depth: narrower topics reach other posts.
    topics = ['price', 'ETF', 'mining', 'lightning', 'whale', 'halving', 'MicroStrategy', 'wallet', 'regulation']
    return base + [f'bitcoin {t}' for t in topics]


def prioritized_bitcoin_queries(path):
    # Keep replies for Bitcoin collection, including posts without hashtags.
    priority = [q.removesuffix(' -filter:replies') + ' ' + BITCOIN_QUERY
                for q in load_queries(path)]
    return priority + bitcoin_queries(), len(priority)


class HistoryProgress:
    """Duplicates in SQLite do not mean the historical feed is exhausted."""
    def __init__(self):
        self.previous = None
        self.stalled = 0

    def update(self, metrics, encountered):
        current = (metrics['y'], metrics['height'])
        moved = self.previous is None or current != self.previous
        bottom = metrics['y'] + metrics['viewport'] >= metrics['height'] - 20
        if encountered or moved or not bottom:
            self.stalled = 0
        elif not metrics.get('loading', False):
            self.stalled += 1
        self.previous = current
        return self.stalled >= 6


PAGE_PROGRESS_JS = """return {
 y: window.scrollY, height: document.documentElement.scrollHeight,
 viewport: window.innerHeight,
 loading: !!document.querySelector('[role="progressbar"]')
};"""

BUFFER_JS = '''
if (!window.__cryptoCapture) {
  const cache = new Map();
  const extract = new Function(arguments[0]);
  const capture = () => {
    for (const p of extract().posts) {
      if (!cache.has(p.id) || p.text.length > cache.get(p.id).text.length) cache.set(p.id, p);
    }
    while (cache.size > 3000) cache.delete(cache.keys().next().value);
  };
  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    queueMicrotask(() => { scheduled = false; capture(); });
  });
  observer.observe(document.body, {childList:true, subtree:true});
  window.__cryptoCapture = {cache, observer, capture, sent: new Set()};
}
const c = window.__cryptoCapture;
c.capture();
const fresh = [];
for (const [id, p] of c.cache) if (!c.sent.has(id)) { c.sent.add(id); fresh.push(p); }
return fresh;
'''


def capture(driver, store, target, seen, counts, topic="all", check_block=True):
    if check_block:
        obs = driver.execute_script(a.OBSERVE_JS)
        if '/login' in obs['url'] or '/i/flow' in obs['url'] or any(x in obs['body'].lower() for x in a.BLOCK_MARKERS):
            raise a.NeedsHuman('Connexion ou intervention requise : '+obs['url'])
    posts = driver.execute_script(BUFFER_JS, a.OBSERVE_JS)
    inserted = 0
    # One transaction per observation instead of one disk commit per post.
    with store.db:
        for p in posts:
            if p['id'] in seen: continue
            seen.add(p['id']); counts['observed_unique'] += 1
            status = age_status(p)
            if status == 'recent': counts['recent_unique'] += 1
            p['collected_at'] = utcnow().isoformat()
            p['url'] = f"https://x.com/{p['author']}/status/{p['id']}"
            if topic == 'bitcoin' and not mentions_bitcoin(p.get('text','')):
                counts['off_topic'] += 1
                continue
            if status == 'recent':
                status = 'pending' if p.get('text','').strip() else 'empty_text'
            cursor = store.db.execute('INSERT OR IGNORE INTO posts(id,payload,target,status) VALUES(?,?,?,?)',
                (p['id'],json.dumps(p,ensure_ascii=False),target,status))
            if cursor.rowcount:
                counts[status] += 1
                if status == 'pending': inserted += 1
            else:
                counts['already_stored'] += 1
    return inserted


def export_raw(store, path, topic="all"):
    temp=path.with_suffix('.tmp')
    n=0
    with temp.open('w',encoding='utf-8') as f:
        for raw,target in store.db.execute("SELECT payload,target FROM posts ORDER BY json_extract(payload, '$.published_at') DESC"):
            p=json.loads(raw)
            if age_status(p)=='recent' and (topic != 'bitcoin' or mentions_bitcoin(p.get('text',''))):
                f.write(json.dumps({**p,'cible':target},ensure_ascii=False)+'\n'); n+=1
    temp.replace(path)
    return n


class Timers:
    """Wall-clock seconds per stage, to find the real bottleneck."""
    def __init__(self):
        self.total = Counter()

    def measure(self, name):
        timers = self
        class _Ctx:
            def __enter__(self): self.t = time.monotonic()
            def __exit__(self, *exc): timers.total[name] += time.monotonic() - self.t
        return _Ctx()


def run(argv=None):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.common.exceptions import WebDriverException
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--topic', choices=('bitcoin','all'), default='bitcoin')
    ap.add_argument('--minutes',type=float,default=5)
    ap.add_argument('--target-posts',type=int,default=500)
    ap.add_argument('--max-navigations',type=int,default=25)
    ap.add_argument('--priority-share',type=float,default=0.25,help='Part de la durée réservée aux comptes prioritaires (0 à 0.9)')
    ap.add_argument('--no-prompt',action='store_true',help='Pas de pause clavier : attend 15 s au départ, ferme Firefox à la fin')
    ap.add_argument('--max-scroll',type=float,default=1.0,help='Pas de défilement maximal, en hauteurs de fenêtre (0.8 à 3)')
    args=ap.parse_args(argv)
    if args.minutes<=0 or args.target_posts<1 or args.max_navigations<1: ap.error('Budgets positifs requis')
    if not 0<=args.priority_share<=0.9: ap.error('--priority-share doit être entre 0 et 0.9')
    if not 0.8<=args.max_scroll<=3: ap.error('--max-scroll doit être entre 0.8 et 3')
    store=Store(a.HERE/'collection.sqlite3')
    counts=Counter(); seen=set(); navs=0; driver=None; timers=Timers()
    started=None; reason='duration'
    if args.topic == 'bitcoin':
        queries, priority_count = prioritized_bitcoin_queries(a.HERE/'sources.json')
    else:
        priority_count = 0
        priority=load_queries(a.HERE/'sources.json')
        queries=[]
        for i in range(max(len(priority),len(a.SEEDS))):
            if i<len(priority): queries.append(priority[i])
            if i<len(a.SEEDS): queries.append(a.SEEDS[i])
    try:
        opts=Options(); opts.add_argument('-profile'); opts.add_argument(str(a.HERE/'profile'))
        driver=webdriver.Firefox(options=opts); driver.set_page_load_timeout(20)
        driver.install_addon(str(a.HERE/'zeeschuimer.xpi'))
        driver.get('https://x.com/home')
        if args.no_prompt: time.sleep(15)  # laisse charger X et l'extension
        else: input('Vérifiez X et Zeeschuimer. Entrée pour collecter sans LLM... ')
        started=time.monotonic(); end=started+args.minutes*60
        for query_index, query in enumerate(queries[:args.max_navigations]):
            if time.monotonic()>=end: break
            if counts['pending']>=args.target_posts: reason='target_reached'; break
            priority_phase = query_index < priority_count
            # Reserve half the session for general discovery; share priority time across groups.
            query_end = min(end, started + args.minutes*60*args.priority_share*(query_index+1)/priority_count) if priority_phase else end
            if time.monotonic() >= query_end:
                counts['priority_groups_skipped_time'] += int(priority_phase)
                continue
            target=a.url_for('search',{'query':query,'mode':'live'})
            with timers.measure('navigation'): driver.get(target)
            navs+=1
            counts['priority_searches' if priority_phase else 'general_searches'] += 1
            steps=0; factor=0.8
            progress=HistoryProgress()
            while time.monotonic()<query_end:
                before_observed = counts['observed_unique']
                with timers.measure('capture'):
                    new=capture(driver,store,target,seen,counts,topic=args.topic,check_block=steps%5==0)
                counts['new_from_priority_searches' if priority_phase else 'new_from_general_searches'] += new
                encountered = counts['observed_unique'] - before_observed
                with timers.measure('metrics'): metrics = driver.execute_script(PAGE_PROGRESS_JS)
                exhausted = progress.update(metrics, encountered)
                print(f"[collecte] {counts['pending']}/{args.target_posts} nouveaux posts récents enregistrés ; {navs} recherches",flush=True)
                if counts['pending']>=args.target_posts:
                    reason='target_reached'; break
                # Keep descending through already-stored posts; stop only at a stalled bottom.
                if exhausted:
                    print('[collecte] Bas des résultats stable ; passage à la recherche suivante.')
                    break
                with timers.measure('scroll_wait'):
                    # Bigger step while content keeps arriving; back to 0.8 viewport after a dry step.
                    driver.execute_script('window.scrollBy(0, Math.floor(window.innerHeight * arguments[0]));', factor)
                    old_len=driver.execute_script('return window.__cryptoCapture.cache.size')
                    deadline=min(query_end,time.monotonic()+2.5)
                    grew=False
                    while time.monotonic()<deadline:
                        time.sleep(min(0.1,max(0,deadline-time.monotonic())))
                        if driver.execute_script('return window.__cryptoCapture.cache.size')>old_len:
                            grew=True; break
                    factor = min(args.max_scroll, factor+0.1) if grew else 0.8
                steps+=1
            if reason=='target_reached': break
        else:
            reason='searches_exhausted'
    except a.NeedsHuman as exc:
        reason='needs_human'; print('[!] Arrêt :',exc)
    except KeyboardInterrupt:
        reason='interrupted'
    except WebDriverException as exc:
        reason='browser_error'; print('[!] Erreur navigateur :',type(exc).__name__,(exc.msg or '')[:300])
    finally:
        try:
            output = a.HERE / ('bitcoin_24h.jsonl' if args.topic == 'bitcoin' else 'collected_24h.jsonl')
            exported=export_raw(store,output,topic=args.topic)
            elapsed=time.monotonic()-started if started else 0
            summary={'at':utcnow().isoformat(),'mode':'capture_only','topic':args.topic,'output':str(output),'reason':reason,'seconds':round(elapsed,2),
                     'target_posts':args.target_posts,'max_scroll':args.max_scroll,'priority_share':args.priority_share,'target_reached':counts['pending']>=args.target_posts,
                     'new_posts_per_minute':round(counts['pending']/(elapsed/60),1) if elapsed else 0,
                     'counts':dict(counts),'timing_seconds':{k:round(v,1) for k,v in timers.total.items()},'navigations':navs,'exported_recent_total':exported,'llm_calls':0}
            if started: (a.HERE/'last_capture_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(summary,ensure_ascii=False,indent=2))
        finally:
            store.close()
            if driver:
                try:
                    if not args.no_prompt: input('Exportez Zeeschuimer si nécessaire. Entrée pour fermer... ')
                finally: driver.quit()


if __name__=='__main__': run()
