CLI commands.

Note the lute.app_factory has to be specified as the `--app`.

Samples:

```
flask --app lute.app_factory cli --help

flask --app lute.app_factory cli language_export English ./hello.csv
```

## cloze_export

Exports cloze (fill-in-the-blank) cards from your books to a CSV you can import
into Anki. For each term you're studying it picks a span of text you can
actually read -- no unknown words, at most one other word you're still learning
-- growing very short sentences into their neighbours for context, and cutting
very long ones down to the clauses around the target. See `lute/clozeexport/`.

```
flask --app lute.app_factory cli cloze_export --language Spanish ./cards.csv

# just one book, three cards per term, one card per word family
flask --app lute.app_factory cli cloze_export --book 14 --max-per-term 3 --family ./cards.csv

# let the configured AI provider re-cut the cards the heuristics aren't
# confident about (it chooses boundaries only -- it never writes card text)
flask --app lute.app_factory cli cloze_export --language Spanish --use-llm ./cards.csv
```

The `cloze` column holds `{{c1::...}}` deletions, `sentence` the same text
unmarked, and `score` how good the span was judged to be (lower is better).
Nothing is written to your database.

See the  help for a command:

```
flask --app lute.app_factory cli language_export --help

Usage: flask cli language_export [OPTIONS] LANGUAGE OUTPUT_PATH

  Get all terms from active books in the language, and write a data file of
  term frequencies and children.
```