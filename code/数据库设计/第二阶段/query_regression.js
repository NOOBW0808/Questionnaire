'use strict';
// Offline semantic regression, never a MongoDB connection. Optional dev dependencies:
// node query_regression.js <absolute node_modules directory containing mingo and bson>
const assert = require('node:assert/strict');
const path = require('node:path');
const {createRequire} = require('node:module');
const requireDev = createRequire(path.join(path.resolve(process.argv[2] || path.join(__dirname, 'node_modules')), '__review__.js'));
const {ObjectId} = requireDev('bson');
const {Aggregator} = requireDev('mingo/aggregator');
const {Context} = requireDev('mingo/core');
const expression = requireDev('mingo/operators/expression');
// Mingo names custom classes in lowercase; MongoDB's BSON ObjectId type is
// exactly "objectId". Normalize ONLY that documented simulator difference.
const context = Context.init({
  expression: {...expression, $type: (obj, expr, options) => {
    const actual = expression.$type(obj, expr, options);
    return actual === 'objectid' ? 'objectId' : actual;
  }},
  pipeline: requireDev('mingo/operators/pipeline'),
  accumulator: requireDev('mingo/operators/accumulator'),
  query: requireDev('mingo/operators/query')
});
const q = require('./query_contracts');
const oid = n => new ObjectId(n.toString(16).padStart(24,'0'));
const ids = {a:oid(1),b:oid(2),ga:oid(3),gb:oid(4),account:oid(5),actor:oid(6),target:oid(7)};
function fixtures() {
  const question = {questionId:'q_age',type:'NUMBER',reuseBinding:{questionVersionId:ids.target}};
  const response = (n,surveyId,participationId,submissionSequence,value) => ({_id:oid(n),surveyId,participationId,
    submissionSequence,publishedVersion:1,visitedQuestionIds:['q_age'],answers:[{questionId:'q_age',type:'NUMBER',value}]});
  return {
    surveys:[{_id:ids.a,ownerId:ids.actor},{_id:ids.b,ownerId:ids.actor}],
    survey_versions:[{_id:oid(10),surveyId:ids.a,publishedVersion:1,definition:{questions:[question]}},
      {_id:oid(11),surveyId:ids.b,publishedVersion:1,definition:{questions:[question]}}],
    participation_guards:[{_id:ids.ga,surveyId:ids.a,userId:ids.account},
      {_id:ids.gb,surveyId:ids.b,userId:ids.account}],
    responses:[response(100,ids.a,ids.ga,1,18),response(101,ids.a,ids.ga,2,20),response(102,ids.b,ids.gb,1,22)]
  };
}
function query(data) {
  return new Aggregator(q.crossSurveyCandidates({actorId:ids.actor,ownedSurveyIds:[ids.a,ids.b],
    questionVersionIds:[ids.target]}),{context,collectionResolver: name => data[name],useStrictMode:true}).run(data.responses);
}
let cases = 0;
function reducer() {
  let finishes = 0;
  return {
    callbacks:{initialize:()=>({values:[],accounts:new Set()}), add:(s,c,row)=>{
      const value = c.answer[0]?.value;
      if (value !== undefined && value !== null) {s.values.push(value);s.accounts.add(row.internalAccountId.toHexString());}
    }, finish:s=>{finishes++;return {count:s.values.length,distinct:s.accounts.size,values:s.values};}},
    get finishes() {return finishes;}
  };
}
async function brokenCase(name, mutate, expectedCode) {
  const data=fixtures(); mutate(data);
  const rows=query(data);
  assert.equal(rows.length,2,name+': no latest whole response disappears');
  assert.ok(rows.some(x=>x.kind==='INTEGRITY_ERROR' && x.integrityErrors.includes(expectedCode)),name);
  const r=reducer();
  await assert.rejects(q.buildCrossSurveyReport(rows,r.callbacks),e=>e.code==='DATA_INTEGRITY_ERROR' && e.status===500,name);
  assert.equal(r.finishes,0,name+': no partial report');
  cases++;
}
async function main() {
  const rows=query(fixtures());
  assert.equal(rows.length,2);
  assert.ok(rows.every(x=>x.kind==='CHECKED_RESPONSE'));
  const normal=reducer();
  assert.deepEqual(await q.buildCrossSurveyReport(rows,normal.callbacks),{count:2,distinct:1,values:[20,22]});
  assert.equal(normal.finishes,1); cases++;
  await brokenCase('late missing guard',d=>d.participation_guards.pop(),'GUARD_CARDINALITY');
  await brokenCase('missing snapshot',d=>d.survey_versions.pop(),'SNAPSHOT_CARDINALITY');
  await brokenCase('missing survey',d=>d.surveys.pop(),'SURVEY_CARDINALITY');
  await brokenCase('duplicate snapshot',d=>d.survey_versions.push({...d.survey_versions[1],_id:oid(12)}),'SNAPSHOT_CARDINALITY');
  await brokenCase('duplicate guard lookup',d=>d.participation_guards.push({...d.participation_guards[1]}),'GUARD_CARDINALITY');
  await brokenCase('guard belongs to another survey',d=>{d.participation_guards[1].surveyId=ids.a;},'GUARD_SURVEY_MISMATCH');
  await brokenCase('guard account missing',d=>{delete d.participation_guards[1].userId;},'GUARD_ACCOUNT_INVALID');
  await brokenCase('guard account string',d=>{d.participation_guards[1].userId='forged';},'GUARD_ACCOUNT_INVALID');
  await brokenCase('wrong survey owner',d=>{d.surveys[1].ownerId=ids.account;},'SURVEY_OWNER_MISMATCH');
  await brokenCase('snapshot questions malformed',d=>{d.survey_versions[1].definition.questions=null;},'SNAPSHOT_QUESTIONS_INVALID');
  await brokenCase('response answers malformed',d=>{d.responses[2].answers=null;},'RESPONSE_ANSWERS_INVALID');
  await brokenCase('response path malformed',d=>{d.responses[2].visitedQuestionIds=null;},'RESPONSE_PATH_INVALID');
  await brokenCase('missing guard even without target',d=>{d.participation_guards.pop();d.survey_versions[1].definition.questions=[];},'GUARD_CARDINALITY');
  await brokenCase('missing guard even when skipped',d=>{d.participation_guards.pop();d.responses[2].visitedQuestionIds=[];},'GUARD_CARDINALITY');
  const skipped=fixtures();skipped.responses[1].visitedQuestionIds=[];
  const skippedRows=query(skipped);
  assert.equal(skippedRows.length,2);assert.deepEqual(skippedRows[0].candidates,[]);
  assert.deepEqual(await q.buildCrossSurveyReport(skippedRows,reducer().callbacks),{count:1,distinct:1,values:[22]});cases++;
  const noTargets=fixtures();noTargets.survey_versions.forEach(s=>{s.definition.questions=[];});
  const noRows=query(noTargets);assert.equal(noRows.length,2);assert.ok(noRows.every(x=>x.kind==='CHECKED_RESPONSE'));
  assert.deepEqual(await q.buildCrossSurveyReport(noRows,reducer().callbacks),{count:0,distinct:0,values:[]});cases++;
  const empty=fixtures();empty.responses[1].answers=[];
  assert.deepEqual(await q.buildCrossSurveyReport(query(empty),reducer().callbacks),{count:1,distinct:1,values:[22]});cases++;
  const latestAcrossVersions=fixtures();latestAcrossVersions.responses[1].publishedVersion=2;
  latestAcrossVersions.responses[1].visitedQuestionIds=[];
  latestAcrossVersions.survey_versions.push({_id:oid(13),surveyId:ids.a,publishedVersion:2,definition:{questions:[]}});
  assert.deepEqual(await q.buildCrossSurveyReport(query(latestAcrossVersions),reducer().callbacks),{count:1,distinct:1,values:[22]});cases++;
  const noResponses=fixtures();noResponses.responses=[];
  assert.deepEqual(await q.buildCrossSurveyReport(query(noResponses),reducer().callbacks),{count:0,distinct:0,values:[]});cases++;
  const r=reducer();
  async function* interrupted() {yield rows[0];throw new Error('simulated cursor failure');}
  await assert.rejects(q.buildCrossSurveyReport(interrupted(),r.callbacks),/simulated cursor failure/);
  assert.equal(r.finishes,0);cases++;
  process.stdout.write(JSON.stringify({status:'PASS',cases,engine:'Mingo in-memory simulation',
    bsonTypeAdapter:'ObjectId class name normalized to MongoDB objectId',databaseConnected:false,
    limits:'No MongoDB execution, DDL acceptance, HTTP integration, Decimal metrics or live old-data verification.'},null,2)+'\n');
}
main().catch(error=>{process.stderr.write(error.stack+'\n');process.exitCode=1;});
