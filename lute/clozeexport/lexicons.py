"""
Small per-language word lists used by the cloze span heuristics.

These are deliberately short and best-effort.  They only ever *nudge* the
scoring (they tilt a cost function, they never gate a card), so a missing or
imperfect list degrades card quality slightly rather than breaking anything.
Languages with no entry simply fall back to punctuation-only clause splitting
and no anaphora hint.

Both lists can be overridden per run from the CLI, which is the intended way
to tune a language you actually read.

ANAPHORA: words that, when a sentence *starts* with one, signal that the
sentence leans on the one before it ("But ...", "He ...", "That's why ...").
Seeing one is a hint to pull the previous sentence into the card.

CONJUNCTIONS: coordinating/subordinating words that make a reasonable clause
boundary when a sentence is too long to use whole.
"""

# Punctuation that ends a clause.  Language-independent enough to hardcode.
CLAUSE_PUNCTUATION = {
    ",",
    ";",
    ":",
    "--",
    "-",
    "–",  # en dash
    "—",  # em dash
    "…",  # ellipsis
    "，",  # fullwidth comma
    "、",  # ideographic comma
    "；",  # fullwidth semicolon
    "：",  # fullwidth colon
}


# fmt: off
ANAPHORA = {
    "english": [
        "he", "she", "it", "they", "them", "him", "her", "his", "hers",
        "this", "that", "these", "those", "there", "but", "so", "then",
        "and", "yet", "also", "however", "still", "because", "which", "who",
        "afterwards", "meanwhile", "instead",
    ],
    "spanish": [
        "él", "ella", "ellos", "ellas", "lo", "la", "le", "les", "su",
        "sus", "esto", "eso", "esta", "ese", "esa", "estos", "esos",
        "aquello", "pero", "y", "entonces", "así", "luego", "además",
        "porque", "aunque", "mientras", "también", "sino",
    ],
    "french": [
        "il", "elle", "ils", "elles", "le", "la", "les", "lui", "leur",
        "ce", "cet", "cette", "ces", "cela", "ça", "celui", "celle", "mais",
        "et", "puis", "alors", "donc", "ainsi", "aussi", "pourtant",
        "parce", "car", "ensuite", "cependant",
    ],
    "german": [
        "er", "sie", "es", "ihn", "ihm", "ihr", "ihnen", "sein", "seine",
        "das", "dies", "diese", "dieser", "dieses", "dort", "da", "deshalb",
        "aber", "und", "dann", "also", "doch", "denn", "trotzdem",
        "außerdem", "danach", "deswegen", "jedoch",
    ],
    "italian": [
        "lui", "lei", "loro", "lo", "la", "gli", "le", "ne", "suo", "sua",
        "questo", "quello", "questa", "quella", "ciò", "lì", "là", "ma",
        "e", "poi", "allora", "quindi", "però", "perché", "inoltre",
        "invece", "dopo",
    ],
    "portuguese": [
        "ele", "ela", "eles", "elas", "lhe", "lhes", "seu", "sua", "o", "a",
        "isso", "isto", "aquilo", "esse", "essa", "este", "esta", "lá",
        "mas", "e", "então", "assim", "porém", "porque", "além", "depois",
        "também", "contudo",
    ],
}


CONJUNCTIONS = {
    "english": [
        "and", "but", "or", "so", "yet", "because", "although", "though",
        "while", "when", "which", "who", "that", "if", "since", "before",
        "after", "unless", "whereas",
    ],
    "spanish": [
        "y", "e", "pero", "o", "u", "porque", "aunque", "mientras",
        "cuando", "que", "si", "sino", "pues", "donde", "antes", "después",
    ],
    "french": [
        "et", "mais", "ou", "car", "parce", "bien", "quand", "lorsque",
        "que", "qui", "si", "puisque", "tandis", "avant", "après",
    ],
    "german": [
        "und", "aber", "oder", "denn", "weil", "obwohl", "während", "wenn",
        "als", "dass", "die", "der", "das", "sondern", "bevor", "nachdem",
    ],
    "italian": [
        "e", "ed", "ma", "o", "perché", "benché", "mentre", "quando", "che",
        "se", "poiché", "prima", "dopo", "però",
    ],
    "portuguese": [
        "e", "mas", "ou", "porque", "embora", "enquanto", "quando", "que",
        "se", "pois", "antes", "depois", "porém",
    ],
}
# fmt: on


def _lookup(table, language_name):
    "Case-insensitive lookup, returning a set (empty if the language is absent)."
    key = (language_name or "").strip().lower()
    return set(table.get(key, []))


def anaphora_words(language_name):
    "Sentence-initial words that hint the sentence depends on the previous one."
    return _lookup(ANAPHORA, language_name)


def conjunction_words(language_name):
    "Words that make a reasonable clause boundary."
    return _lookup(CONJUNCTIONS, language_name)
