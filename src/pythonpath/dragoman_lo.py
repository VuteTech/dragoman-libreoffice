# SPDX-FileCopyrightText: 2026 Blagovest Petrov <blagovest@petrovs.info>
# SPDX-FileCopyrightText: 2026 Vute Tech Ltd. <https://vute.tech>
# SPDX-License-Identifier: GPL-3.0-or-later
"""UNO-independent core of the LibreOffice extension.

Kept free of LibreOffice imports so it can be tested with plain python3
(see ../test-core.py). Talks to the dragomand daemon through the
dragomanctl CLI, which handles D-Bus activation and install-on-demand.
"""

import json
import os
import re
import subprocess
import unicodedata

DEFAULT_PAIR = ("bg", "en")
# Generous: the first use of a pair downloads and loads its model.
TIMEOUT_SECONDS = 300

# Display names for the codes Mozilla currently publishes (2026-09).
# Unknown codes fall back to the raw tag, so new releases still work.
LANGUAGE_NAMES = {
    "af": "Afrikaans", "ar": "Arabic", "az": "Azerbaijani",
    "be": "Belarusian", "bg": "Bulgarian", "bn": "Bengali",
    "bs": "Bosnian", "ca": "Catalan", "cs": "Czech", "da": "Danish",
    "de": "German", "el": "Greek", "en": "English", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "gl": "Galician", "gu": "Gujarati", "he": "Hebrew",
    "hi": "Hindi", "hr": "Croatian", "hu": "Hungarian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian",
    "ja": "Japanese", "kn": "Kannada", "ko": "Korean",
    "lt": "Lithuanian", "lv": "Latvian", "ml": "Malayalam",
    "mr": "Marathi", "ms": "Malay", "nb": "Norwegian Bokmål",
    "nl": "Dutch", "nn": "Norwegian Nynorsk", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian",
    "sr": "Serbian", "sv": "Swedish", "ta": "Tamil", "te": "Telugu",
    "th": "Thai", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "vi": "Vietnamese", "zh-Hans": "Chinese (Simplified)",
    "zh-Hant": "Chinese (Traditional)",
}


# Zero-width and formatting characters that ride along in web copies and
# damage translation quality (measured in docs; soft hyphens noticeably).
_STRIP = dict.fromkeys([0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF])
_REPLACE = {0x00A0: " ", 0x2028: "\n", 0x2029: "\n"}


def sanitize_text(text, source=None):
    """NFC-normalizes, removes invisible web-copy artifacts, and repairs
    mixed-script homoglyph words."""
    text = unicodedata.normalize("NFC", text)
    return fold_homoglyphs(
        text.translate({**_STRIP, **_REPLACE}),
        prefer_cyrillic=source in CYRILLIC_SOURCES,
    )


# Cyrillic letters with pixel-identical Latin twins. Web copy chains
# sometimes deliver text mixing the two scripts inside a word; it looks
# flawless on screen but shreds the translation model's tokenization.
_LATIN_TO_CYRILLIC = str.maketrans(
    "aeopcyxABEKMHOPCTYX",
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0423\u0425",
)
_CYRILLIC_TO_LATIN = str.maketrans(
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445"
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0423\u0425",
    "aeopcyxABEKMHOPCTYX",
)


def _is_cyrillic(c):
    return "\u0400" <= c <= "\u04FF"


def fold_homoglyphs(text, prefer_cyrillic=False):
    """Repairs words that mix Cyrillic and Latin homoglyphs.

    A mixed-script word folds its minority twins into the majority
    script. With `prefer_cyrillic` (the source language is Cyrillic),
    a mixed word whose Latin letters are all twins folds to Cyrillic
    regardless of majority, and - once such pollution is seen anywhere -
    stray all-twin Latin words ("ce", "c") fold too. Single-script words
    in an unpolluted text are never touched."""
    twinnable = set("aeopcyxABEKMHOPCTYX")
    cyr_twinnable = set(
        "\u0430\u0435\u043e\u0440\u0441\u0443\u0445"
        "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0423\u0425"
    )

    def word_stats(w):
        cyr = sum(1 for c in w if _is_cyrillic(c))
        lat = [c for c in w if c.isascii() and c.isalpha()]
        return cyr, lat

    words = []
    word = []
    for c in text:
        if c.isalpha():
            word.append(c)
        else:
            if word:
                words.append("".join(word))
                word.clear()
            words.append(c)
    if word:
        words.append("".join(word))

    polluted = prefer_cyrillic and any(
        (lambda st: st[0] and st[1] and all(c in twinnable for c in st[1]))(
            word_stats(w)
        )
        for w in words
        if len(w) > 1 or w.isalpha()
    )

    out = []
    for w in words:
        if not (w and w[0].isalpha()):
            out.append(w)
            continue
        cyr, lat = word_stats(w)
        all_twins = bool(lat) and all(c in twinnable for c in lat)
        if cyr and lat:
            # Non-twin letters are authoritative: they can only belong to
            # the word's real script.
            lat_proof = any(c not in twinnable for c in lat)
            cyr_proof = any(
                c not in cyr_twinnable for c in w if _is_cyrillic(c)
            )
            if cyr_proof and not lat_proof:
                w = w.translate(_LATIN_TO_CYRILLIC)
            elif lat_proof and not cyr_proof:
                w = w.translate(_CYRILLIC_TO_LATIN)
            elif not lat_proof and not cyr_proof:
                # Twins on both sides: the expected script, else majority.
                if prefer_cyrillic or cyr >= len(lat):
                    w = w.translate(_LATIN_TO_CYRILLIC)
                else:
                    w = w.translate(_CYRILLIC_TO_LATIN)
            # Both sides have proof: a genuine mixed word; leave it.
        elif polluted and not cyr and all_twins:
            w = w.translate(_LATIN_TO_CYRILLIC)
        out.append(w)
    return "".join(out)


# Sources whose text is expected to be Cyrillic; used to catch
# transliterated ("shlyokavitsa") input, which produces garbage.
CYRILLIC_SOURCES = {"bg", "ru", "uk", "be", "sr"}


def cyrillic_ratio(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    cyrillic = sum(1 for c in letters if "\u0400" <= c <= "\u04FF")
    return cyrillic / len(letters)


def looks_like_wrong_script(source, text):
    """True when `source` expects Cyrillic but the text is mostly Latin."""
    return source in CYRILLIC_SOURCES and cyrillic_ratio(text) < 0.3


def locale_to_code(language, country):
    """Maps a UNO com.sun.star.lang.Locale to a Mozilla model code.
    Returns None when no language is set."""
    language = (language or "").strip()
    country = (country or "").strip().upper()
    if not language:
        return None
    if language == "zh":
        return "zh-Hant" if country in ("TW", "HK", "MO") else "zh-Hans"
    return language


def script_category(text):
    """`western`, `asian` or `complex`, from the first letter: decides
    which of Writer's three per-run language attributes applies."""
    for ch in text:
        if not ch.isalpha():
            continue
        o = ord(ch)
        if (
            0x3040 <= o <= 0x30FF   # Hiragana, Katakana
            or 0x3400 <= o <= 0x9FFF  # Han
            or 0xAC00 <= o <= 0xD7AF  # Hangul
            or 0xF900 <= o <= 0xFAFF
        ):
            return "asian"
        if 0x0590 <= o <= 0x08FF or 0xFB1D <= o <= 0xFEFC:  # Hebrew, Arabic
            return "complex"
        return "western"
    return "western"


def choose_direction(saved, detected):
    """The pair actually used for "Translate selection".

    The saved pair is the anchor; the text's own language (Writer's
    character-language attribute) steers the direction:
      - unset/unknown, or equal to the saved source  -> saved pair
      - equal to the saved target                    -> reversed pair
      - anything else                                -> detected -> saved target
    """
    source, target = saved
    if not detected or detected == source:
        return source, target
    if detected == target:
        return target, source
    return detected, target


class TranslateError(Exception):
    """A user-presentable failure."""


def language_label(code):
    """`bg` -> `Bulgarian (bg)`; unknown codes stay as they are."""
    name = LANGUAGE_NAMES.get(code)
    return "%s (%s)" % (name, code) if name else code


def code_from_label(label):
    """Accepts `Bulgarian (bg)`, `bg`, or free-typed text."""
    label = label.strip()
    if label.endswith(")") and "(" in label:
        return label.rsplit("(", 1)[1][:-1].strip()
    # A typed language name instead of a code also works.
    lowered = label.lower()
    for code, name in LANGUAGE_NAMES.items():
        if name.lower() == lowered:
            return code
    return label


def available_languages():
    """(source_codes, target_codes) from `dragomanctl --json pairs`
    (installed models plus the cached catalog). Falls back to the static
    table when the CLI fails or the catalog is empty, so the dialog is
    never a bare text field."""
    ctl = os.environ.get("DRAGOMAN_LO_CTL", "dragomanctl")
    sources, targets = set(), set()
    try:
        proc = subprocess.run(
            [ctl, "--json", "pairs"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode == 0:
            for row in json.loads(proc.stdout):
                sources.add(row["source"])
                targets.add(row["target"])
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        pass
    if not sources or not targets:
        fallback = set(LANGUAGE_NAMES)
        sources, targets = fallback, set(fallback)
    return sorted(sources), sorted(targets)


def _config_path():
    override = os.environ.get("DRAGOMAN_LO_CONFIG")
    if override:
        return override
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if not config_home or not os.path.isabs(config_home):
        config_home = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "dragomand", "libreoffice.json")


def _valid_lang(tag):
    return (
        0 < len(tag) <= 16
        and all(c.isalnum() or c == "-" for c in tag)
        and not tag.startswith("-")
        and not tag.endswith("-")
    )


def load_pair():
    """The saved (source, target) pair, or the default."""
    try:
        with open(_config_path(), encoding="utf-8") as f:
            data = json.load(f)
        source, target = data["source"], data["target"]
        if _valid_lang(source) and _valid_lang(target):
            return source, target
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_PAIR


def save_pair(source, target):
    if not (_valid_lang(source) and _valid_lang(target)) or source == target:
        raise TranslateError(
            "bad language pair %r -> %r (use short tags like bg, en)" % (source, target)
        )
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"source": source, "target": target}, f)


def translate_text(source, target, text):
    """Translates a text block, preserving its line structure.

    Empty and whitespace-only lines are never sent: they need no
    translation, and the CLI's line-based stdin protocol cannot express a
    trailing empty segment (a trailing newline is a terminator, so a
    final blank line would come back missing)."""
    text = sanitize_text(text, source)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    jobs = [(i, line) for i, line in enumerate(lines) if line.strip()]
    if not jobs:
        return text
    translated = translate_lines(source, target, [line for _, line in jobs])
    out = list(lines)
    for (i, _), replacement in zip(jobs, translated):
        out[i] = replacement
    return "\n".join(out)


_TAG_SPLIT = re.compile(r"(<[^>]*>)")


def sanitize_html(html, source=None):
    """Applies the text sanitizer to text nodes only, leaving markup and
    entities alone, and flattens newlines (insignificant in HTML) so the
    fragment travels as one segment."""
    parts = _TAG_SPLIT.split(html)
    for i, part in enumerate(parts):
        if not part.startswith("<"):
            parts[i] = sanitize_text(part, source)
    return "".join(parts).replace("\r", " ").replace("\n", " ")


def translate_html(source, target, html):
    """Translates an HTML fragment with markup (links, bold, ...)
    preserved: the daemon's HTML mode projects tags onto the translation
    via the engine's alignments."""
    fragment = sanitize_html(html, source)
    translated = _run_translate(source, target, [fragment], html=True)
    return translated[0]


def translate_lines(source, target, lines):
    return _run_translate(source, target, lines, html=False)


def _run_translate(source, target, lines, html):
    ctl = os.environ.get("DRAGOMAN_LO_CTL", "dragomanctl")
    command = [ctl, "--json", "translate", "-f", source, "-t", target]
    if html:
        command.append("--html")
    try:
        proc = subprocess.run(
            command,
            input="\n".join(lines),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise TranslateError(
            "dragomanctl not found: is the dragomand package installed?"
        ) from None
    except subprocess.TimeoutExpired:
        raise TranslateError("translation timed out") from None
    if proc.returncode != 0:
        message = proc.stderr.strip().splitlines()
        raise TranslateError(message[-1] if message else "translation failed")
    try:
        translations = json.loads(proc.stdout)["translations"]
    except (ValueError, KeyError, TypeError):
        raise TranslateError("unexpected output from dragomanctl") from None
    if len(translations) != len(lines):
        raise TranslateError(
            "expected %d segments, got %d" % (len(lines), len(translations))
        )
    return translations
