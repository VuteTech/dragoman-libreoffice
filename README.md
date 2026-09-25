# Dragoman LibreOffice extension

A LibreOffice extension (`.oxt`) that translates the selection in Writer
through [Dragomand](https://github.com/VuteTech/dragomand), the offline
machine-translation daemon (website:
[dragomand.l10n-bg.dev](https://dragomand.l10n-bg.dev)). It shells out to
`dragomanctl`, so install-on-demand model downloads and offline reuse
come for free. The chosen language pair is remembered in
`~/.config/dragomand/libreoffice.json`.

Requirements: LibreOffice with its Python scripting support, and
`dragomanctl` on `PATH` (the Dragomand package).

## How it works?

A **Translate** submenu under Tools and a **Translate toolbar** (enable
it under View, Toolbars, Translate) with icons: translate the selection,
choose languages, swap direction. The language chooser lists every
available language by name (from the daemon's catalog, with the full
Mozilla set as fallback) in editable dropdowns, so codes never need
typing.

**Rich text.** When the selection contains links or character
formatting, the extension translates it as HTML (the daemon's HTML mode
projects the markup onto the translation via the engine's alignments),
so a hyperlink stays a hyperlink, attached to the translated words, and
bold or italic runs survive. Plain selections take a faster line-based
path. Text is NFC-normalized and cleaned of invisible web-copy
artifacts (soft hyphens, zero-width characters, non-breaking spaces),
and mixed Cyrillic/Latin homoglyph words are repaired before
translating, since those wreck the model's tokenization while looking
fine on screen.

**Direction.** "Translate selection" follows the language Writer has
set on the selected text (under Format, Character, Language; what the
spelling checker uses). Text in the saved source language translates as
saved; text in the saved *target* language reverses the direction; any
other recognized language translates into the saved target; text
without a language uses the saved pair unchanged. The chooser dialog
prefills the same way. Nothing statistical is guessed; only Writer's
own language attribute is read.

**Tabbed ("ribbon") interface.** The extension registers its three
commands as `OfficeNotebookBar` items too, so they appear as buttons in
the dedicated **Extension** tab of the Tabbed interface. (Extensions
cannot place buttons inside built-in tabs like Tools; that is a
LibreOffice platform rule, and the classic `OfficeToolBar` registration
only yields the floating add-on toolbar, which the Tabbed UI does not
show.) The Tools, Translate menu entries work in every interface
(behind the hamburger menu button when the menubar is hidden). In the
classic interface the Translate toolbar can be shown or hidden under
View, Toolbars, Translate.

## Download

Released versions are attached to the
[GitHub releases](https://github.com/VuteTech/dragoman-libreoffice/releases)
as `dragoman-libreoffice-<version>.oxt`. Every push to `master` also
builds one, kept as a workflow artifact on the Actions tab.

## Build and install

```sh
./build-oxt.sh                      # prints dist/dragoman-libreoffice-<version>.oxt
unopkg add dist/dragoman-libreoffice-*.oxt
```

or add the built file through the Extension Manager in the Tools menu.
The version comes from git: a `vX.Y.Z` tag on the current commit gives
`X.Y.Z`, otherwise it is the last tag plus the number of commits since
it (for example `0.2.0.5`), so a development build installs over the
release it follows. `--version X.Y.Z` overrides it.
The first use of a language pair downloads its model, so it can take a
few seconds; LibreOffice waits meanwhile.

## Tests

The UNO-free core has plain-Python tests:

```sh
python3 test-core.py
```


## License

GPL-3.0-or-later. Every file carries its copyright and license (SPDX
headers, or `REUSE.toml` for files that cannot); `reuse lint` checks it.
