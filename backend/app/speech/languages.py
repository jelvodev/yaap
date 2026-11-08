"""Language names as the reply_language setting holds them, turned into ISO 639-1 codes.

Whisper takes a code to transcribe in a fixed language, and the browser picks a voice by code. A name YAAP does not
know gives no code: Whisper then detects the language itself and the browser uses its default voice.
"""

import re

# Whisper's languages by their English name (the list in Whisper's tokenizer).
WHISPER_LANGUAGES = {
    "afrikaans": "af", "albanian": "sq", "amharic": "am", "arabic": "ar", "armenian": "hy", "assamese": "as",
    "azerbaijani": "az", "bashkir": "ba", "basque": "eu", "belarusian": "be", "bengali": "bn", "bosnian": "bs",
    "breton": "br", "bulgarian": "bg", "cantonese": "yue", "catalan": "ca", "chinese": "zh", "croatian": "hr",
    "czech": "cs", "danish": "da", "dutch": "nl", "english": "en", "estonian": "et", "faroese": "fo", "finnish": "fi",
    "french": "fr", "galician": "gl", "georgian": "ka", "german": "de", "greek": "el", "gujarati": "gu",
    "haitian creole": "ht", "hausa": "ha", "hawaiian": "haw", "hebrew": "he", "hindi": "hi", "hungarian": "hu",
    "icelandic": "is", "indonesian": "id", "italian": "it", "japanese": "ja", "javanese": "jw", "kannada": "kn",
    "kazakh": "kk", "khmer": "km", "korean": "ko", "lao": "lo", "latin": "la", "latvian": "lv", "lingala": "ln",
    "lithuanian": "lt", "luxembourgish": "lb", "macedonian": "mk", "malagasy": "mg", "malay": "ms",
    "malayalam": "ml", "maltese": "mt", "maori": "mi", "marathi": "mr", "mongolian": "mn", "myanmar": "my",
    "nepali": "ne", "norwegian": "no", "nynorsk": "nn", "occitan": "oc", "pashto": "ps", "persian": "fa",
    "polish": "pl", "portuguese": "pt", "punjabi": "pa", "romanian": "ro", "russian": "ru", "sanskrit": "sa",
    "serbian": "sr", "shona": "sn", "sindhi": "sd", "sinhala": "si", "slovak": "sk", "slovenian": "sl",
    "somali": "so", "spanish": "es", "sundanese": "su", "swahili": "sw", "swedish": "sv", "tagalog": "tl",
    "tajik": "tg", "tamil": "ta", "tatar": "tt", "telugu": "te", "thai": "th", "tibetan": "bo", "turkish": "tr",
    "turkmen": "tk", "ukrainian": "uk", "urdu": "ur", "uzbek": "uz", "vietnamese": "vi", "welsh": "cy",
    "yiddish": "yi", "yoruba": "yo",
}

# Other names people use for the same languages, including their own names for the common ones.
ALIASES = {
    "flemish": "nl", "nederlands": "nl", "vlaams": "nl", "deutsch": "de", "français": "fr", "francais": "fr",
    "español": "es", "espanol": "es", "castilian": "es", "castellano": "es", "italiano": "it", "português": "pt",
    "portugues": "pt", "brazilian portuguese": "pt", "svenska": "sv", "dansk": "da", "norsk": "no", "bokmål": "no",
    "bokmal": "no", "norwegian bokmål": "no", "polski": "pl", "türkçe": "tr", "turkce": "tr", "suomi": "fi",
    "mandarin": "zh", "farsi": "fa", "burmese": "my", "valencian": "ca", "moldavian": "ro", "moldovan": "ro",
    "letzeburgesch": "lb", "pushto": "ps", "panjabi": "pa", "sinhalese": "si", "filipino": "tl",
    "american english": "en", "british english": "en",
}

CODES = frozenset(WHISPER_LANGUAGES.values())


def language_code(name: str) -> str | None:
    """The code for a language name such as Dutch or Nederlands, or a code itself (nl); None when unknown."""
    text = " ".join(name.strip().lower().split())
    if not text:
        return None
    if text in CODES:
        return text
    for candidate in (text, re.sub(r"\s*\(.*\)\s*$", "", text)):
        code = WHISPER_LANGUAGES.get(candidate) or ALIASES.get(candidate)
        if code:
            return code
    return None


# Short, frequent words that tell the common languages apart, for reading a reply aloud when no reply language is
# set. Words that several of these languages share are mostly left out.
STOPWORDS = {
    "en": {"the", "and", "are", "you", "that", "this", "with", "for", "not", "have", "it", "of", "to", "can"},
    "nl": {"de", "het", "een", "en", "niet", "dat", "je", "jij", "u", "wat", "voor", "met", "ook", "kan", "zijn"},
    "de": {"der", "die", "das", "und", "ist", "nicht", "ich", "sie", "mit", "auch", "ein", "eine", "für", "kann"},
    "fr": {"le", "la", "les", "et", "est", "vous", "pas", "une", "pour", "avec", "dans", "ce", "sont", "qui"},
    "es": {"el", "los", "las", "y", "es", "por", "pero", "está", "puede", "del", "muy", "hay", "esto"},
    "it": {"il", "lo", "gli", "che", "per", "non", "sono", "anche", "della", "può", "questo", "molto"},
    "pt": {"o", "os", "uma", "com", "não", "você", "também", "pode", "isso", "muito", "são", "foi"},
}


def guess_language(text: str) -> str | None:
    """The most likely of a few common languages, by counting their frequent words; None when unclear."""
    words = re.findall(r"[^\W\d_]+", text.lower())[:400]
    if len(words) < 3:
        return None
    scores = {code: sum(word in stopwords for word in words) for code, stopwords in STOPWORDS.items()}
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best, top), (_, runner_up) = ranked[0], ranked[1]
    # A clear lead only: mixed or code-heavy text keeps the browser's default voice.
    if top < 2 or top < runner_up * 1.3:
        return None
    return best
