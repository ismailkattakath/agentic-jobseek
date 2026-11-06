export const meta = {
  name: 'evidence-draft',
  description: 'Draft one evidence proposal per group brief, five at a time (evidence-discover step 6, ADR-0006 § 4j)',
  whenToUse: 'Run by the evidence-discover routine after scripts/prepare.py has written the briefs',
  phases: [{ title: 'Draft', detail: 'one evidence-group drafter per brief, in 5 lanes' }],
}
// args: {scratch: "<dir with briefs/ and drafts/>", repo: "<agentic-jobseek checkout>", keys: [...]}.
// Workflow scripts have no filesystem access, so each drafter writes its own draft file and
// validates it with scripts/assemble.py --check against the ONE schema file; this script only
// returns small status objects. Five lanes cap concurrency at 5 whatever the runtime allows.
const S = args.scratch
const REPO = args.repo
const KEYS = args.keys || []
const LANES = 5
const STATUS = {
  type: 'object',
  properties: {
    group: { type: 'string' },
    action: { type: 'string', enum: ['new', 'enrich', 'skip', 'error'] },
    valid: { type: 'boolean' },
    note: { type: 'string' },
  },
  required: ['group', 'action', 'valid'],
}
const prompt = (key) =>
  `Draft the evidence proposal for group \`${key}\`.\n` +
  `Working directory for paths: ${S}. Repository checkout: ${REPO}.\n` +
  `Read, once each and nothing else of the procedure: ${REPO}/routines/evidence-group.md (your whole ` +
  `procedure and rules) and ${S}/briefs/${key}.json (your whole input). Follow the procedure. ` +
  `Write the proposal line to ${S}/drafts/${key}.json, then run ` +
  `\`python3 ${REPO}/scripts/assemble.py --check ${S}/drafts/${key}.json\` and fix the file until it ` +
  `prints valid. Return group, action, valid (true only if --check printed valid), and a one-line note.`
phase('Draft')
const lanes = Array.from({ length: LANES }, (_, l) => KEYS.filter((_, i) => i % LANES === l))
const done = await parallel(lanes.map((lane) => async () => {
  const out = []
  for (const key of lane) {
    out.push(await agent(prompt(key), {
      label: `draft:${key}`, phase: 'Draft', schema: STATUS, agentType: 'evidence-group',
    }))
  }
  return out
}))
const results = done.filter(Boolean).flat().filter(Boolean)
const got = new Set(results.map((r) => r.group))
const missing = KEYS.filter((k) => !got.has(k))
const invalid = results.filter((r) => !r.valid).map((r) => r.group)
log(`${results.length}/${KEYS.length} drafters returned; ${invalid.length} invalid; ${missing.length} missing`)
return { returned: results.length, invalid, missing, results }
