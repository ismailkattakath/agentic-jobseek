# ADR-0005 — Our own theme: Typst, single column, Material 3, gated text layer

**Status:** accepted, 2026-11-02. Supersedes ADR-0003 §4's "stay on academic-cv-lite". Every
choice below was measured, mostly on the master résumé and one tailored application (the
Lead Developer posting), with four text readers and two ATS checkers.

## 1. The engine — Typst, not an HTML JSON Resume theme

- **What a PDF has to get right** for an ATS: the *text layer*, not the look. Three layers
  matter — drawing order, geometry, and the tag tree (Tagged PDF, ISO 32000). Reference for a
  correctly ordered two-column PDF: PDF Association technique G4_03 (UA1_Tpdf-G4_03, CC-BY).
- **Typst 0.15.1** (nixpkgs) writes tagged PDFs and **PDF/UA-1** (`--pdf-standard ua-1`); veraPDF
  1.30.2 `--flavour ua1` PASSES every build. Fonts embed as CID TrueType.
- **Rejected — HTML themes through Chrome** (the jsonresume/resumed path): Chrome embeds any
  *variable* font as Type 3 (Reactive Resume's checker caps such a file at 25/100); the trial
  in ADR-0003 §4 found no HTML theme without a text-layer defect; and every measurement here
  would have to be redone on Chrome output.
- **Rejected — existing Typst packages** (Typst Universe, searched 2026-11-02): `altacv` (its
  own look, JSON-Resume-*style* data), `jsume` (a different schema), `gairm-import`
  (validation only — `jsonresume validate` already does that).
- **Kept from the old path:** `jsonresume validate` (schema gate). jsonresume.org's hosted
  profile still renders `meta.theme` (an HTML theme); this theme only makes the PDF.

## 2. Layout — single column, because columns reorder text

| Layout tried | pdftotext (geometric) | `-raw` / PDFKit / tags |
|---|---|---|
| Right sidebar spanning pages | 86–95 inversions | raw/PDFKit wrong, tags right |
| Left sidebar ending on page 1 | 10 inversions (headings sharing a baseline) | in order |
| Full-height two-column grid, name in sidebar | in order at 8.5pt; **breaks** once a heading collides with the name, or the sidebar spills to page 2 (12 inversions) | spill breaks raw too |
| Page-1 sidebar + full width after | in order, but p2+ lines ran **~130 characters** | in order |
| Flowing two columns (newspaper) | **2–3 inversions**; tuning gutter, heading pairs and spacing passed **4 of 30** configurations — position luck, not a parameter | in order |
| **Single column** | **in order** | **in order** |

- A table for skills (name | keywords) read in order too, but only while every name fits one
  line, and took 35 lines against 24 — rejected for the hanging indent below.

## 3. Line length — 1in side margins

- Evidence (line-length research, 2026-11-02): Bringhurst 45–75 characters per line (66
  ideal), WCAG 1.4.8 AAA ≤ 80, Baymard 50–75, Butterick 45–90; Ling & van Schaik (2006) best
  accuracy at 70 CPL; no study tested beyond 100.
- Measured at the print sizes below: 0.55in margins = median 103 / p90 132 CPL. **1in = median
  85–87 / p90 92–95.** Narrower would need ~1.15in for every line ≤ 90 (≈ +1 page).

## 4. Design — Material 3, applied from the spec, not by eye

- **Type** (m3 type-scale tokens + "Applying type"): name Display Small 36/44 · sections
  Headline Small 24/32 · entries Title Medium 16/24/500 · body Body Medium 14/20 · dates, roles,
  contact Label Medium 12/16/500 · emphasis = Emphasized Body Medium (weight 500, never Bold).
  **Print scale: CSS px × 0.75 = pt** (CSS Values 4), so body = 10.5pt. Exact line pitch from the
  token line heights.
- **Font:** Google Sans (OFL), vendored static cuts in `theme/fonts/` with `OFL.txt` and
  `TRADEMARKS.md`. Optical size per "use an optical size that matches your type size": the 18
  cut for Headline/Display, the 17 cut below. Variable files are never used (Type 3 risk).
- **Colour** (`@material/web` 2.4.1 tokens, baseline light scheme): text on-surface `#1d1b20`,
  meta on-surface-variant `#49454f`, links primary `#6750a4` **and underlined** (the guide
  requires both), a primary strip on every page's top edge, dividers outline-variant `#cac4d0`
  at 1dp (m3 divider spec). Every pair ≥ 4.5:1.
- **Spacing:** every gap is an M3 spacing token (8dp scale). The one custom token, space1200 =
  96dp = exactly 1in, follows the guide's multiplier rule.
- **Not applied:** shape, elevation, motion (no print meaning); icons and skill chips (icon
  glyphs and chips break the extracted text).
- **Tried and dropped by the operator:** a full-bleed primary header band; a footer with name
  and page numbers (copied into every extraction; ATS scores unchanged, removed because nobody
  prints the file).

## 5. Text-layer rules the theme enforces

| Defect (measured) | Fix in `theme/lib.typ` |
|---|---|
| Line break inside "event-driven" → extractors join "eventdriven" | hyphen/slash compounds are boxed (`show regex(...)`) |
| Break after "Python/" → "Python/ FastAPI" in every reader | same box covers `/` |
| "~ 480" split from its number | the box takes a leading `~` |
| PDFKit glued an entry heading to its date line at a ~2pt gap | heading `below` ≥ space100 |
| Static Roboto has no "→" (serif fallback) | Google Sans covers it |
| Letter-spaced headings ("S U M M A R Y") hid sections from Reactive Resume | no tracking on headings |

- Per-entry `keywords` lines are **hidden** by default (`--input keywords=show` restores
  them): 53 of 75 entries were repeats; hiding cost 6 → 5 pages and **no** measurable keyword
  match. Distinct terms that matter belong in `skills`, in the master.

## 6. Results and the gate

| Same master, Lead Developer posting | Reactive Resume | Jobalytics match / overall / sections / essentials |
|---|---|---|
| stackoverflow (previous theme) | 60 | 63 / 68 / 70 / 88 |
| **this theme** | **99** | **63 / 83 / 100 / 100** |

- Reactive Resume's last point is its link check: visible text is the bare domain, the target
  is `https://…` — a deliberate choice.
- Jobalytics scores the **upload filename**: "…-M3Final.pdf" cost 8 essentials points;
  `render.sh` already names files `<Name>-<Role>.pdf`.
- **Gate:** `scripts/theme-check.py` (order + completeness + splits, two readers) runs in
  `render.sh` after every render, and `nix flake check` renders JSON Resume's public sample
  (`theme/test/sample.resume.json`, MIT, jsonresume/resume-schema 1.2.1) and requires
  theme-check PASS and veraPDF PDF/UA-1 PASS — on Linux and macOS.
