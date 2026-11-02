// agentic-jobseek's own résumé theme: JSON Resume → tagged PDF (PDF/UA-1), Material 3 design.
// Why Typst, the measurements behind every choice, and what it beat: docs/adr/0005-own-theme.md.
//
// The résumé arrives as a build INPUT, never a file in this tree (rules/no-data-here.md):
//   typst compile --input resume="$(cat resume.json)" theme/resume.typ out.pdf
#let resume-text = sys.inputs.at("resume", default: none)
#assert(resume-text != none, message: "pass --input resume=<JSON Resume text>")
#let r = json(bytes(resume-text))
#let b = r.at("basics", default: (:))
#let get(d, key) = d.at(key, default: none)
#let many(d, key) = d.at(key, default: ())

// ── Typography: Material 3, read at the source 2026-11-02 ────────────────────────────────────
// Tokens: m3.material.io/styles/typography/type-scale-tokens (baseline + emphasized sets).
// Roles: m3.material.io/styles/typography/applying-type — Display "short, important text",
// Headline primary regions, Title secondary regions, Body long passages, Label captions;
// default text colour on-surface; links primary AND underlined.
// Each token: (size, line height, tracking) in spec px, plus weight.
// Print scale: tokens are CSS px on the web; CSS Values 4 fixes 1px = 0.75pt, so printing the
// web scale as-is gives pt = px × 0.75 (Body Medium 14 → 10.5pt).
#let T = (
  font: "Google Sans 17pt", // the 17 optical-size cut (text sizes)
  // Optical size ("use an optical size that matches your type size", editorial-treatments):
  // Google Sans opsz spans 17–18 only; the 18 cut ("Google Sans") for ≥18pt printed text.
  name: (size: 36, lh: 44, track: 0, weight: 400, font: "Google Sans"), // Display Small
  section: (size: 24, lh: 32, track: 0, weight: 400, font: "Google Sans"), // Headline Small
  entry: (size: 16, lh: 24, track: 0.15, weight: 500), // Title Medium: jobs, projects
  body: (size: 14, lh: 20, track: 0.25, weight: 400), // Body Medium (Emphasized = weight 500)
  label: (size: 12, lh: 16, track: 0.5, weight: 500), // Label Medium: dates, roles, contact
  // Colour roles, baseline light scheme: @material/web 2.4.1 tokens v0_192
  // (_md-sys-color, _md-ref-palette). Page = surface-container-lowest (white).
  color: (
    text: rgb("#1d1b20"), // on-surface (neutral10)
    meta: rgb("#49454f"), // on-surface-variant (neutral-variant30)
    link: rgb("#6750a4"), // primary (primary40)
    primary: rgb("#6750a4"), // primary (primary40): top-edge strip
    outline-variant: rgb("#cac4d0"), // outline-variant (neutral-variant80): dividers
  ),
)
#let k = 0.75pt // per spec px

// ── Spacing: M3 system spacing tokens (m3.material.io/styles/spacing/tokens) ─────────────────
// An 8dp scale, space100 = 8dp. "Add custom system spacing … follow the multiplier pattern"
// (spacing/applying-spacing): space1200 = 96dp is ours — exactly 1in at 1px = 0.75pt, the side
// margin the line-length evidence asked for. Every gap below is a token, never a raw length.
#let sp = (
  "0": 0, "25": 2, "50": 4, "75": 6, "100": 8, "125": 10, "150": 12, "175": 14, "200": 16,
  "250": 20, "300": 24, "400": 32, "450": 36, "500": 40, "600": 48, "700": 56, "800": 64,
  "900": 72,
  "1200": 96, // custom (multiplier pattern)
)
#let space(n) = sp.at(str(n)) * k

// Apply a token: size, tracking, weight, optical cut, and an exact line pitch. With par leading
// 0 a line's pitch is top-edge − bottom-edge, so splitting the spec's half-leading around an
// 0.8/0.2 em box makes every line exactly `lh` tall.
#let tok(t) = (
  size: t.size * k,
  tracking: t.track * k,
  weight: t.weight,
  ..if "font" in t { (font: t.font) } else { (:) },
  top-edge: 0.8 * t.size * k + (t.lh - t.size) * k / 2,
  bottom-edge: -(0.2 * t.size * k + (t.lh - t.size) * k / 2),
)
#let styled(t, body) = text(..tok(t), body)

#let months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
#let fmt(d) = if d == none { "Present" } else if d.len() >= 7 {
  months.at(int(d.slice(5, 7)) - 1) + " " + d.slice(0, 4)
} else { d }
#let span(e) = if get(e, "startDate") == none and get(e, "endDate") == none { none } else {
  fmt(get(e, "startDate")) + " – " + fmt(get(e, "endDate"))
}
#let bare(u) = u.replace("https://", "").replace("http://", "").replace(regex("^www\."), "").trim("/")
#let url-link(u) = link(u, bare(u))
// Join the present (non-none) parts with a separator.
#let joined(parts, sep) = parts.filter(p => p != none).join(sep)

#let setup(doc) = {
  let name = b.at("name", default: "")
  set document(title: name + " — Résumé", author: name)
  set page(paper: "us-letter", margin: (x: space(1200), y: space(600))) // 1in sides, 0.5in top/bottom
  // Primary strip along the top edge of every page: a page background, so a tagged-PDF artifact.
  set page(background: place(top + left, rect(width: 100%, height: space(100), fill: T.color.primary)))
  set text(lang: "en", region: "CA", font: T.font, hyphenate: false, fill: T.color.text, ..tok(T.body))
  set par(justify: false, leading: 0pt, spacing: space(125))
  set list(spacing: space(0), indent: space(0), body-indent: space(100))
  set strong(delta: 100) // 400 → 500 = Emphasized Body Medium
  // Never break a line inside a hyphen or slash compound: extractors join "event-" + "driven"
  // into "eventdriven" and split "Python/" + "FastAPI" into "Python/ FastAPI" (both measured).
  // Boxing keeps the real characters in the text; only line breaking changes.
  show regex("~?[A-Za-z0-9.]+([-/][A-Za-z0-9.]+)+"): it => box(it)
  show link: it => underline(text(fill: T.color.link, it))
  show heading.where(level: 1): it => block(below: space(0), styled(T.name, it.body))
  // M3 divider (components/divider/specs; @material/web md-comp-divider: 1dp, outline-variant,
  // full width) between sections — not above the first, which follows the header directly.
  show heading.where(level: 2): it => context {
    if query(heading.where(level: 2).before(here())).len() > 1 {
      block(above: space(300), below: space(0), line(length: 100%, stroke: 1 * k + T.color.outline-variant))
    }
    block(above: space(300), below: space(100), styled(T.section, it.body))
  }
  // `below` ≥ space100: PDFKit glued a heading to the line under it at a ~2pt gap (measured).
  show heading.where(level: 3): it => block(above: space(200), below: space(100), styled(T.entry, it.body))
  doc
}

// Dates / roles / URL line under an entry heading.
#let meta(body) = styled(T.label, text(fill: T.color.meta, body))

#let header() = {
  heading(level: 1, b.at("name", default: ""))
  if get(b, "label") != none { styled(T.entry, b.label); linebreak() }
  let loc = b.at("location", default: (:))
  let parts = (
    if get(b, "email") != none { link("mailto:" + b.email, b.email) },
    if get(b, "phone") != none { [#b.phone] },
    if get(loc, "city") != none { [#loc.city] }, // city only; region/country stay in the JSON
    if get(b, "url") != none { url-link(b.url) },
    if get(b, "calendar") != none { url-link(b.calendar) },
    ..many(b, "profiles").map(p => if get(p, "url") != none { url-link(p.url) }),
  )
  styled(T.label, joined(parts, " · "))
}

// Per-entry Keywords lines: hidden by default (their distinct terms belong in skills);
// `--input keywords=show` brings them back.
#let show-kw = sys.inputs.at("keywords", default: "hide") == "show"
#let keywords(e) = if show-kw and many(e, "keywords").len() > 0 [*Keywords:* #e.keywords.join(", ")]

#let summary() = if get(b, "summary") != none [== Summary
  #b.summary]

#let experience() = if many(r, "work").len() > 0 [== Experience
  #for w in r.work [
    === #joined((get(w, "position"), get(w, "name")), " — ")
    #meta(joined((span(w), if get(w, "url") != none { url-link(w.url) }), " · "))
    #if get(w, "summary") != none [ \ #w.summary ]
    #list(..many(w, "highlights"))
    #keywords(w)
  ]]

#let projects() = if many(r, "projects").len() > 0 [== Projects
  #for p in r.projects [
    === #get(p, "name")
    #meta(joined((many(p, "roles").join(", "), if get(p, "url") != none { url-link(p.url) }), " · ")) \
    #get(p, "description")
    #list(..many(p, "highlights"))
    #keywords(p)
  ]]

// One paragraph per group with a hanging indent: group names form a scannable left edge while
// the text stays one flow (side-by-side cells misread in pdftotext — measured).
#let skills() = if many(r, "skills").len() > 0 [== Skills
  #set par(hanging-indent: space(300), spacing: space(50))
  #for s in r.skills [
    *#get(s, "name"):* #many(s, "keywords").join(", ")

  ]]

#let education() = if many(r, "education").len() > 0 [== Education
  #for e in r.education [
    *#joined((get(e, "studyType"), get(e, "area")), ", ")* \
    #if get(e, "institution") != none [#e.institution \ ]
    #if get(e, "url") != none [#url-link(e.url) \ ]
    #if span(e) != none [#meta(span(e)) \ ]
  ]]
