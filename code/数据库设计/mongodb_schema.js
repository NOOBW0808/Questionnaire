(async function () {
// mongosh: install strict validators and indexes into a NEW database only.
// schemas.json is authoritative. No application data is inserted or removed.
const fs = require('fs');
const path = require('path');
const packageDir = process.env.SURVEY_SCHEMA_DIR || process.cwd();
globalThis.installSurveySchema = async function(targetDb, dir = packageDir) {
  if (['admin','local','config','test'].includes(targetDb.getName())) {
    throw new Error('Choose a dedicated application database in the connection URI.');
  }
  const spec = JSON.parse(fs.readFileSync(path.join(dir, 'schemas.json'), 'utf8').replace(/^\uFEFF/, ''));
  const names = Object.keys(spec.collections);
  const existing = await targetDb.getCollectionNames();
  if (names.some(n => existing.includes(n))) {
    throw new Error('Application collections already exist. Use a new database or a reviewed migration.');
  }
  for (const name of names) {
    const cfg = spec.collections[name];
    await targetDb.createCollection(name, {
      validator: cfg.validator,
      validationLevel: cfg.validationLevel,
      validationAction: cfg.validationAction
    });
    for (const {key, ...options} of cfg.indexes) {
      await targetDb.getCollection(name).createIndex(key, options);
    }
  }
  return {database: targetDb.getName(), collections: names};
};
if (process.env.SURVEY_SCHEMA_LIBRARY_ONLY !== '1') {
  printjson(await globalThis.installSurveySchema(db));
}

})().catch(error => { print(error.stack || error); quit(1); });
