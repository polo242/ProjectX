"""Code retiré de agent.py (inutilisé depuis que collection.run pilote la session).
Archivé tel quel, non importé. Remplaçants : collection.age_status (fraîcheur) et
collection.run.observe (observation). Imports retirés avec lui : random, time, selenium webdriver/Options.
"""
from datetime import datetime, timedelta, timezone
import agent as a  # BLOCK_MARKERS, NeedsHuman, OBSERVE_JS


def is_recent(post, now=None):
    """Fail closed: missing, invalid, future or >=24h timestamps are rejected."""
    now = now or datetime.now(timezone.utc)
    try:
        published = datetime.fromisoformat(post.get("published_at", "").replace("Z", "+00:00"))
        if published.tzinfo is None:
            return False
        return timedelta(0) <= now - published <= timedelta(hours=24) and now - published != timedelta(hours=24)
    except (ValueError, TypeError, AttributeError):
        return False


def observe(driver, history, seen_ids):
    obs = driver.execute_script(a.OBSERVE_JS)
    low = obs["body"].lower()
    if "/login" in obs["url"] or "/i/flow" in obs["url"] or any(b in low for b in a.BLOCK_MARKERS):
        raise a.NeedsHuman(f"Blocage ou connexion requise sur {obs['url']}")
    all_posts = obs["posts"]
    obs["posts"] = [p for p in all_posts if is_recent(p)]
    obs["excluded_age"] = len(all_posts) - len(obs["posts"])
    new = [p for p in obs["posts"] if p["id"] not in seen_ids]
    seen_ids.update(p["id"] for p in obs["posts"])
    return obs, new
