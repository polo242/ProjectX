"""Build bounded priority search groups from user-provided accounts."""
import json
import re


def load_queries(path, group_size=5):
    data = json.loads(path.read_text(encoding='utf-8'))
    handles = []
    for account in data['accounts']:
        h = account['handle']
        if not re.fullmatch(r'[A-Za-z0-9_]{1,15}', h):
            raise ValueError('Pseudo invalide dans sources.json')
        if account.get('enabled', True) and h.lower() not in [x.lower() for x in handles]:
            handles.append(h)
    return ['(' + ' OR '.join('from:' + h for h in handles[i:i+group_size]) + ') -filter:replies'
            for i in range(0, len(handles), group_size)]
