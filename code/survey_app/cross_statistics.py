"""Snapshot-consistent cross-survey single-question metrics; no raw answer output."""
from collections import Counter
from decimal import Decimal, localcontext
from copy import deepcopy

from bson import ObjectId
from bson.decimal128 import Decimal128
from flask import Blueprint, current_app, g

from .common import body, fail, fields, oid, reply, text_field, utcnow, plain, Problem
from .domain import as_decimal, decimal128, empty, canonical_answers, validate_definition
from .phase2_core import enabled, canonical_hash, resource, version
from .security import authenticated, db
from .statistics import average

cross_statistics = Blueprint('cross_statistics', __name__, url_prefix='/api/v1')


def integrity(reason, row=None):
    current_app.logger.error('requestId=%s integrity=%s responseId=%s', g.request_id, reason, (row or {}).get('_id'))
    fail('DATA_INTEGRITY_ERROR', '统计所需数据不完整或不一致，请凭requestId排查；未返回部分结果', 500)


def latest_envelopes(survey_ids, session):
    pipeline = [
        {'$match': {'surveyId': {'$in': survey_ids}}},
        {'$sort': {'surveyId': 1, 'participationId': 1, 'submissionSequence': -1}},
        {'$group': {'_id': {'s': '$surveyId', 'p': '$participationId'}, 'row': {'$first': '$$ROOT'}}},
        {'$replaceRoot': {'newRoot': '$row'}},
        {'$lookup': {'from': 'surveys', 'localField': 'surveyId', 'foreignField': '_id', 'as': '__survey'}},
        {'$lookup': {'from': 'survey_versions', 'let': {'sid': '$surveyId', 'v': '$publishedVersion'}, 'pipeline': [
            {'$match': {'$expr': {'$and': [{'$eq': ['$surveyId', '$$sid']}, {'$eq': ['$publishedVersion', '$$v']}]}}}], 'as': '__snapshot'}},
        {'$lookup': {'from': 'participation_guards', 'localField': 'participationId', 'foreignField': '_id', 'as': '__guard'}}]
    return db().responses.aggregate(pipeline, session=session, allowDiskUse=True, maxTimeMS=10000)


def checked(row, session):
    if any(len(row[k]) != 1 for k in ('__survey', '__snapshot', '__guard')):
        integrity('LOOKUP_CARDINALITY', row)
    survey, snap, guard = row['__survey'][0], row['__snapshot'][0], row['__guard'][0]
    if (survey['ownerId'] != g.user['_id'] or guard.get('surveyId') != row['surveyId'] or
            not isinstance(guard.get('userId'), ObjectId) or
            not isinstance(snap.get('definition', {}).get('questions'), list) or
            not isinstance(row.get('answers'), list) or not isinstance(row.get('visitedQuestionIds'), list)):
        integrity('REFERENCE_OR_SHAPE', row)
    if not row['anonymous'] and row.get('respondentUserId') != guard['userId']:
        integrity('RESPONDENT_MISMATCH', row)
    if (row.get('schemaVersion') != 2 or not isinstance(row.get('submissionSequence'), int)
            or isinstance(row['submissionSequence'], bool) or row['submissionSequence'] < 1
            or guard.get('acceptedCount', 0) < row['submissionSequence']
            or not db().users.find_one({'_id': guard['userId']}, {'_id': 1}, session=session)):
        integrity('ACCOUNT_OR_SEQUENCE_INVALID', row)
    try:
        definition = snap['definition']
        validate_definition(definition, publishing=True)
        from .phase2_core import check_binding
        for question in definition['questions']:
            check_binding(question, session)
        if len({q['questionId'] for q in definition['questions']}) != len(definition['questions']):
            integrity('DUPLICATE_QUESTION_ID', row)
        replay = canonical_answers(definition, plain(row['answers']), complete=True)
        if (replay['visitedQuestionIds'] != row['visitedQuestionIds'] or
                replay['answers'] != row['answers'] or row['anonymous'] != definition['policy']['anonymous']):
            integrity('WHOLE_RESPONSE_PATH_MISMATCH', row)
    except (Problem, KeyError, TypeError, ValueError, AttributeError):
        integrity('WHOLE_RESPONSE_INVALID', row)
    return snap, guard['userId']


def execution_context(definition, question):
    """Conservative structural grouping; never a claim of automatic semantic proof."""
    positions = {q['questionId']: i for i, q in enumerate(definition['questions'])}
    prefix = []
    for q in definition['questions'][:positions[question['questionId']] + 1]:
        mapping = {x['localOptionId']: x['semanticOptionId'] for x in q.get('reuseBinding', {}).get('optionMap', [])}
        opts = {o['optionId']: mapping.get(o['optionId'], str(i)) for i, o in enumerate(q.get('options', []))}
        rules = []
        for rule in q['jumpRules']:
            value = rule['value']
            if q['type'] == 'SINGLE':
                value = opts.get(value)
            elif q['type'] == 'MULTIPLE':
                value = [opts.get(x) for x in value]
            rules.append({**rule, 'ruleId': None, 'value': value,
                          'target': 'END' if rule['target'] == 'END' else positions.get(rule['target'])})
        prefix.append({'type': q['type'], 'title': q['title'], 'required': q['required'], 'validation': q['validation'],
                       'options': [o['label'] for o in q.get('options', [])], 'rules': rules})
    return canonical_hash({'description': definition['description'], 'prefix': prefix})


def prepare_comparison(versions, spec, merged):
    fields(spec, ('contextConfirmed', 'rationale', 'semanticDeclarations', 'categories', 'optionMappings', 'bins'))
    bins = spec.get('bins')
    if bins is not None:
        if not isinstance(bins, list) or not 2 <= len(bins) <= 101:
            fail('BINS_INVALID', 'bins应为2～101个严格递增的十进制边界')
        bins = [as_decimal(decimal128(x)) for x in bins]
        if any(a >= b for a, b in zip(bins, bins[1:])):
            fail('BINS_INVALID', '分箱边界必须严格递增')
    if not merged:
        return {}, [], bins
    if spec.get('contextConfirmed') is not True:
        fail('COMPARISON_REQUIRED', '合并须明确确认上下文可比')
    text_field(spec.get('rationale'), '比较依据', 2000, 1)
    if len({v['content']['type'] for v in versions.values()}) != 1:
        fail('NOT_COMPARABLE', '不同题型不能合并')
    declarations = spec.get('semanticDeclarations', [])
    if not isinstance(declarations, list):
        fail('COMPARISON_INVALID', 'semanticDeclarations必须为数组')
    declared = {}
    for d in declarations:
        fields(d, ('questionVersionId', 'measure', 'unit', 'timeBasis', 'population'),
               ('questionVersionId', 'measure', 'unit', 'timeBasis', 'population'))
        if d['questionVersionId'] in declared or d['questionVersionId'] not in versions:
            fail('COMPARISON_INVALID', '版本声明重复或不在targets内')
        declared[d['questionVersionId']] = {k: text_field(d[k], k, 500, 1) for k in ('measure', 'unit', 'timeBasis', 'population')}
    semantics = []
    for vid, v in versions.items():
        stored = v['content'].get('semantics', {})
        actual = declared.get(vid, stored)
        if any(not actual.get(k) for k in ('measure', 'unit', 'timeBasis', 'population')):
            fail('COMPARISON_REQUIRED', '缺少测量/单位/时间/范围说明，须显式补充语义声明')
        if any(stored.get(k) and actual[k] != stored[k] for k in ('measure', 'unit', 'timeBasis', 'population')):
            fail('NOT_COMPARABLE', '比较声明与固定版本测量说明冲突')
        semantics.append(tuple(actual[k] for k in ('measure', 'unit', 'timeBasis', 'population')))
    if len(set(semantics)) != 1:
        fail('NOT_COMPARABLE', '测量说明不一致；本版不支持不同单位转换')
    mappings, categories = {}, spec.get('categories', [])
    kind = next(iter(versions.values()))['content']['type']
    if kind in ('SINGLE', 'MULTIPLE'):
        if not isinstance(categories, list) or not 1 <= len(categories) <= 100:
            fail('MAPPING_REQUIRED', '选择题合并须明确categories和完整optionMappings')
        cat_ids = set()
        for c in categories:
            fields(c, ('categoryId', 'label'), ('categoryId', 'label'))
            cid = text_field(c['categoryId'], 'categoryId', 64, 1)
            text_field(c['label'], '类别名称', 200, 1)
            if cid in cat_ids:
                fail('MAPPING_INVALID', '类别不能重复')
            cat_ids.add(cid)
        entries = spec.get('optionMappings', [])
        if not isinstance(entries, list):
            fail('MAPPING_INVALID', 'optionMappings必须为数组')
        for entry in entries:
            fields(entry, ('questionVersionId', 'mapping'), ('questionVersionId', 'mapping'))
            vid = entry['questionVersionId']
            if vid not in versions or vid in mappings or not isinstance(entry['mapping'], list):
                fail('MAPPING_INVALID', '映射版本无效或重复')
            pairs = {}
            for x in entry['mapping']:
                fields(x, ('semanticOptionId', 'categoryId'), ('semanticOptionId', 'categoryId'))
                if x['semanticOptionId'] in pairs or x['categoryId'] not in cat_ids:
                    fail('MAPPING_INVALID', '映射重复或指向未知类别')
                pairs[x['semanticOptionId']] = x['categoryId']
            if set(pairs) != {x['semanticOptionId'] for x in versions[vid]['content']['options']}:
                fail('MAPPING_INVALID', '选项映射不完整')
            mappings[vid] = pairs
        if set(mappings) != set(versions):
            fail('MAPPING_INVALID', '必须映射全部目标版本')
    return mappings, categories, bins


def build_report(data, session):
    targets = data['targets']; sids = data['surveyIds']; mode = data.get('mode', 'GROUPED')
    if (not isinstance(targets, list) or not 1 <= len(targets) <= 50 or
            not isinstance(sids, list) or not 1 <= len(sids) <= 100 or mode not in ('GROUPED', 'MERGED')):
        fail('SCOPE_INVALID', 'targets为1～50项、surveyIds为1～100项，mode为GROUPED/MERGED')
    survey_ids = [oid(x) for x in sids]
    if len(set(survey_ids)) != len(survey_ids):
        fail('SCOPE_INVALID', '问卷不能重复')
    if db().surveys.count_documents({'_id': {'$in': survey_ids}, 'ownerId': g.user['_id']}, session=session) != len(survey_ids):
        fail('NOT_FOUND', '问卷不存在或无权访问', 404)
    versions = {}
    for t in targets:
        fields(t, ('resourceId', 'questionVersionId'), ('resourceId', 'questionVersionId'))
        r = resource(t['resourceId'], session); v = version(r, t['questionVersionId'], session)
        vid = str(v['_id'])
        if vid in versions:
            fail('TARGET_INVALID', '目标版本不能重复')
        versions[vid] = v
    merged = mode == 'MERGED'
    spec = data.get('comparisonSpec', {})
    maps, categories, bins = prepare_comparison(versions, spec, merged)
    selectors = data.get('instanceSelectors', [])
    if not isinstance(selectors, list) or len(selectors) > 1000:
        fail('INSTANCE_INVALID', 'instanceSelectors应为有限数组')
    selected = {}
    for x in selectors:
        fields(x, ('surveyId', 'publishedVersion', 'questionId'), ('surveyId', 'publishedVersion', 'questionId'))
        if x['surveyId'] not in sids or type(x['publishedVersion']) is not int or x['publishedVersion'] < 1:
            fail('INSTANCE_INVALID', '实例必须属于选中问卷及正发布版本')
        key = (x['surveyId'], x['publishedVersion'])
        if key in selected:
            fail('INSTANCE_INVALID', '每份快照最多选择一个统计实例')
        selected[key] = text_field(x['questionId'], 'questionId', 64, 1)
    groups = {}; exclusions = Counter(); whole = 0; account_scopes = set()
    def group(v, definition=None, q=None):
        vid = str(v['_id']); context = execution_context(definition, q) if definition else 'NO_MATCHING_PUBLISHED_INSTANCE'
        key = 'MERGED' if merged else vid + ':' + context
        if key not in groups:
            c = v['content']
            groups[key] = {'key': key, 'type': c['type'], 'targets': set(), 'count': 0, 'accounts': set(), 'reached': 0,
                'sum': Decimal(0), 'frequency': Counter(), 'buckets': [0] * (len(bins) + 1) if bins else None,
                'options': {x['categoryId']: {'categoryId': x['categoryId'], 'label': x['label'], 'count': 0} for x in categories} if merged else
                           {x['semanticOptionId']: {'semanticOptionId': x['semanticOptionId'], 'label': x['label'], 'count': 0} for x in c.get('options', [])}}
        groups[key]['targets'].add(vid)
        return groups[key]
    # Validate instance selection even for published scopes with zero answers.
    matched_versions = set()
    snapshot_questions = {}
    for snap in db().survey_versions.find({'surveyId': {'$in': survey_ids}}, session=session).max_time_ms(10000):
        qs = snap.get('definition', {}).get('questions')
        if not isinstance(qs, list):
            continue  # Integrity scope is selected latest whole responses, checked below.
        try:
            validate_definition(snap['definition'], publishing=True)
            from .phase2_core import check_binding
            for question in qs:
                check_binding(question, session)
        except (Problem, KeyError, TypeError, ValueError):
            continue
        candidates = [q for q in qs if str(q.get('reuseBinding', {}).get('questionVersionId')) in versions]
        k = (str(snap['surveyId']), snap['publishedVersion'])
        if k in selected:
            candidates = [q for q in candidates if q['questionId'] == selected[k]]
            if len(candidates) != 1:
                fail('INSTANCE_INVALID', '实例选择不属于目标版本或不存在')
        elif len(candidates) > 1:
            fail('INSTANCE_SELECTION_REQUIRED', '同卷有多个目标实例，请明确instanceSelectors')
        snapshot_questions[k] = candidates
        for q in candidates:
            v = versions[str(q['reuseBinding']['questionVersionId'])]
            group(v, snap['definition'], q); matched_versions.add(str(v['_id']))
    if set(selected) - set(snapshot_questions):
        fail('INSTANCE_INVALID', '实例发布快照不存在')
    for vid, v in versions.items():
        if vid not in matched_versions:
            group(v)
    with localcontext() as context:
        context.prec = 12500
        # NO target/path/empty filter before checked(); no $unwind of joined arrays.
        for row in latest_envelopes(survey_ids, session):
            snap, account = checked(row, session); whole += 1
            scope = (row['surveyId'], account)
            if scope in account_scopes:
                integrity('DUPLICATE_ACCOUNT_PARTICIPATION', row)
            account_scopes.add(scope)
            candidates = snapshot_questions.get((str(row['surveyId']), row['publishedVersion']), [])
            if not candidates:
                exclusions['NO_TARGET_IN_LATEST_WHOLE_RESPONSE'] += 1; continue
            q = candidates[0]; vid = str(q['reuseBinding']['questionVersionId']); v = versions[vid]
            st = group(v, snap['definition'], q)
            if q['questionId'] not in row['visitedQuestionIds']:
                exclusions['SKIPPED'] += 1; continue
            st['reached'] += 1
            answers = [a for a in row['answers'] if a['questionId'] == q['questionId']]
            if len(answers) != 1 or answers[0]['type'] != q['type']:
                integrity('REACHED_ANSWER_INVALID', row)
            value = answers[0]['value']
            if empty(value):
                exclusions['EMPTY'] += 1; continue
            st['count'] += 1; st['accounts'].add(account)
            if q['type'] == 'NUMBER':
                if not isinstance(value, Decimal128) or not value.to_decimal().is_finite():
                    integrity('NUMBER_INVALID', row)
                number = as_decimal(value); st['sum'] += number
                if bins:
                    index = 0
                    while index < len(bins) and number >= bins[index]:
                        index += 1
                    st['buckets'][index] += 1
                else:
                    st['frequency'][number] += 1
                    if len(st['frequency']) > 1000:
                        fail('DISTRIBUTION_TOO_LARGE', '不同数值超过1000项，请明确提供bins后重试')
            elif q['type'] in ('SINGLE', 'MULTIPLE'):
                mapping = {x['localOptionId']: x['semanticOptionId'] for x in q['reuseBinding']['optionMap']}
                values = [value] if q['type'] == 'SINGLE' else value
                if not isinstance(values, list) or any(x not in mapping for x in values):
                    integrity('ANSWER_OPTION_INVALID', row)
                semantic = {mapping[x] for x in values}
                if merged:
                    semantic = {maps[vid][x] for x in semantic}
                for x in semantic:
                    st['options'][x]['count'] += 1
        # Only now, after successful cursor exhaustion, create a public report.
        output = []
        for key, st in sorted(groups.items()):
            item = {'groupKey': key, 'type': st['type'], 'questionVersionIds': sorted(st['targets']),
                    'answeredParticipationCount': st['count'], 'distinctAnsweredAccountCount': len(st['accounts']), 'reachedCount': st['reached']}
            if st['type'] == 'NUMBER':
                item['average'] = average(st['sum'], st['count'])
                item['distribution'] = {'kind': 'BINS', 'boundaries': [str(x) for x in bins],
                    'counts': st['buckets'], 'intervalRule': 'underflow; [lower,upper); overflow'} if bins else {
                    'kind': 'FREQUENCY', 'items': [{'value': str(n), 'count': c} for n, c in sorted(st['frequency'].items())]}
            if st['type'] in ('SINGLE', 'MULTIPLE'):
                item['options'] = list(st['options'].values())
            output.append(item)
    return {'targets': targets, 'surveyIds': sids, 'mode': mode, 'comparisonSpec': spec, 'instanceSelectors': selectors,
            'latestWholeResponseCount': whole, 'groups': output, 'exclusions': dict(exclusions),
            'contextBasis': 'MERGED_EXPLICIT_DECLARATION' if merged else 'EXECUTION_CONTEXT_V1: description, ordered prefix and normalized rules',
            'generatedAt': utcnow()}


@cross_statistics.post('/question-statistics/cross-survey')
@authenticated
@enabled
def cross_survey():
    data = body(('targets', 'surveyIds', 'mode', 'comparisonSpec', 'instanceSelectors'), ('targets', 'surveyIds'))
    return reply(current_app.extensions['database'].transaction(lambda session: build_report(data, session)))
