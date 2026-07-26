"""
Settings form.
"""

import os
from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, IntegerField, TextAreaField, SelectField
from wtforms.validators import InputRequired, NumberRange
from wtforms import ValidationError


class UserSettingsForm(FlaskForm):
    """
    Settings.

    Note the field names here must match the keys in the settings table.
    """

    backup_enabled = BooleanField("Backup Enabled")
    backup_dir = StringField("Backup directory")
    backup_auto = BooleanField("Run backups automatically (daily)")
    backup_warn = BooleanField("Warn if backup hasn't run in a week")
    backup_count = IntegerField(
        "Retain backup count",
        validators=[InputRequired(), NumberRange(min=1)],
        render_kw={
            "title": "Count of zipfiles to retain, oldest files are deleted first"
        },
    )

    current_theme = SelectField("Theme")
    custom_styles = TextAreaField("Custom styles")
    show_highlights = BooleanField("Highlight terms by status")

    open_popup_in_new_tab = BooleanField("Open popup in new tab")
    stop_audio_on_term_form_open = BooleanField("Stop audio on term form open")
    show_streak_on_home = BooleanField("Show reading streak on home page")
    stats_calc_sample_size = IntegerField(
        "Book stats page sample size",
        validators=[InputRequired(), NumberRange(min=1, max=500)],
        render_kw={"title": "Number of pages to use for book stats calculation."},
    )

    term_popup_promote_parent_translation = BooleanField(
        "Promote parent translation to term translation if possible"
    )
    term_popup_show_components = BooleanField("Show component terms")

    term_parent_autocomplete = BooleanField(
        "Auto-correct text typed in the parent term field",
        render_kw={
            "title": (
                "When off, the parent field still suggests matching terms "
                "in a dropdown, but text you type is left exactly as typed: "
                "no automatic capitalization or spelling correction from the "
                "OS, and Tab/Right arrow won't silently accept a suggestion."
            )
        },
    )

    mecab_path = StringField("MECAB_PATH environment variable")
    reading_choices = [
        ("katakana", "Katakana"),
        ("hiragana", "Hiragana"),
        ("alphabet", "Romaji"),
    ]
    japanese_reading = SelectField("Pronunciation characters", choices=reading_choices)

    use_ankiconnect = BooleanField("Enable export using AnkiConnect")
    ankiconnect_url = StringField("AnkiConnect URL", validators=[InputRequired()])

    # AI translation suggestions.
    ai_suggestions_enabled = BooleanField("Enable AI translation suggestions")
    ai_provider = SelectField(
        "AI provider",
        choices=[
            ("gemini", "Google Gemini"),
            ("openai", "OpenAI-compatible (OpenAI / OpenRouter / Ollama)"),
        ],
    )
    ai_api_key = StringField(
        "AI API key",
        render_kw={
            "type": "password",
            "autocomplete": "off",
            "title": "Stored locally; never sent to the browser.",
        },
    )
    ai_model = StringField(
        "AI model(s)",
        render_kw={
            "title": (
                "One model, or a comma-separated cascade tried best-first with "
                "fallback when rate-limited. Blank = default Gemini cascade."
            )
        },
    )
    ai_base_url = StringField(
        "AI base URL (OpenAI-compatible only)",
        render_kw={
            "title": "e.g. https://api.openai.com/v1 or http://localhost:11434/v1"
        },
    )
    ai_trigger = SelectField(
        "Fetch suggestions",
        choices=[
            ("auto_new", "Automatically, for words with no translation yet"),
            ("auto_always", "Automatically, every time a word form opens"),
            ("on_demand", "Only when I click the suggest button"),
        ],
    )
    ai_target_language = StringField(
        "Translate into",
        render_kw={"title": "Your language, e.g. English"},
    )
    ai_explanation_language = StringField(
        "Explain in",
        render_kw={
            "title": (
                "Language the context-explanation panel writes in. "
                "Blank = same as 'Translate into'."
            )
        },
    )

    # DeepL sentence / paragraph translation (independent of the LLM features).
    deepl_enabled = BooleanField("Enable DeepL sentence/paragraph translation")
    deepl_api_key = StringField(
        "DeepL API key",
        render_kw={
            "type": "password",
            "autocomplete": "off",
            "title": "Stored locally; never sent to the browser.",
        },
    )
    deepl_target_lang = StringField(
        "DeepL target language",
        render_kw={
            "title": (
                "DeepL language code to translate into, e.g. EN-US, EN-GB, DE, "
                "FR, ES, IT, JA, ZH.  The source language is auto-detected."
            )
        },
    )

    def validate_backup_dir(self, field):
        "Field must be set if enabled."
        if self.backup_enabled.data is False:
            return
        v = field.data
        if (v or "").strip() == "":
            raise ValidationError("Backup directory required")

        abspath = os.path.abspath(v)
        if v != abspath:
            msg = f'Backup dir must be absolute path.  Did you mean "{abspath}"?'
            raise ValidationError(msg)
        if not os.path.exists(v):
            raise ValidationError(f'Directory "{v}" does not exist.')
        if not os.path.isdir(v):
            raise ValidationError(f'"{v}" is not a directory.')


class UserShortcutsForm(FlaskForm):
    """
    Shortcuts form.

    The route manages getting and storing the settings
    from the db, as there's a variable number of settings,
    and it's easier to just work with the data directly
    rather than trying to create a variable number of fields.

    I'm only using this form to get the validate_on_submit()!
    There's likely a better way to do this.
    """
