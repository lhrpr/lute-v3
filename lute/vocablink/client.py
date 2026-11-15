"""
HTTP client for the vocab server's external API (/v1/ext).
"""

from typing import Any, Dict, List

import requests

# Version of the request format the server expects.
EXT_VERSION = 2

# Words per request: keeps each request well under the server's body limit
# and each transaction short.
BATCH_SIZE = 500


class VocabError(Exception):
    "The vocab server couldn't be reached or refused a request."


class VocabClient:
    "Talks to one vocab server with one token."

    def __init__(self, url, token, timeout=60):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _call(self, method, path, **kwargs):
        try:
            res = self.session.request(
                method, f"{self.url}{path}", timeout=self.timeout, **kwargs
            )
        except requests.RequestException as e:
            raise VocabError(f"can't reach the vocab server: {e}") from e
        if res.status_code == 401:
            raise VocabError("the vocab server refused the token (LUTE_VOCAB_TOKEN)")
        if res.status_code != 200:
            raise VocabError(f"vocab server: {res.status_code} {res.text.strip()}")
        return res.json()

    def lookup(self, keys: List[Dict[str, str]]) -> List[Any]:
        "The word for each {language, term}, or None, in order."
        ret = []
        for i in range(0, len(keys), BATCH_SIZE):
            batch = keys[i : i + BATCH_SIZE]
            ret.extend(self._call("POST", "/v1/ext/lookup", json={"words": batch})["words"])
        return ret

    def upsert(self, words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        "Create or update words; one result per word, in order."
        ret = []
        for i in range(0, len(words), BATCH_SIZE):
            body = {"version": EXT_VERSION, "words": words[i : i + BATCH_SIZE]}
            ret.extend(self._call("POST", "/v1/ext/words", json=body)["results"])
        return ret

    def changes(self, since: int) -> Dict[str, Any]:
        "Words changed or deleted since a server sequence number."
        return self._call("GET", "/v1/ext/words", params={"since": since})
