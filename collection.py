"""Persistent collection, bounded classification batches and navigation scheduling."""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time

import llm

MAX_ATTEMPTS = 3
CATEGORIES = {'news', 'analysis', 'education', 'promotion', 'humor', 'other'}


def utcnow():
    return datetime.now(timezone.utc)


def age_status(post, now=None):
    now = now or utcnow()
    try:
        stamp = datetime.fromisoformat(post.get('published_at', '').replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            return 'invalid_date'
    except (ValueError, TypeError, AttributeError):
        return 'invalid_date'
    delta = now - stamp
    if delta < timedelta(0):
        return 'future_date'
    return 'recent' if delta < timedelta(hours=24) else 'old'


def validate_ratings(posts, reply):
    rows = reply.get('ratings') if isinstance(reply, dict) else None
    if not isinstance(rows, list):
        raise llm.NoAnswer('Notation absente ou invalide')
    expected = {p['id'] for p in posts}
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('id') not in expected or row['id'] in result:
            raise llm.NoAnswer('Identifiant de notation invalide ou dupliqué')
        for key in ('crypto', 'quality'):
            if type(row.get(key)) is not int or not 0 <= row[key] <= 5:
                raise llm.NoAnswer('Score invalide')
        if row.get('category') not in CATEGORIES or not isinstance(row.get('reason'), str):
            raise llm.NoAnswer('Catégorie ou justification invalide')
        result[row['id']] = {k: row[k] for k in ('crypto', 'quality', 'category', 'reason')}
        result[row['id']]['reason'] = row['reason'][:300]
    if set(result) != expected:
        raise llm.NoAnswer('Notation incomplète : lot conservé pour reprise')
    return result


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, target TEXT NOT NULL,
            status TEXT NOT NULL, rating TEXT, selected INTEGER NOT NULL DEFAULT 0)''')
        if 'attempts' not in {r[1] for r in self.db.execute('PRAGMA table_info(posts)')}:
            self.db.execute('ALTER TABLE posts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0')
        self.db.commit()

    def add(self, post, target, status):
        cur = self.db.execute('INSERT OR IGNORE INTO posts(id,payload,target,status) VALUES(?,?,?,?)',
                              (post['id'], json.dumps(post, ensure_ascii=False), target, status))
        if cur.rowcount == 0 and status == 'pending':
            # A post first seen without text may reappear complete: let it be rated, never overwrite rated ones.
            cur = self.db.execute("UPDATE posts SET payload=?,status='pending' WHERE id=? AND status='empty_text'",
                                  (json.dumps(post, ensure_ascii=False), post['id']))
        self.db.commit()
        return cur.rowcount == 1

    def pending(self, limit=12):
        # Posts whose rating failed MAX_ATTEMPTS times are set aside so they cannot starve the queue.
        rows = self.db.execute("SELECT payload,target FROM posts WHERE status='pending' AND attempts<? "
                               "ORDER BY attempts,rowid LIMIT ?", (MAX_ATTEMPTS, limit))
        return [(json.loads(p), t) for p, t in rows]

    def fail(self, post_ids):
        self.db.executemany("UPDATE posts SET attempts=attempts+1 WHERE id=?", [(i,) for i in post_ids])
        self.db.commit()

    def finish(self, post_id, rating, selected, alerts=None, post=None):
        """Mark a post rated and, when given, enqueue its alert in the same transaction."""
        self.db.execute("UPDATE posts SET status='rated',rating=?,selected=? WHERE id=?",
                        (json.dumps(rating, ensure_ascii=False), int(selected), post_id))
        if alerts is not None and post is not None:
            alerts.enqueue(post, rating, commit=False)
        self.db.commit()

    def expire(self, post_id):
        self.db.execute("UPDATE posts SET status='old',selected=0 WHERE id=?", (post_id,))
        self.db.commit()

    def export(self, path):
        # Rebuild atomically: no duplicated output after an interrupted write.
        temp = Path(str(path) + '.tmp')
        with temp.open('w', encoding='utf-8') as f:
            for payload, target, rating in self.db.execute('SELECT payload,target,rating FROM posts WHERE selected=1 ORDER BY rowid'):
                post = json.loads(payload)
                if age_status(post) != 'recent':
                    continue
                r = json.loads(rating)
                f.write(json.dumps({**post, **r, 'score': r['quality'], 'cible': target}, ensure_ascii=False) + '\n')
        temp.replace(path)

    def yields(self):
        return {target: {'rated': n, 'selected': kept} for target, n, kept in self.db.execute(
            "SELECT target,COUNT(*),SUM(selected) FROM posts WHERE status='rated' GROUP BY target")}

    def close(self):
        self.db.close()


def run():
    import agent as a
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
    from selenium.webdriver.support.ui import WebDriverWait

    ap = argparse.ArgumentParser()
    ap.add_argument('--discord', action='store_true', help='Envoyer les nouvelles alertes via DISCORD_WEBHOOK_URL')
    ap.add_argument('--goal', default=a.GOAL)
    ap.add_argument('--minutes', type=float, default=15)
    ap.add_argument('--model', default=llm.DEFAULT_MODEL)
    ap.add_argument('--max-llm-calls', type=int, default=60)
    ap.add_argument('--max-navigations', type=int, default=25)
    ap.add_argument('--start-url', default='https://x.com/home')
    ap.add_argument('--batch-size', type=int, default=12)
    ap.add_argument('--decision-every', type=int, default=4)
    args = ap.parse_args()
    if args.minutes <= 0 or not 1 <= args.batch_size <= 20 or args.decision_every < 1 or args.max_llm_calls < 1 or args.max_navigations < 1:
        ap.error('Budgets positifs requis ; batch-size entre 1 et 20')
    if not __import__('os').environ.get('OPENROUTER_API_KEY'):
        ap.error('Variable OPENROUTER_API_KEY absente')
    from watchlist import load_queries
    from alerts import AlertQueue
    priority_queries = load_queries(a.HERE / 'sources.json')
    seeds = priority_queries + a.SEEDS
    if args.discord and not __import__('os').environ.get('DISCORD_WEBHOOK_URL'):
        ap.error('DISCORD_WEBHOOK_URL absente ; ne partagez pas ce secret dans le chat')
    try:
        a.llm.check_model(args.model)
    except llm.LLMError as exc:
        ap.error(str(exc))
    database_path = a.HERE / 'collection.sqlite3'
    if not database_path.exists() and a.RELEVANT.exists():
        import shutil
        backup = a.RELEVANT.with_name('relevant_24h_before_database_' + utcnow().strftime('%Y%m%dT%H%M%S%f') + '.jsonl')
        shutil.copy2(a.RELEVANT, backup)
        print('Ancien export conservé :', backup)
    store = Store(database_path)
    alerts = AlertQueue(store.db)
    counts = Counter()
    session_seen = set()
    empty_ids = set()  # seen without text: re-examined if the text shows up later
    spam_samples = []  # a few rejected posts shown in the summary to spot false positives
    calls = navs = steps = empty = failures = 0
    current = 'home'
    visited, history = [], []
    driver = None
    end = float('inf')

    def remaining():
        return max(0, end - time.monotonic())

    def flush(force=False):
        nonlocal calls
        while remaining() > 0 and calls < args.max_llm_calls:
            batch = store.pending(args.batch_size)
            if not batch or (not force and len(batch) < args.batch_size):
                return
            todo = []
            for p, target in batch:
                if age_status(p) != 'recent':
                    store.expire(p['id'])
                    counts['expired_before_rating'] += 1
                else:
                    todo.append(p)
            if not todo:
                continue
            calls += 1
            try:
                ratings = a.score_batch(args.model, args.goal, todo)
            except llm.NoAnswer as exc:
                counts['rating_failures'] += 1
                a.audit(event='rating_failed', error=str(exc), ids=[p['id'] for p in todo])
                store.fail([p['id'] for p in todo])
                continue  # Posts stay pending (retried up to MAX_ATTEMPTS, then set aside).
            kept = 0
            for post in todo:
                r = ratings[post['id']]
                selected = (r['crypto'] >= 3 and r['quality'] >= 3 and
                            r['category'] in {'news', 'analysis', 'education'} and age_status(post) == 'recent')
                alert = selected and r['quality'] >= 4 and r['category'] in {'news', 'analysis'}
                store.finish(post['id'], r, selected, alerts if alert else None, post)
                counts['rated'] += 1
                counts['selected' if selected else 'rejected_rating'] += 1
                counts['category_' + r['category']] += 1
                kept += selected
            store.export(a.RELEVANT)
            alerts.deliver(a.HERE / 'alerts_preview.jsonl', send=args.discord)
            print(f'[score] {kept}/{len(todo)} retenus ; {calls}/{args.max_llm_calls} appels LLM')

    def observe():
        nonlocal empty
        obs = driver.execute_script(a.OBSERVE_JS)
        low = obs['body'].lower()
        if '/login' in obs['url'] or '/i/flow' in obs['url'] or any(b in low for b in a.BLOCK_MARKERS):
            raise a.NeedsHuman('Connexion ou intervention requise : ' + obs['url'])
        recent = []
        new = 0
        for post in obs['posts']:
            status = age_status(post)
            if status == 'recent':
                recent.append(post)
            if post['id'] in session_seen and not (post['id'] in empty_ids and post['text'].strip()):
                continue
            session_seen.add(post['id'])
            empty_ids.discard(post['id'])
            if not post['text'].strip():
                empty_ids.add(post['id'])
            post['url'] = f"https://x.com/{post['author']}/status/{post['id']}"
            post['collected_at'] = utcnow().isoformat()
            counts['observed_unique'] += 1
            if status == 'recent':
                if not post['text'].strip():
                    status = 'empty_text'
                elif a.SPAM.search(post['text']):
                    status = 'spam'
                    if len(spam_samples) < 3:
                        spam_samples.append({'url': post['url'], 'motif': a.SPAM.search(post['text']).group(0),
                                             'texte': post['text'][:140]})
                else:
                    status = 'pending'
            if not store.add(post, current, status):
                counts['already_seen_previous_session'] += 1
                continue
            counts[status] += 1
            new += status == 'pending'
        empty = 0 if new else empty + 1
        obs['posts'] = recent
        obs['new'] = new
        return obs

    def navigate(action, values):
        nonlocal navs, current, steps, empty
        if navs >= args.max_navigations or remaining() <= 0:
            return False
        target = a.url_for(action, values)
        if target in visited:
            return False
        driver.get(target)
        WebDriverWait(driver, min(15, max(1, remaining()))).until(
            lambda d: d.execute_script('return document.readyState') == 'complete')
        visited.append(target)
        current = action + ':' + next(iter(values.values()))
        navs += 1
        steps = empty = 0
        a.audit(event='navigation', action=action, args=values, url=target)
        return True

    try:
        opts = Options()
        opts.add_argument('-profile')
        opts.add_argument(str(a.HERE / 'profile'))
        driver = webdriver.Firefox(options=opts)
        driver.set_page_load_timeout(30)
        driver.install_addon(str(a.HERE / 'zeeschuimer.xpi'))
        driver.get(args.start_url)
        input('Vérifiez la connexion X et la capture Zeeschuimer, puis Entrée... ')
        end = time.monotonic() + args.minutes * 60
        # Seed rotation follows coverage, not browser-fingerprint evasion.
        navigate('search', {'query': seeds[0], 'mode': 'live'})
        obs = observe()
        while remaining() > 0:
            flush()
            if calls >= args.max_llm_calls:
                print('Budget LLM épuisé ; les lots en attente sont conservés.')
                break
            if steps >= args.decision_every or empty >= 3:
                flush(force=True)
                if calls >= args.max_llm_calls or remaining() <= 0:
                    break
                payload = json.dumps({'objectif': args.goal, 'url': obs['url'],
                    'posts_visibles': [{**p, 'text': p['text'][:600]} for p in obs['posts'][:8]],
                    'rendement_par_cible': store.yields(), 'cibles_deja_visitees': visited[-20:],
                    'seeds': seeds, 'observations_sans_nouveau_post_recent': empty,
                    'minutes_restantes': round(remaining()/60, 1)}, ensure_ascii=False)
                calls += 1
                try:
                    reply = llm.ask(args.model, a.SYSTEM, payload)
                    action, values = a.validate(reply.get('action'), reply.get('args') or {})
                    failures = 0
                except (llm.NoAnswer, ValueError, TypeError, AttributeError) as exc:
                    failures += 1
                    a.audit(event='decision_failed', error=str(exc))
                    if failures >= 3:
                        break
                    action, values = 'scroll', {'viewports': 1}
                    reply = {}
                if action == 'stop':
                    break
                if empty >= 3 or any(a.url_for('search', {'query': q, 'mode': 'live'}) not in visited for q in priority_queries):
                    seed = next((q for q in seeds if a.url_for('search', {'query': q, 'mode': 'live'}) not in visited), None)
                    if seed:
                        action, values = 'search', {'query': seed, 'mode': 'live'}
                a.audit(event='decision', action=action, args=values, why=reply.get('why', ''))
                print(f'[agent] {action} {values}')
                if action in ('search', 'open_profile', 'open_hashtag'):
                    if navs >= args.max_navigations:
                        break
                    if navigate(action, values):
                        obs = observe()
                        continue
                if action == 'wait':
                    time.sleep(min(values['seconds'], remaining()))
                    obs = observe()
                    continue
                steps = 0
            # One viewport at a time, observe immediately after every step.
            driver.execute_script('window.scrollBy(0, Math.floor(window.innerHeight * 0.8));')
            time.sleep(min(3, remaining()))
            obs = observe()
            steps += 1
        flush(force=True)
    except a.NeedsHuman as exc:
        print('[!] Intervention humaine requise :', exc)
        a.audit(event='needs_human', error=str(exc))
    except (llm.LLMError, KeyboardInterrupt) as exc:
        print('[!] Arrêt :', exc)
        a.audit(event='stopped', error=str(exc))
    finally:
        try:
            store.export(a.RELEVANT)
            alerts.deliver(a.HERE / 'alerts_preview.jsonl', send=args.discord)
            summary = {'at': utcnow().isoformat(), 'counts': dict(counts), 'spam_examples': spam_samples,
                       'llm_calls': calls, 'navigations': navs, 'pending': store.db.execute(
                           "SELECT COUNT(*) FROM posts WHERE status='pending'").fetchone()[0],
                       'yield_by_target': store.yields()}
            (a.HERE / 'last_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            a.audit(event='summary', **summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        finally:
            store.close()
            if driver:
                try:
                    input('Exportez Zeeschuimer avant de fermer. Entrée pour fermer Firefox... ')
                finally:
                    driver.quit()
