"""
Link settings, read from the environment.

The link runs on one instance only (the cloud Lute), so it is configured
there rather than in user settings, which are per device and hold no
secrets that should travel.

  LUTE_VOCAB_URL        vocab sync server, e.g. https://vocab.up.railway.app
  LUTE_VOCAB_TOKEN      its external API token (EXT_TOKEN<n> on the server)
  LUTE_VOCAB_LANGUAGES  Lute languages to link, comma-separated; give the
                        vocab name too when it differs: "Spanish,German=de"
  LUTE_VOCAB_DECK       deck for words new to vocab; "{language}" is replaced
                        by the vocab language.  Empty (default) is vocab's
                        Default deck.
  LUTE_VOCAB_INTERVAL   seconds between background runs (default 300);
                        0 turns the background run off.
"""

import os
from dataclasses import dataclass, field
from typing import Dict


class VocabConfigError(Exception):
    "Bad link settings."


def parse_languages(value: str) -> Dict[str, str]:
    "'Spanish,German=de' -> {'Spanish': 'Spanish', 'German': 'de'}."
    ret = {}
    for item in value.split(","):
        item = item.strip()
        if item == "":
            continue
        lute_name, _, vocab_name = item.partition("=")
        lute_name, vocab_name = lute_name.strip(), vocab_name.strip()
        if lute_name == "":
            raise VocabConfigError(f"LUTE_VOCAB_LANGUAGES: no Lute language in {item!r}")
        ret[lute_name] = vocab_name or lute_name
    return ret


@dataclass
class VocabConfig:
    "Settings for the link to a vocab server."

    url: str = ""
    token: str = ""
    # Lute language name -> vocab language.
    languages: Dict[str, str] = field(default_factory=dict)
    deck: str = ""
    interval: int = 300

    @property
    def enabled(self) -> bool:
        "True if there is a server to link to."
        return self.url != "" and self.token != ""

    def deck_for(self, vocab_language: str) -> str:
        "Deck for a new word."
        return self.deck.replace("{language}", vocab_language)

    @staticmethod
    def from_env(env=None) -> "VocabConfig":
        "Read the LUTE_VOCAB_* variables."
        env = os.environ if env is None else env
        try:
            interval = int(env.get("LUTE_VOCAB_INTERVAL", "300"))
        except ValueError as e:
            raise VocabConfigError("LUTE_VOCAB_INTERVAL must be a number of seconds") from e
        return VocabConfig(
            url=env.get("LUTE_VOCAB_URL", "").strip().rstrip("/"),
            token=env.get("LUTE_VOCAB_TOKEN", "").strip(),
            languages=parse_languages(env.get("LUTE_VOCAB_LANGUAGES", "")),
            deck=env.get("LUTE_VOCAB_DECK", "").strip(),
            interval=interval,
        )
