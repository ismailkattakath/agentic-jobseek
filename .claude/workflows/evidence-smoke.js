export const meta = {
  name: 'evidence-smoke',
  description: 'Cloud smoke test for the evidence-draft workflow: scriptPath load, schema output, 5-lane concurrency, file writes',
  phases: [{ title: 'Smoke', detail: '8 tiny agents in 5 lanes' }],
}
// args: {dir: "<scratch dir>"}. Each agent records its own start/end with `date`, writes one file,
// and returns a schema-checked object; the lanes cap concurrency at 5 without any env setting.
const DIR = (args && args.dir) || '/tmp/evidence-smoke'
const LANES = 5
const SCHEMA = {
  type: 'object',
  properties: {
    key: { type: 'string' },
    start: { type: 'string' },
    end: { type: 'string' },
    wrote: { type: 'string' },
  },
  required: ['key', 'start', 'end', 'wrote'],
}
const keys = Array.from({ length: 8 }, (_, i) => `k${i + 1}`)
phase('Smoke')
const lanes = Array.from({ length: LANES }, (_, l) => keys.filter((_, i) => i % LANES === l))
const results = await parallel(lanes.map((lane) => async () => {
  const out = []
  for (const key of lane) {
    out.push(await agent(
      `Smoke test step for key ${key}. Run exactly: \`date -u +%Y-%m-%dT%H:%M:%SZ\` (note it as start), ` +
      `then \`sleep 20\`, then \`mkdir -p ${DIR} && echo ${key} > ${DIR}/${key}.txt\`, then ` +
      `\`date -u +%Y-%m-%dT%H:%M:%SZ\` (end). Return key, start, end, and wrote = the file path.`,
      { label: `smoke:${key}`, phase: 'Smoke', schema: SCHEMA, effort: 'low' }))
  }
  return out
}))
const flat = results.filter(Boolean).flat().filter(Boolean)
log(`${flat.length}/8 smoke agents returned schema-valid objects`)
return { returned: flat.length, results: flat }
