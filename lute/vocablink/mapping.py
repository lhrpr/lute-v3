"""
How Lute terms map to vocab words, and how the two are reconciled.

Kept free of the database so the rules can be tested on their own.  Every
field that crosses the link is listed in FIELDS; a field type that changes on
either side is one entry here.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Lute's WoStatus -> vocab status.  Lute's 0 ("unknown") is vocab's unrated.
STATUS_TO_VOCAB = {0: None, 1: "1", 2: "2", 3: "3", 4: "4", 5: "5", 98: "I", 99: "K"}
STATUS_FROM_VOCAB = {v: k for k, v in STATUS_TO_VOCAB.items()}

# Statuses that make a term "being learned", and so worth creating in vocab.
LEARNING = (1, 2, 3, 4, 5)

ZWS = "​"


@dataclass(frozen=True)
class Field:
    "A field carried by the link."

    name: str  # vocab's name, also used in VlBase
    lute_column: Optional[str]  # None: not stored as a plain column
    max_length: Optional[int] = None  # Lute's column limit
    is_text: bool = True


FIELDS = (
    Field("reading", "WoRomanization", 100),
    Field("translation", "WoTranslation", 500),
    Field("status", None, is_text=False),
)


def term_text(wotext: str) -> str:
    "Lute stores multiword terms with zero-width spaces between tokens."
    return (wotext or "").replace(ZWS, "")


def lute_values(reading, translation, status) -> Dict[str, Any]:
    "A term's linked fields, in vocab's terms."
    return {
        "reading": reading or "",
        "translation": translation or "",
        "status": STATUS_TO_VOCAB.get(status),
    }


def vocab_values(word: Dict[str, Any]) -> Dict[str, Any]:
    "A vocab word's linked fields."
    return {
        "reading": word.get("reading") or "",
        "translation": word.get("translation") or "",
        "status": word.get("status"),
    }


def lute_timestamp_ms(value) -> int:
    "WoStatusChanged (UTC, from SQLite's CURRENT_TIMESTAMP) as epoch ms."
    if value is None:
        return 0
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def fits_lute(field: Field, value) -> bool:
    "Values too long for Lute's column are not truncated; they don't cross."
    return field.max_length is None or len(value or "") <= field.max_length


@dataclass
class Merge:
    "The outcome of reconciling one term with its vocab word."

    to_vocab: Dict[str, Any]
    to_lute: Dict[str, Any]
    base: Dict[str, Any]  # what both sides hold once the changes are applied


def merge(
    base: Optional[Dict[str, Any]],
    lute: Dict[str, Any],
    vocab: Optional[Dict[str, Any]],
    lute_status_ms: int,
    vocab_modified_ms: int,
) -> Merge:
    """
    Three-way merge of one term's fields.

    base is what both sides last agreed on (None if never linked), vocab is
    None if vocab has no such word.  Per field: a side that changed since
    base wins.  If both changed, or there is no base, a status goes to the
    later change (but with no base, a missing status never wins over a set
    one), and text goes to vocab (where cards are edited while studying)
    unless vocab's is empty.
    """
    if vocab is None:
        to_vocab = {k: v for k, v in lute.items() if v not in ("", None)}
        return Merge(to_vocab, {}, dict(lute))

    to_vocab, to_lute, new_base = {}, {}, {}
    for f in FIELDS:
        name = f.name
        lv, vv = lute[name], vocab[name]
        if lv == vv:
            new_base[name] = lv
            continue
        bv = None if base is None else base.get(name)
        lute_changed = base is None or lv != bv
        vocab_changed = base is None or vv != bv
        if lute_changed and not vocab_changed:
            winner = "lute"
        elif vocab_changed and not lute_changed:
            winner = "vocab"
        elif not f.is_text and base is None and None in (lv, vv):
            # First link: no status (unrated / unknown) says nothing, so it
            # never wins over one that's set.
            winner = "vocab" if lv is None else "lute"
        elif not f.is_text:
            winner = "lute" if lute_status_ms > vocab_modified_ms else "vocab"
        elif vv in ("", None):
            winner = "lute"
        else:
            winner = "vocab"
        if winner == "vocab" and not fits_lute(f, vv):
            # Can't hold it: keep Lute's and don't send it back either.
            new_base[name] = lv
            continue
        if winner == "lute":
            to_vocab[name] = lv
            new_base[name] = lv
        else:
            to_lute[name] = vv
            new_base[name] = vv
    return Merge(to_vocab, to_lute, new_base)
