"""Persistent Discord outbox. Preview by default; no network without --discord."""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone


class AlertQueue:
    def __init__(self, db):
        self.db = db
        db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE, payload TEXT, status TEXT DEFAULT "pending", retry_at REAL DEFAULT 0)')
        db.commit()

    def enqueue(self, post, rating, commit=True):
        normalized = re.sub(r'https?://\S+', '', post['text'].lower())
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
        payload = {'source': post['author'], 'published_at': post['published_at'],
                   'url': post['url'], 'text': post['text'][:1200],
                   'category': rating['category'], 'quality': rating['quality'],
                   'reason': rating['reason'], 'verification': 'Information rapportée sur X, non vérifiée indépendamment.'}
        self.db.execute('INSERT OR IGNORE INTO alerts(id,fingerprint,payload) VALUES(?,?,?)',
                        (post['id'], fingerprint, json.dumps(payload,ensure_ascii=False)))
        if commit:
            self.db.commit()

    def deliver(self, preview_path, send=False):
        rows = list(self.db.execute('SELECT id,payload FROM alerts WHERE status="pending" AND retry_at<=? ORDER BY rowid', (time.time(),)))
        temp = preview_path.with_suffix('.tmp')
        with temp.open('w',encoding='utf-8') as f:
            for ident, raw in rows:
                f.write(raw+'\n')
        temp.replace(preview_path)
        if not send:
            return
        url = os.environ.get('DISCORD_WEBHOOK_URL', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'discord.com' or not re.fullmatch(r'/api/webhooks/\d+/[A-Za-z0-9_.-]+', parsed.path) or parsed.query or parsed.fragment:
            raise ValueError('DISCORD_WEBHOOK_URL invalide')
        sent = 0
        for ident, raw in rows:
            if sent >= 3:
                break
            p = json.loads(raw)
            try:
                stamp = datetime.fromisoformat(p['published_at'].replace('Z','+00:00'))
                expired = (datetime.now(timezone.utc)-stamp).total_seconds() >= 86400
            except (ValueError, AttributeError, TypeError):
                expired = True
            if expired:  # Expired alerts do not count against the per-call send limit.
                self.db.execute('UPDATE alerts SET status="expired" WHERE id=?',(ident,)); self.db.commit()
                continue
            content = (f"Veille crypto — @{p['source']} | {p['category']} | qualité {p['quality']}/5\n"
                       f"Publié : {p['published_at']}\n{p['text']}\n\nSélection : {p['reason']}\n"
                       f"{p['verification']}\n{p['url']}")[:1990]
            req = urllib.request.Request(url+'?wait=true', data=json.dumps(
                {'content':content,'allowed_mentions':{'parse':[]}}).encode(),
                headers={'Content-Type':'application/json','User-Agent':'CryptoWatch/1.0'},method='POST')
            try:
                with urllib.request.urlopen(req,timeout=15) as response:
                    response.read()
            except urllib.error.HTTPError as exc:
                self.db.execute('UPDATE alerts SET retry_at=? WHERE id=?',(time.time()+60,ident)); self.db.commit()
                print('[discord] Envoi reporté, HTTP',exc.code)
                break
            except (urllib.error.URLError, TimeoutError):
                self.db.execute('UPDATE alerts SET retry_at=? WHERE id=?',(time.time()+60,ident)); self.db.commit()
                print('[discord] Réseau indisponible ; alerte conservée.')
                break
            else:
                self.db.execute('UPDATE alerts SET status="sent" WHERE id=?',(ident,)); self.db.commit()
                sent += 1
