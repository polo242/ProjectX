"""Client OpenRouter minimal (gratuit uniquement) qui renvoie une action JSON."""
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"


class LLMError(Exception):
    pass


class NoAnswer(LLMError):
    """Le modèle n'a pas produit de JSON exploitable (récupérable, contrairement à une erreur HTTP)."""


def check_model(model):
    """Refuse tout modèle non gratuit et vérifie qu'il est au catalogue."""
    if not model.endswith(":free"):
        raise LLMError(f"Modèle non gratuit refusé : {model}")
    try:
        with urllib.request.urlopen(f"{BASE}/models", timeout=30) as r:
            ids = {m["id"] for m in json.load(r)["data"]}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        raise LLMError(f"Catalogue OpenRouter inaccessible : {exc}")
    if model not in ids:
        raise LLMError(f"Modèle absent du catalogue OpenRouter : {model}")


def _post(payload, key):
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def ask(model, system, user, retries=3, max_tokens=1500):
    """Retourne le dict JSON de la réponse. Backoff sur 429/5xx, réparation bornée du JSON."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise LLMError("Variable d'environnement OPENROUTER_API_KEY absente")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(retries):
        try:
            data = _post({"model": model, "messages": messages,
                          "temperature": 0.3, "max_tokens": max_tokens}, key)
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                return json.loads(m.group(0))
            print(f"[llm] réponse sans JSON (finish={choice.get('finish_reason')}) : {text[:150]!r}")
            raise ValueError("pas de JSON")
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                try:
                    wait = int(e.headers.get("Retry-After") or 15 * (attempt + 1))
                except ValueError:  # Retry-After may be an HTTP date
                    wait = 15 * (attempt + 1)
                print(f"[llm] HTTP {e.code}, pause {wait}s")
                time.sleep(min(wait, 120))
            else:
                raise LLMError(f"HTTP {e.code}: {e.read()[:200]!r}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            wait = 5 * (attempt + 1)
            print(f"[llm] réseau indisponible ({e}), pause {wait}s")
            time.sleep(wait)
        except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
            messages.append({"role": "user",
                             "content": "Réponse invalide. Réponds uniquement par un objet JSON."})
    raise NoAnswer("Pas de réponse exploitable après plusieurs tentatives")
