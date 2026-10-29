'use strict';
// Review tool: filesystem only. It never creates a MongoClient or issues DDL.
const fs = require('node:fs');
const path = require('node:path');
function ddlPlan() {
  const spec = JSON.parse(fs.readFileSync(path.join(__dirname, 'schemas.json'), 'utf8'));
  const changes = JSON.parse(fs.readFileSync(path.join(__dirname, 'change_manifest.json'), 'utf8'));
  return {
    status: 'REVIEW_ONLY_NOT_APPLIED',
    commands: [
      ...[...changes.newBusinessCollections, ...changes.newOperationalCollections].map(name => ({
        stage: 'NEW_COLLECTION', collection: name,
        command: {create: name, validator: spec.collections[name].validator,
          validationLevel: 'strict', validationAction: 'error'}
      })),
      ...[...changes.newBusinessCollections, ...changes.newOperationalCollections].map(name => ({
        stage: 'NEW_INDEXES', collection: name,
        command: {createIndexes: name, indexes: spec.collections[name].indexes}
      })),
      ...changes.extendValidatorOnly.map(name => ({
        stage: 'EXISTING_VALIDATOR', collection: name,
        command: {collMod: name, validator: spec.collections[name].validator,
          validationLevel: 'strict', validationAction: 'error'},
        prerequisite: 'Reviewed offline copy, verified backup, all writers paused, compatible app ready'
      }))
    ],
    note: 'Commands are data for review. No apply function is provided. DDL is not one transaction.'
  };
}
module.exports = {ddlPlan};
if (require.main === module) {
  if (process.argv.slice(2).some(x => x !== '--plan')) throw new Error('Only --plan is supported; no apply mode.');
  process.stdout.write(JSON.stringify(ddlPlan(), null, 2) + '\n');
}
