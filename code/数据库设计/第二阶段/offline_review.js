'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const read = name => JSON.parse(fs.readFileSync(path.join(__dirname, name), 'utf8'));
const spec = read('schemas.json');
const baseBytes = fs.readFileSync(path.join(__dirname, '..', 'schemas.json'));
const base = JSON.parse(baseBytes);
const manifest = read('change_manifest.json');
const indexes = read('indexes.json');
const sha = x => crypto.createHash('sha256').update(x).digest('hex');
assert.equal(sha(baseBytes), spec.baselineSha256);
assert.equal(Object.keys(spec.collections).length, 18);
for (const name of manifest.preserveExactly) assert.deepEqual(spec.collections[name], base.collections[name]);
for (const name of manifest.extendValidatorOnly) {
  assert.deepEqual(spec.collections[name].validator.$or[0], base.collections[name].validator);
  assert.deepEqual(spec.collections[name].indexes, base.collections[name].indexes);
}
for (const [name, cfg] of Object.entries(spec.collections)) {
  assert.equal(cfg.validationLevel, 'strict');
  assert.equal(cfg.validationAction, 'error');
  assert.equal(new Set(cfg.indexes.map(x => x.name)).size, cfg.indexes.length, name);
  const expected = indexes.retainedExistingIndexes[name] || indexes.newCollectionIndexes[name];
  assert.deepEqual(cfg.indexes, expected);
  for (const x of cfg.indexes) assert.ok(Object.keys(x.key).length > 0);
}
const plan = require('./migration_plan').migrationPlan();
assert.equal(plan.executionProvided, false);
assert.equal(plan.targetSchemaSha256, sha(fs.readFileSync(path.join(__dirname, 'schemas.json'))));
assert.equal(require('./schema_plan').ddlPlan().commands.length, 22);
assert.equal(require('./transaction_contracts').transactionContracts().transactions.length, 10);
const q = require('./query_contracts');
const prefix = q.latestWholeResponses(['survey']);
assert.equal(Object.keys(prefix[0].$match).join(), 'surveyId');
assert.ok(prefix[2].$group.latest.$first);
const cross = q.crossSurveyCandidates({actorId:'actor', ownedSurveyIds:['survey'],questionVersionIds:['version']});
assert.deepEqual(cross.slice(0, prefix.length), prefix);
assert.ok(!cross.some(x => x.$unwind), 'cross-survey integrity cannot discard lookup results');
assert.ok(!cross.slice(prefix.length).some(x => x.$match), 'no response may disappear after latest selection');
assert.equal(cross.filter(x => x.$lookup).length, 3);
assert.ok(cross.some(x => x.$set?.__integrityErrors));
const vs = spec.collections.question_versions.validator.$jsonSchema;
const adopt = vs.oneOf.find(x => x.properties.originKind.enum.includes('ADOPT'));
assert.ok(adopt.required.includes('adoptionSource'));
assert.ok(vs.properties.adoptionSource.required.includes('sourceQuestionSnapshot'));
assert.ok(vs.properties.adoptionSource.required.includes('surveyId'));
assert.ok(vs.properties.adoptionSource.required.includes('questionId'));
assert.ok(spec.collections.question_versions.indexes.some(x => x.name === 'ix_version_adoption_source' && !x.unique));
const usage = q.usageSummaries({actorId:'actor',resourceId:'resource',resourceOwnedByActor:true});
const facetIndex = usage.findIndex(x => x.$facet);
const authIndex = usage.findIndex(x => x.$match && x.$match.$expr && x.$match.$expr.$or);
assert.ok(authIndex > 0 && facetIndex > authIndex);
const projected = Object.keys(usage[facetIndex].$facet.items.at(-1).$project).filter(x => x !== '_id');
assert.deepEqual(projected.sort(), read('usage_summary_policy.json').fields.sort());
assert.throws(() => q.latestWholeResponses([]));
assert.throws(() => q.usageSummaries({actorId:'a',resourceId:'r',resourceOwnedByActor:false,limit:101}));
async function reviewReportGate() {
  let finished = 0;
  const reducer = {initialize: () => ({count:0}), add: s => {s.count++;},
    finish: s => {finished++; return s;}};
  const valid = {kind:'CHECKED_RESPONSE',internalAccountId:'internal-only',integrityErrors:[],candidates:[{}]};
  const broken = {kind:'INTEGRITY_ERROR',integrityErrors:['GUARD_CARDINALITY'],candidates:[]};
  await assert.rejects(q.buildCrossSurveyReport([valid,broken],reducer), e => e.code === 'DATA_INTEGRITY_ERROR' && e.status === 500);
  assert.equal(finished,0,'late corrupt response must suppress whole report');
  await assert.rejects(q.buildCrossSurveyReport([valid,{...valid,integrityErrors:['SNAPSHOT_CARDINALITY']}],reducer));
  assert.equal(finished,0);
  async function* failedCursor() {yield valid; throw new Error('cursor interrupted');}
  await assert.rejects(q.buildCrossSurveyReport(failedCursor(),reducer), /cursor interrupted/);
  assert.equal(finished,0);
  assert.deepEqual(await q.buildCrossSurveyReport([{...valid,candidates:[]}],reducer),{count:0});
  assert.equal(finished,1,'normal empty target is not corruption');
}
reviewReportGate().then(() => process.stdout.write(JSON.stringify({offlineChecks:'PASS',collections:18,newCollections:10,
  preservedCollections:manifest.preserveExactly, oldValidatorBranchPreserved:true,
  adoptionProvenanceContractChecked:true, lateIntegrityFailureSuppressesReport:true,
  migrationExecuted:false, databaseConnected:false,
  limits:'Does not validate MongoDB DDL acceptance, query execution plans, live data or application behavior.'},null,2)+'\n'))
  .catch(error => {process.stderr.write(error.stack+'\n');process.exitCode=1;});
