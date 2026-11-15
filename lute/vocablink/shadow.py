"""
Read-only view of what the vocab server holds for a term ("In vocab, not
synced"), shown where the term is edited or looked up.

Only entries with several senses are shown: their translations aren't kept in
step with the term's, so the term form lists them to explain the difference.
"""

import json

from sqlalchemy import text as sqltext


def vocab_senses(session, woid):
    "The vocab senses last seen for a linked term, or [] if none to show."
    if not woid:
        return []
    try:
        row = session.execute(
            sqltext(
                "SELECT VlSenses FROM vocablink WHERE VlWoID = :w AND VlState = 'linked'"
            ),
            {"w": woid},
        ).first()
    except Exception:  # pylint: disable=broad-exception-caught
        # Not migrated yet (or no link tables): nothing to show.
        return []
    if row is None or not row[0]:
        return []
    try:
        senses = json.loads(row[0])
    except ValueError:
        return []
    return senses if isinstance(senses, list) and len(senses) > 1 else []


def senses_json(word) -> str:
    "What to store for a vocab entry: its senses when it has several."
    senses = word.get("senses") or []
    if len(senses) < 2:
        return ""
    keep = ("cue", "pos", "translation", "status")
    return json.dumps([{k: s.get(k) for k in keep} for s in senses], ensure_ascii=False)
