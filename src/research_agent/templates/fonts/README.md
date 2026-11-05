The fonts used by the printed edition (PDF). They are shipped with the program so the PDF looks the same on every machine (a Mac, the Docker image on Railway, CI) instead of using whatever fonts each one happens to have.

| File | Used for | Source and licence |
|---|---|---|
| `Gelasio-Regular`, `-Bold`, `-Italic`, `-BoldItalic` (`.ttf`) | all reading text and headings (a serif made to match Georgia) | Gelasio by the Gelasio Project Authors, https://github.com/SorkinType/Gelasio, SIL Open Font License 1.1 (`OFL-Gelasio.txt`). **Modified:** the four static weights were extracted from the upstream variable fonts (`Gelasio[wght].ttf`, `Gelasio-Italic[wght].ttf`) with `fontTools.varLib.instancer` at weights 400 and 700; the letter shapes are unchanged. The font has no Reserved Font Name. |
| `LiberationSans-Regular`, `-Bold`, `-Italic`, `LiberationMono-Bold` (`.ttf`) | labels, tables, chips (sans) and the printed DOI (monospace); also the fallback for Greek letters | Liberation Fonts 2.1.5 (Red Hat, with Google's Arimo and Cousine), SIL Open Font License 1.1, unmodified (`LICENSE-Liberation.txt`). |
| `DejaVuSans.ttf` | last-resort fallback for symbols the others lack (superscript plus, arrows, mathematical signs) | DejaVu Fonts (Bitstream Vera licence, free to use and redistribute), unmodified (`LICENSE-DejaVu.txt`). |

How they are used: `../print_edition.css.j2` declares them with `@font-face` as "Edition Serif", "Edition Sans", "Edition Mono" and "Edition Symbols", and the renderer gives WeasyPrint the templates folder as its base address so the relative `fonts/...` links resolve. The system fonts named after them in the stylesheet (Georgia, Helvetica and so on) are only a fallback if a file is missing.

Changing a font: replace the file, keep its name, and keep its licence file here. The OFL requires the licence text to travel with the font.
