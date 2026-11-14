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

// ── Tokens: the house-style Typst package (github:kattakath/skills, plugins/house-style) ─────
// Fonts, colours, the Material 3 print scale, the spacing scale and `tok`/`styled` all come
// from it, so this résumé, the long-form PDFs and the Google Docs share one definition. The
// reasoning behind each token (type roles, px × 0.75 = pt, optical sizes, colour roles, the
// exact line pitch) is in docs/adr/0005-own-theme.md § 4. Only the role → token map is ours.
#import "@local/house-style:1.0.0": color, font, k, scale, space, styled, tok
#let T = (
  name: scale.display-small,
  section: scale.headline-small,
  entry: scale.title-medium, // jobs, projects
  body: scale.body-medium, // Emphasized = weight 500
  label: scale.label-medium, // dates, roles, contact
)

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
  set page(background: place(top + left, rect(width: 100%, height: space(100), fill: color.primary)))
  set text(lang: "en", region: "CA", font: font, hyphenate: false, fill: color.text, ..tok(T.body))
  set par(justify: false, leading: 0pt, spacing: space(125))
  set list(spacing: space(0), indent: space(0), body-indent: space(100))
  set strong(delta: 100) // 400 → 500 = Emphasized Body Medium
  // Never break a line inside a hyphen or slash compound: extractors join "event-" + "driven"
  // into "eventdriven" and split "Python/" + "FastAPI" into "Python/ FastAPI" (both measured).
  // Boxing keeps the real characters in the text; only line breaking changes.
  show regex("~?[A-Za-z0-9.]+([-/][A-Za-z0-9.]+)+"): it => box(it)
  show link: it => underline(text(fill: color.primary, it))
  show heading.where(level: 1): it => block(below: space(0), styled(T.name, it.body))
  // M3 divider (components/divider/specs; @material/web md-comp-divider: 1dp, outline-variant,
  // full width) between sections — not above the first, which follows the header directly.
  show heading.where(level: 2): it => context {
    if query(heading.where(level: 2).before(here())).len() > 1 {
      block(above: space(300), below: space(0), line(length: 100%, stroke: 1 * k + color.outline-variant))
    }
    block(above: space(300), below: space(100), styled(T.section, it.body))
  }
  // `below` ≥ space100: PDFKit glued a heading to the line under it at a ~2pt gap (measured).
  show heading.where(level: 3): it => block(above: space(200), below: space(100), styled(T.entry, it.body))
  doc
}

// Dates / roles / URL line under an entry heading.
#let meta(body) = styled(T.label, text(fill: color.meta, body))

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
