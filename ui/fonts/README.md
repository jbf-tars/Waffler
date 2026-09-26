# Bundled UI fonts

These fonts ship inside the app so the window never has to fetch them from
Google Fonts. Loading them from fonts.googleapis.com meant every launch sent
the user's IP address to Google, which does not fit Waffler's rule that the
only network calls are the user's chosen AI provider and the GitHub update
check.

| File | Family | Axes | Subset |
|---|---|---|---|
| inter-latin-wght-normal.woff2 | Inter | wght 100-900 | latin |
| inter-latin-ext-wght-normal.woff2 | Inter | wght 100-900 | latin-ext |
| source-serif-4-latin-opsz-normal.woff2 | Source Serif 4 | wght 200-900, opsz 8-60 | latin |
| source-serif-4-latin-ext-opsz-normal.woff2 | Source Serif 4 | wght 200-900, opsz 8-60 | latin-ext |
| source-serif-4-latin-opsz-italic.woff2 | Source Serif 4 Italic | wght 200-900, opsz 8-60 | latin |
| source-serif-4-latin-ext-opsz-italic.woff2 | Source Serif 4 Italic | wght 200-900, opsz 8-60 | latin-ext |
| geist-latin-wght-normal.woff2 | Geist | wght 100-900 | latin |
| geist-latin-ext-wght-normal.woff2 | Geist | wght 100-900 | latin-ext |
| geist-mono-latin-wght-normal.woff2 | Geist Mono | wght 100-900 | latin |
| geist-mono-latin-ext-wght-normal.woff2 | Geist Mono | wght 100-900 | latin-ext |

Source: the Fontsource npm packages @fontsource-variable/inter 5.2.8 and
@fontsource-variable/source-serif-4 5.2.9 (the same files and unicode ranges
Google Fonts serves). Both families are licensed under the SIL Open Font
License 1.1; the licence texts are OFL-Inter.txt and OFL-SourceSerif4.txt.
Geist and Geist Mono (3.15, the website's display and mono faces) come from
@fontsource-variable/geist 5.3.0 and @fontsource-variable/geist-mono 5.3.0,
also under the SIL Open Font License 1.1: OFL-Geist.txt and OFL-GeistMono.txt.

Roles: Inter for everything you read and click, Geist for titles, figures and
the wordmark, Geist Mono for keys, versions and costs, and Source Serif 4
italic only for dates and times.

The italic is included because the Journal's date dividers and entry
timestamps are set in serif italic; without it the browser would fake the
italic by slanting the upright letters.

The @font-face rules that use them are at the top of ui/tokens.css.
