/**
 * evidence inbox drop (agentic-jobseek ADR-0006 § 4h).
 *
 * The cloud evidence-discover routine POSTs its proposal and run files here with curl, so the
 * bytes reach Drive exactly as the routine's script wrote them: no model retypes them. Deployed
 * as a web app that executes as the operator. Script Properties (never in this repo):
 *   TOKEN      the shared secret the routine sends in the JSON body (never in the URL,
 *              which proxies and request logs keep)
 *   FOLDER_ID  the drop folder; created on first use if unset (drive.file can only see
 *              files this script created, so it must create the folder itself)
 *
 * POST a JSON body {token, name, content} (content = the file's text). Answers JSON:
 * {ok, id, name, bytes, sha256} or {ok: false, error}. sha256 is over the UTF-8 bytes of
 * content as received, so the caller can prove the upload was byte-exact.
 *
 * POST {token, action: "list"} answers {ok, runs: [{folder, name, until_at}]}: every run file in
 * the inbox (the drop folder's parent), drop/ and processed/, so the routine's window and taken
 * stamps come from a script, not a connector (ADR-0006 § 4l). Reading files this script did not
 * create needs drive.readonly; the code reads only that folder tree, and only run files.
 */

const NAME_RE = /^(proposal-[0-9A-Za-z]+\.jsonl|run-[0-9A-Za-z]+\.json)$/;

function doPost(e) {
  try {
    const props = PropertiesService.getScriptProperties();
    const token = props.getProperty('TOKEN');
    let p;
    try {
      p = JSON.parse((e && e.postData && e.postData.contents) || '');
    } catch (err) {
      return reply_({ok: false, error: 'body is not JSON'});
    }
    if (!token || !sameText_(String(p.token || ''), token)) return reply_({ok: false, error: 'unauthorized'});
    if (p.action === 'list') return reply_({ok: true, runs: listRuns_(dropFolder_(props))});
    const name = String(p.name || '');
    if (!NAME_RE.test(name)) return reply_({ok: false, error: 'bad name'});
    const body = typeof p.content === 'string' ? p.content : '';
    if (!body) return reply_({ok: false, error: 'empty content'});

    const folderId = dropFolder_(props);
    const q = "name = '" + name + "' and '" + folderId + "' in parents and trashed = false";
    if (Drive.Files.list({q: q, fields: 'files(id)'}).files.length) {
      return reply_({ok: false, error: 'exists'});
    }
    const blob = Utilities.newBlob(body, 'text/plain', name);
    const file = Drive.Files.create({name: name, parents: [folderId], mimeType: 'text/plain'}, blob);
    const bytes = Utilities.newBlob(body).getBytes();
    return reply_({ok: true, id: file.id, name: name, bytes: bytes.length, sha256: hex_(bytes)});
  } catch (err) {
    return reply_({ok: false, error: String(err && err.message || err)});
  }
}

/** Run once from the editor: grants the drive.file consent and creates the drop folder. */
function setup() {
  const id = dropFolder_(PropertiesService.getScriptProperties());
  Logger.log('drop folder ' + id + (PropertiesService.getScriptProperties().getProperty('TOKEN') ? ', TOKEN set' : ', TOKEN NOT set'));
}

function listRuns_(dropId) {
  const drop = DriveApp.getFolderById(dropId);
  const inbox = drop.getParents().next();
  const folders = [['inbox', inbox], ['drop', drop]];
  const processed = inbox.getFoldersByName('processed');
  if (processed.hasNext()) folders.push(['processed', processed.next()]);
  const runs = [];
  folders.forEach(([label, folder]) => {
    const files = folder.getFiles();
    while (files.hasNext()) {
      const f = files.next();
      if (!/^run-[0-9A-Za-z]+\.json$/.test(f.getName()) || f.isTrashed()) continue;
      let until = null;
      try {
        until = JSON.parse(f.getBlob().getDataAsString()).until_at || null;
      } catch (err) {
        until = null; // an unreadable run file still takes its stamp
      }
      runs.push({folder: label, name: f.getName(), until_at: until});
    }
  });
  return runs.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

/** Run from the editor: logs what `list` would answer (counts and the latest until_at only). */
function checkList() {
  const runs = listRuns_(dropFolder_(PropertiesService.getScriptProperties()));
  const ends = runs.map((r) => r.until_at).filter(Boolean).sort();
  Logger.log(runs.length + ' run files; latest until_at ' + ends[ends.length - 1] +
    '; by folder ' + JSON.stringify(runs.reduce((m, r) => ((m[r.folder] = (m[r.folder] || 0) + 1), m), {})));
}

function dropFolder_(props) {
  let id = props.getProperty('FOLDER_ID');
  if (!id) {
    id = Drive.Files.create({name: 'drop', mimeType: 'application/vnd.google-apps.folder'}).id;
    props.setProperty('FOLDER_ID', id);
  }
  return id;
}

function hex_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes)
    .map((b) => ('0' + (b & 0xff).toString(16)).slice(-2))
    .join('');
}

function sameText_(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
