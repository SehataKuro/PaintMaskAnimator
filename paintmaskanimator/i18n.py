"""Translation lookup and translator installation.

The UI is authored in Japanese: the Japanese string *is* the message id, so an
untranslated build behaves exactly as before this module existed. Wrapping a
string in :func:`tr` is therefore always safe and never changes behaviour on its
own -- it only makes the string visible to ``pyside6-lupdate``.

Usage::

    from .i18n import tr

    QMessageBox.critical(self, tr("PSD書き出し"), tr("PSDを書き出せませんでした。"))

Interpolation must happen **after** translation, and by name, because a
translator may need to reorder the placeholders::

    tr("{count}個のキーフレームを書き出しました。").format(count=exported)

Never wrap a log message, a config key, a file-format token or a dict key: those
are protocol, not prose, and translating them would corrupt saved data.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

from . import config

#: Basename of the compiled catalogues under ``translations/``.
CATALOGUE = "paintmaskanimator"

#: Translation context. Deliberately empty: ``pyside6-lupdate`` cannot see
#: through the :func:`tr` wrapper to a class, so it files every string under the
#: empty context. The runtime lookup must use the same one or *nothing* resolves
#: -- silently, since an unresolved lookup returns the source string, which is
#: already Japanese. ``test_i18n.py`` round-trips a real catalogue to catch a
#: regression here.
CONTEXT = ""

#: Locale name -> label shown in the language menu. The source language first.
SUPPORTED_LANGUAGES = {
    "ja": "日本語",
    "en": "English",
}

DEFAULT_LANGUAGE = "ja"

#: Installed translators, kept referenced so Qt does not collect them.
_installed: list[QTranslator] = []


def tr(text: str, disambiguation: str | None = None, n: int = -1) -> str:
    """Translate ``text`` in the application context (identity when untranslated)."""
    return QCoreApplication.translate(CONTEXT, text, disambiguation, n)


def translations_dir() -> Path:
    return Path(__file__).resolve().parent / "translations"


def available_languages() -> dict[str, str]:
    """Languages with a compiled catalogue present, plus the source language."""
    found = {DEFAULT_LANGUAGE: SUPPORTED_LANGUAGES[DEFAULT_LANGUAGE]}
    for code, label in SUPPORTED_LANGUAGES.items():
        if code == DEFAULT_LANGUAGE:
            continue
        if (translations_dir() / f"{CATALOGUE}_{code}.qm").exists():
            found[code] = label
    return found


def system_language() -> str:
    """The best supported match for the OS locale, else the source language."""
    for name in QLocale.system().uiLanguages():
        code = name.replace("-", "_").split("_")[0].lower()
        if code in SUPPORTED_LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def install(app: QCoreApplication, language: str | None = None) -> str:
    """Install the catalogue for ``language`` (auto-detected when ``None``).

    Returns the language actually installed. The source language installs no
    catalogue at all, so it can never be stale relative to the code.
    """
    for translator in _installed:
        app.removeTranslator(translator)
    _installed.clear()

    code = language or system_language()
    if code not in SUPPORTED_LANGUAGES:
        code = DEFAULT_LANGUAGE
    if code == DEFAULT_LANGUAGE:
        return code

    catalogue = translations_dir() / f"{CATALOGUE}_{code}.qm"
    translator = QTranslator()
    if not translator.load(str(catalogue)):
        # A missing or unreadable catalogue must not break start-up; the app
        # simply stays in the source language.
        return DEFAULT_LANGUAGE
    app.installTranslator(translator)
    _installed.append(translator)

    # Qt's own strings (standard dialog buttons, file dialog) ship separately.
    qt_translator = QTranslator()
    qt_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_translator.load(f"qtbase_{code}", qt_path):
        app.installTranslator(qt_translator)
        _installed.append(qt_translator)
    return code


#: Config key holding the user's explicit language choice ("system" to follow the OS).
CONFIG_KEY = "language"
SYSTEM = "system"


def preferred_language() -> str:
    """The saved language choice, or ``SYSTEM`` when the user has not picked one."""
    value = config.get_value(CONFIG_KEY, SYSTEM)
    return value if value in SUPPORTED_LANGUAGES or value == SYSTEM else SYSTEM


def set_preferred_language(language: str) -> None:
    """Persist the language choice. Takes effect on the next start-up."""
    if language != SYSTEM and language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"unsupported language: {language!r}")
    config.set_value(CONFIG_KEY, language)


def install_preferred(app: QCoreApplication) -> str:
    """Install the catalogue for the saved choice (or the OS locale)."""
    choice = preferred_language()
    return install(app, None if choice == SYSTEM else choice)
