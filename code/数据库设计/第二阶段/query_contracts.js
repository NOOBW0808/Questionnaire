'use strict';
// Pure pipeline builders for a future trusted backend. No DB connection/execution.
// actorId and all ObjectIds must come from authenticated/validated backend inputs.
function latestWholeResponses(ownedSurveyIds) {
  if (!Array.isArray(ownedSurveyIds) || !ownedSurveyIds.length) throw new Error('Explicit owned survey IDs required.');
  return [
    {$match: {surveyId: {$in: ownedSurveyIds}}},
    {$sort: {surveyId: 1, participationId: 1, submissionSequence: -1}},
    {$group: {_id: {surveyId: '$surveyId', participationId: '$participationId'}, latest: {$first: '$$ROOT'}}},
    {$replaceRoot: {newRoot: '$latest'}}
  ];
}

function crossSurveyCandidates({actorId, ownedSurveyIds, questionVersionIds}) {
  if (!actorId || !Array.isArray(questionVersionIds) || !questionVersionIds.length) throw new Error('Explicit targets required.');
  // The caller checks EVERY requested survey (including ones with no responses)
  // belongs to actorId, then runs this pipeline in that SAME snapshot/session.
  // One envelope survives for every latest whole response. Never unwind or filter
  // lookup results: a missing/ambiguous dependency must become an integrity error.
  const issue = (condition, code) => ({$cond: [condition, [code], []]});
  const intact = {$eq: [{$size: '$__integrityErrors'}, 0]};
  return [
    ...latestWholeResponses(ownedSurveyIds),
    {$lookup: {from: 'surveys', localField: 'surveyId', foreignField: '_id', as: '__survey'}},
    {$lookup: {from: 'survey_versions', let: {sid: '$surveyId', ver: '$publishedVersion'}, pipeline: [
      {$match: {$expr: {$and: [{$eq: ['$surveyId', '$$sid']}, {$eq: ['$publishedVersion', '$$ver']}]}}}
    ], as: '__snapshot'}},
    {$lookup: {from: 'participation_guards', localField: 'participationId', foreignField: '_id', as: '__guard'}},
    {$set: {
      __surveyDoc: {$ifNull: [{$arrayElemAt: ['$__survey', 0]}, null]},
      __snapshotDoc: {$ifNull: [{$arrayElemAt: ['$__snapshot', 0]}, null]},
      __guardDoc: {$ifNull: [{$arrayElemAt: ['$__guard', 0]}, null]}
    }},
    {$set: {__integrityErrors: {$concatArrays: [
      issue({$ne: [{$size: '$__survey'}, 1]}, 'SURVEY_CARDINALITY'),
      issue({$ne: [{$size: '$__snapshot'}, 1]}, 'SNAPSHOT_CARDINALITY'),
      issue({$ne: [{$size: '$__guard'}, 1]}, 'GUARD_CARDINALITY'),
      issue({$ne: [{$ifNull: ['$__surveyDoc.ownerId', null]}, {$literal: actorId}]}, 'SURVEY_OWNER_MISMATCH'),
      issue({$ne: [{$ifNull: ['$__guardDoc.surveyId', null]}, '$surveyId']}, 'GUARD_SURVEY_MISMATCH'),
      issue({$ne: [{$type: '$__guardDoc.userId'}, 'objectId']}, 'GUARD_ACCOUNT_INVALID'),
      issue({$eq: [{$isArray: '$__snapshotDoc.definition.questions'}, false]}, 'SNAPSHOT_QUESTIONS_INVALID'),
      issue({$eq: [{$isArray: '$answers'}, false]}, 'RESPONSE_ANSWERS_INVALID'),
      issue({$eq: [{$isArray: '$visitedQuestionIds'}, false]}, 'RESPONSE_PATH_INVALID')
    ]}}},
    {$project: {_id: 0, responseId: '$_id', surveyId: 1, publishedVersion: 1, participationId: 1,
      kind: {$cond: [intact, 'CHECKED_RESPONSE', 'INTEGRITY_ERROR']},
      integrityErrors: '$__integrityErrors',
      internalAccountId: {$cond: [intact, '$__guardDoc.userId', null]},
      candidates: {$cond: [intact, {$map: {
        input: {$filter: {input: '$__snapshotDoc.definition.questions', as: 'q', cond: {$and: [
          {$in: ['$$q.reuseBinding.questionVersionId', {$literal: questionVersionIds}]},
          {$in: ['$$q.questionId', '$visitedQuestionIds']}
        ]}}}, as: 'q', in: {question: '$$q',
          answer: {$filter: {input: '$answers', as: 'a', cond: {$eq: ['$$a.questionId', '$$q.questionId']}}}}
      }}, []]}
    }}
  ];
}
// INTERNAL envelopes only. Empty candidates are normal (no target, skipped path);
// they MUST NOT conceal dependency errors. Inspect errors before target/empty filters.
// Do not filter the stream to CHECKED_RESPONSE or aggregate only candidates in Mongo.
// This checks selected latest responses, not the entire historical database.

function integrityFailure(row) {
  const error = new Error('Statistics unavailable because required data is inconsistent.');
  error.code = 'DATA_INTEGRITY_ERROR';
  error.status = 500;
  // Restricted server log only; HTTP serializers must whitelist code + requestId.
  error.internalDetails = {responseId: row?.responseId, surveyId: row?.surveyId,
    reasons: row?.integrityErrors || ['INVALID_ENVELOPE']};
  return error;
}

async function buildCrossSurveyReport(rows, {initialize, add, finish}) {
  // Reducers operate on request-local memory only: no HTTP writes/streaming,
  // cache publication, notifications or external side effects from these callbacks.
  // finish() is reachable only after successful exhaustion of the entire cursor.
  const state = initialize();
  for await (const row of rows) {
    if (row?.kind !== 'CHECKED_RESPONSE' || !Array.isArray(row.integrityErrors) ||
        row.integrityErrors.length || !Array.isArray(row.candidates) || !row.internalAccountId) {
      throw integrityFailure(row);
    }
    for (const candidate of row.candidates) await add(state, candidate, {
      responseId: row.responseId, surveyId: row.surveyId, publishedVersion: row.publishedVersion,
      participationId: row.participationId, internalAccountId: row.internalAccountId
    });
  }
  return finish(state);
}
// Reducer validates instanceSelectors, comparisonSpec, content/context comparability,
// semantic option maps, units and nonempty answers; uses Decimal arithmetic, then
// emits ONLY aggregate metrics/distinct account count. Errors/cursor failures abort
// the whole report; no fallback to an older answer or a different/current snapshot.
// No old unbound snapshot is inferred into a resource by this pipeline.

function usageSummaries({actorId, resourceId, resourceOwnedByActor, after = null, limit = 20}) {
  if (!actorId || !resourceId || typeof resourceOwnedByActor !== 'boolean') throw new Error('Trusted resource authorization required.');
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error('Limit must be 1..100.');
  const page = [];
  if (after) page.push({$match: {$or: [
    {surveyId: {$gt: after.surveyId}},
    {surveyId: after.surveyId, snapshotVersion: {$gt: after.snapshotVersion}},
    {surveyId: after.surveyId, snapshotVersion: after.snapshotVersion, questionVersionId: {$gt: after.questionVersionId}}
  ]}});
  page.push({$sort: {surveyId: 1, snapshotVersion: 1, questionVersionId: 1}}, {$limit: limit + 1},
    {$project: {_id: 0, surveyId: 1, surveyTitle: 1, ownership: 1, resourceId: 1,
      questionVersionId: 1, publishedVersion: {$cond: [{$eq: ['$snapshotVersion', 0]}, null, '$snapshotVersion']},
      usageStage: 1, surveyStatus: 1, instanceCount: 1}});
  return [
    {$match: {resourceId}},
    {$lookup: {from: 'surveys', localField: 'surveyId', foreignField: '_id', as: '__survey'}},
    {$unwind: '$__survey'},
    {$lookup: {from: 'usage_summary_grants', let: {sid: '$surveyId', rid: '$resourceId', owner: '$__survey.ownerId'}, pipeline: [
      {$match: {$expr: {$and: [{$eq: ['$surveyId', '$$sid']}, {$eq: ['$resourceId', '$$rid']},
        {$eq: ['$grantorId', '$$owner']}, {$eq: ['$recipientId', actorId]},
        {$eq: ['$enabled', true]}, {$eq: ['$policyVersion', 'SUMMARY_V1']}]}}}
    ], as: '__grant'}},
    {$match: {$expr: {$or: [{$eq: ['$__survey.ownerId', actorId]},
      {$and: [{$literal: resourceOwnedByActor}, {$gt: [{$size: '$__grant'}, 0]}]}]}}},
    {$lookup: {from: 'survey_versions', let: {sid: '$surveyId', sv: '$snapshotVersion'}, pipeline: [
      {$match: {$expr: {$and: [{$eq: ['$surveyId', '$$sid']}, {$eq: ['$publishedVersion', '$$sv']}]}}}
    ], as: '__versions'}},
    {$set: {__definition: {$cond: [{$eq: ['$snapshotVersion', 0]}, '$__survey.draft',
      {$arrayElemAt: ['$__versions.definition', 0]}]}}},
    // Recheck the authoritative embedded binding so stale projection rows cannot leak a false reference.
    {$match: {$expr: {$gt: [{$size: {$filter: {input: {$ifNull: ['$__definition.questions', []]}, as: 'q', cond: {$and: [
      {$eq: ['$$q.questionId', '$questionId']},
      {$eq: ['$$q.reuseBinding.resourceId', '$resourceId']},
      {$eq: ['$$q.reuseBinding.questionVersionId', '$questionVersionId']}
    ]}}}}, 0]}}},
    {$set: {surveyTitle: '$__definition.title', ownership: {$cond: [{$eq: ['$__survey.ownerId', actorId]}, 'SELF', 'OTHER']},
      usageStage: {$cond: [{$eq: ['$snapshotVersion', 0]}, 'DRAFT', {$cond: [
        {$eq: ['$snapshotVersion', '$__survey.publishedVersion']}, 'CURRENT_PUBLISHED', 'HISTORICAL_PUBLISHED']}]},
      surveyStatus: {$cond: [{$and: [{$eq: ['$__survey.state', 'PUBLISHED']}, {$ne: ['$__survey.deadlineAt', null]},
        {$lte: ['$__survey.deadlineAt', '$$NOW']}]}, 'EXPIRED', '$__survey.state']}}},
    {$group: {_id: {sid: '$surveyId', sv: '$snapshotVersion', vid: '$questionVersionId'},
      surveyId: {$first: '$surveyId'}, snapshotVersion: {$first: '$snapshotVersion'},
      surveyTitle: {$first: '$surveyTitle'}, ownership: {$first: '$ownership'},
      resourceId: {$first: '$resourceId'}, questionVersionId: {$first: '$questionVersionId'},
      usageStage: {$first: '$usageStage'}, surveyStatus: {$first: '$surveyStatus'}, instanceCount: {$sum: 1}}},
    {$facet: {items: page, totalVisible: [{$count: 'value'}]}}
  ];
}
// Backend validates/signs cursor with actor, resource, filters, policy, and expiry;
// takes <=limit rows, derives the next key from the final row, adds coverage,
// and normalizes missing totalVisible to zero. Authorization precedes count/page.
module.exports = {latestWholeResponses, crossSurveyCandidates, buildCrossSurveyReport, usageSummaries};
