# SPDX-FileCopyrightText: 2026 Blagovest Petrov <blagovest@petrovs.info>
# SPDX-FileCopyrightText: 2026 Vute Tech Ltd. <https://vute.tech>
# SPDX-License-Identifier: GPL-3.0-or-later
"""LibreOffice UNO component for dragomand.

Registered as the service ``dev.l10n_bg.dragomand.LibreOffice`` and
invoked through ``service:…?arg`` menu URLs (Addons.xcu). The heavy
lifting lives in pythonpath/dragoman_lo.py, which stays UNO-free.

Menu commands (XJobExecutor.trigger arguments):
  translate      translate the selection with the saved pair
  choose         ask for languages, save them, then translate
  swap           swap the saved direction
  selftest:PATH  headless smoke hook: exercises config + the CLI without
                 any UI and writes the outcome to PATH (used by tests)
"""

import os
import re
import tempfile
import traceback

import uno  # noqa: F401  (needed for the enum helper below)
import unohelper
from com.sun.star.awt.MessageBoxButtons import BUTTONS_OK
from com.sun.star.awt.MessageBoxType import ERRORBOX, INFOBOX
from com.sun.star.beans import PropertyValue
from com.sun.star.task import XJobExecutor

from dragoman_lo import (
    TranslateError,
    available_languages,
    choose_direction,
    code_from_label,
    language_label,
    load_pair,
    locale_to_code,
    looks_like_wrong_script,
    translate_html,
    save_pair,
    script_category,
    translate_text,
)

IMPLEMENTATION_NAME = "dev.l10n_bg.dragomand.LibreOffice"


# Markup worth the HTML round-trip: links and character formatting.
_RICH_MARKUP = re.compile(r"<\s*(a\s|b[\s>]|strong[\s>]|i[\s>]|em[\s>]|u[\s>])", re.I)


def _body_fragment(html):
    """(inner body HTML without comments, reinsertion wrapper): the
    engine's HTML mode wants a fragment, not a document with DOCTYPE,
    <head> and <style>."""
    html = re.sub(r"<!--.*?-->", " ", html, flags=re.S)
    match = re.search(r"<body[^>]*>(.*)</body>", html, flags=re.S | re.I)
    fragment = match.group(1) if match else html
    wrap = (
        '<html><head><meta http-equiv="content-type" '
        'content="text/html; charset=utf-8"/></head><body>%s</body></html>'
    )
    return fragment, wrap


class DragomanJob(unohelper.Base, XJobExecutor):
    def __init__(self, ctx):
        self.ctx = ctx

    # --- XJobExecutor -----------------------------------------------------
    def trigger(self, arg):
        arg = (arg or "").strip()
        if arg.startswith("selftest:"):
            self._selftest(arg.partition(":")[2])
            return
        try:
            if arg == "swap":
                source, target = load_pair()
                save_pair(target, source)
                self._message(INFOBOX, "translation direction is now %s -> %s" % (target, source))
            elif arg == "choose":
                saved = load_pair()
                prefill = saved
                try:
                    ranges = self._selected_ranges()
                    texts = [r.getString() for r in ranges]
                    prefill = choose_direction(saved, self._detect_language(ranges, texts))
                except TranslateError:
                    pass  # no usable selection yet; prefill with the saved pair
                pair = self._choose_pair(*prefill)
                if pair is not None:
                    save_pair(*pair)
                    self._translate_selection(*pair)
            else:
                self._translate_selection(*load_pair(), auto_direction=True)
        except TranslateError as error:
            self._message(ERRORBOX, str(error))
        except Exception:
            self._message(ERRORBOX, "internal error:\n" + traceback.format_exc(limit=3))

    # --- work -------------------------------------------------------------
    def _translate_selection(self, source, target, auto_direction=False):
        ranges = self._selected_ranges()
        texts = [r.getString() for r in ranges]
        if not any(text.strip() for text in texts):
            raise TranslateError("select some text first")
        if auto_direction:
            detected = self._detect_language(ranges, texts)
            source, target = choose_direction((source, target), detected)
            whole = " ".join(texts)
            if looks_like_wrong_script(source, whole):
                preview = " ".join(whole.split())[:80]
                raise TranslateError(
                    "the selection does not look like %s text - it is mostly "
                    "Latin letters, probably transliterated during copying.\n"
                    "What was read from the document starts with:\n"
                    "\u201c%s\u2026\u201d\n"
                    "Try Edit > Paste Special > Unformatted text when pasting "
                    "from the web, or pick the languages explicitly to "
                    "translate it anyway." % (language_label(source), preview)
                )
        # Rich path: when the selection carries links or character
        # formatting, translate it as HTML so the markup is projected
        # onto the translation (the daemon's HTML mode).
        html = self._selection_html()
        if html is not None and _RICH_MARKUP.search(html) and len(ranges) == 1:
            fragment, wrap = _body_fragment(html)
            translated = translate_html(source, target, fragment)
            self._replace_range_with_html(ranges[0], wrap % translated)
            return
        for text_range, text in zip(ranges, texts):
            if text.strip():
                text_range.setString(translate_text(source, target, text))

    def _selection_html(self):
        """The current selection as an HTML fragment, or None."""
        try:
            transferable = self._controller().getTransferable()
            for flavor in transferable.getTransferDataFlavors():
                if flavor.MimeType.split(";")[0] == "text/html":
                    data = transferable.getTransferData(flavor)
                    raw = data.value if hasattr(data, "value") else data
                    if isinstance(raw, str):
                        return raw
                    return bytes(raw).decode("utf-8", "replace")
        except Exception:
            return None
        return None

    def _detect_language(self, ranges, texts):
        """The language Writer has set on the selected text (the
        character-language attribute), or None. Which of the three
        per-script attributes applies is decided by the script of the
        first letter actually selected."""
        properties = {
            "western": "CharLocale",
            "asian": "CharLocaleAsian",
            "complex": "CharLocaleComplex",
        }
        for text_range, text in zip(ranges, texts):
            if not text.strip():
                continue
            attribute = properties[script_category(text)]
            try:
                locale = getattr(text_range, attribute)
                return locale_to_code(locale.Language, locale.Country)
            except Exception:
                return None
        return None

    def _controller(self):
        desktop = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx
        )
        component = desktop.getCurrentComponent()
        controller = component.getCurrentController() if component else None
        if controller is None:
            raise TranslateError("no document is open")
        return controller

    def _selected_ranges(self):
        selection = self._controller().getSelection()
        ranges = []
        if selection is not None and hasattr(selection, "getCount"):
            for i in range(selection.getCount()):
                item = selection.getByIndex(i)
                if hasattr(item, "getString") and hasattr(item, "setString"):
                    ranges.append(item)
        elif (
            selection is not None
            and hasattr(selection, "getString")
            and hasattr(selection, "setString")
        ):
            ranges.append(selection)
        if not ranges:
            raise TranslateError(
                "this selection cannot be translated here (select text in a document)"
            )
        return ranges

    def _replace_range_with_html(self, text_range, html):
        """Replaces one selected range with an HTML fragment, so links and
        character formatting survive. Uses a temp file plus the HTML import
        filter, the dependable way to paste HTML at a cursor."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        )
        try:
            tmp.write(html)
            tmp.close()
            text = text_range.getText()
            cursor = text.createTextCursorByRange(text_range)
            cursor.setString("")
            url = unohelper.systemPathToFileUrl(tmp.name)
            prop = PropertyValue()
            prop.Name, prop.Value = "FilterName", "HTML (StarWriter)"
            cursor.insertDocumentFromURL(url, (prop,))
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    # --- UI ---------------------------------------------------------------
    def _message(self, kind, text):
        debug = os.environ.get("DRAGOMAN_LO_DEBUG")
        if debug:
            with open(debug, "a", encoding="utf-8") as f:
                f.write("--- message (%s):\n%s\n" % (kind, text))
        try:
            toolkit = self.ctx.ServiceManager.createInstanceWithContext(
                "com.sun.star.awt.Toolkit", self.ctx
            )
            try:
                parent = self._controller().Frame.ContainerWindow
            except TranslateError:
                parent = toolkit.getDesktopWindow()
            toolkit.createMessageBox(parent, kind, BUTTONS_OK, "Dragomand", text).execute()
        except Exception:
            # Headless sessions may have no dialogs; never fail on reporting.
            if debug:
                with open(debug, "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc())

    def _choose_pair(self, source, target):
        sources, targets = available_languages()
        smgr = self.ctx.ServiceManager
        model = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialogModel", self.ctx)
        model.Title = "Translate selection"
        model.Width, model.Height = 190, 68

        def add(kind, name, x, y, width, height, **props):
            control = model.createInstance("com.sun.star.awt.UnoControl%sModel" % kind)
            control.Name = name
            control.PositionX, control.PositionY = x, y
            control.Width, control.Height = width, height
            for key, value in props.items():
                setattr(control, key, value)
            model.insertByName(name, control)
            return control

        def combo(name, y, codes, current):
            # A combo box: every known language selectable by name, and
            # free typing still accepted for anything newer.
            add(
                "ComboBox",
                name,
                52,
                y,
                130,
                12,
                Dropdown=True,
                LineCount=14,
                Autocomplete=True,
                StringItemList=uno.Any(
                    "[]string", tuple(language_label(code) for code in codes)
                ),
                Text=language_label(current),
            )

        add("FixedText", "from_label", 8, 10, 40, 10, Label="From:")
        combo("from_box", 8, sources, source)
        add("FixedText", "to_label", 8, 26, 40, 10, Label="To:")
        combo("to_box", 24, targets, target)
        ok = add("Button", "ok", 74, 46, 50, 14, Label="Translate", DefaultButton=True)
        ok.PushButtonType = uno.Enum("com.sun.star.awt.PushButtonType", "OK")
        cancel = add("Button", "cancel", 132, 46, 50, 14, Label="Cancel")
        cancel.PushButtonType = uno.Enum("com.sun.star.awt.PushButtonType", "CANCEL")

        dialog = smgr.createInstanceWithContext("com.sun.star.awt.UnoControlDialog", self.ctx)
        dialog.setModel(model)
        toolkit = smgr.createInstanceWithContext("com.sun.star.awt.Toolkit", self.ctx)
        dialog.createPeer(toolkit, None)
        try:
            if not dialog.execute():
                return None
            return (
                code_from_label(dialog.getControl("from_box").Text),
                code_from_label(dialog.getControl("to_box").Text),
            )
        finally:
            dialog.dispose()

    # --- headless smoke hook ---------------------------------------------
    def _selftest(self, path):
        try:
            save_pair("bg", "en")
            assert load_pair() == ("bg", "en")
            translated = translate_text("bg", "en", "проба\nвтори ред")
            outcome = "ok\n" + translated
        except Exception as error:  # deliberately broad: report, never raise
            outcome = "fail\n%s" % error
        with open(path, "w", encoding="utf-8") as f:
            f.write(outcome)


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    DragomanJob, IMPLEMENTATION_NAME, (IMPLEMENTATION_NAME,)
)
