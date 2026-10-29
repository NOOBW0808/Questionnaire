"""Build a complete catalog, reusing phase-one specifications without rewriting them."""
import copy
from collections import Counter
import re
import xml.etree.ElementTree as ET

from .common import ROOT, STAGE, CATALOG, BASELINE, encoded, read, sha

MODULES = {
    9: '独立题目与工作稿', 10: '版本历史与恢复', 11: '选用、换版、ADOPT与派生',
    12: '题库组织', 13: '版本共享与归属', 14: '使用追踪与摘要授权',
    15: '跨卷范围与题型指标', 16: '可比性与实例选择', 17: '完整性与匿名输出',
    18: '新增接口权限、事务与并发', 19: '兼容、升级与恢复',
}
AUTH = '仅在专用测试环境执行。MongoDB 7+可写副本集；API前缀/api/v1；A/B为独立Cookie会话、批次唯一账号，密码TestPass!2026。写入带Origin、X-CSRF-Token，修改带最新If-Match，创建型POST带16～128位ASCII幂等键。所有ID、ETag取真实响应，不把示例变量原样提交。'
FORM = '除本例明确覆盖或要求在步骤中创建的状态外，填写场景保持问卷anonymous=true、allowRepeat=true、deadlineAt=null且PUBLISHED；默认数字题年龄，整数0～150，测量=周岁年龄、单位=岁、时间=填写当日、对象=受访者。选用时题版固定，导入实例默认非必答、无跳转。每例独立建数据，不依赖前例。'
ROWS = []
SEQ = Counter()


def case(module, execution, title, inputs, pairs, expected, node=None, pending=False, pre=None):
    SEQ[(module, execution)] += 1
    code = f'TC{module:02d}-' + ('M' if execution == 'manual' else 'A') + f'{SEQ[(module, execution)]:02d}'
    row = {'id': code, 'phase': 2, 'moduleId': module, 'module': MODULES[module], 'title': title,
           'execution': execution, 'priority': 'P1' if execution == 'manual' else 'P0', 'sourceSteps': [],
           'requirementRefs': {9:['N01-N03','B01'],10:['N07-N15','B01'],11:['N02/N03/N11/N17','U01/U02'],12:['N18-N21'],13:['N04-N06','B02'],14:['N16','B05'],15:['N22','B03/B04'],16:['U03','N22'],17:['数据设计完整性/匿名白名单'],18:['原认证协议/增量事务边界'],19:['数据兼容与迁移方案'],20:['已确认页面流程/测试建议']}[module],
           'preconditions': ([AUTH, FORM] if execution == 'api' else [
               '在独立测试环境启动问卷应用，确认第二阶段功能已在测试库启用。浏览器打开被测系统地址；按本例需要注册带批次后缀的A/B/C账号，密码TestPass!2026。各角色使用独立浏览器会话，先登录步骤指定账号。',
               '按本例输入在页面创建独立测试题目和问卷。涉及填写时允许重复填写、无截止、保持开放；除特定题型说明外，数字题为年龄、整数0～150、单位岁。不得在实际业务库注入故障或执行迁移。']) + (pre or []), 'input': inputs,
           'steps': [{'action': a, 'expected': b} for a,b in pairs], 'expected': expected,
           'automation': {'state': 'not_implemented' if pending else ('implemented' if node else 'manual'), 'node': node},
           'evidence': '人工上传真实页面截图与实际现象；API留当轮日志、JUnit、请求状态/错误码、必要的脱敏数据断言。'}
    if node:
        file, name = node.split('::',1)
        row['implementation'] = 'test/自动化脚本/第二阶段/' + file
        row['automation']['node'] = name
    ROWS.append(row)
    return row


def api(module, file, name, title, inputs, pairs, expected, pre=None):
    return case(module,'api',title,inputs,pairs,expected,file+'::'+name,pre=pre)


def existing():
    f='test_resources.py'
    api(10,f,'test_resource_version_restore_and_published_stability','再发布后旧attempt仍按旧快照执行',{'v1成功答卷':'18','旧attempt':'20','v2最小值':'21','新attempt':'20→30'},[
        ('导入v1并发布，B提交18，A开始未完成attempt','记录发布v1快照和既有答卷BSON'),
        ('生成最低21的v2，恢复v1生成v3；显式绑定v2并再次发布','历史为3版，恢复来源/父版本正确；当前发布v2，旧快照不变'),
        ('再次发布后才用旧attempt保存提交20；新attempt先保存20再保存提交30','旧提交201且publishedVersion=1；新20为422 NUMBER_RANGE，30提交201且版本2')],['旧快照/原成功答卷BSON不变；公开定义无reuseBinding；状态开放且无截止。'])
    api(11,f,'test_adoption_preserves_source_and_legacy_execution','ADOPT完整来源与旧题实际填写',{'旧单选':'参加吗[是,否]','adoption':'POST /surveys/S/questions/Q/adoption {}','hash':'Canonical Extended JSON + SHA256'},[
        ('发布未绑定旧题并开启attempt，保存原实例BSON、编辑版本和控制序号','来源尚无reuseBinding'),
        ('绑定前后记录时间并ADOPT，独立计算哈希，再用同键重放','201后200 replay；源卷/实例/两序号/时间/完整快照/哈希正确且仅一个资源'),
        ('删除当前草稿题，旧attempt用原“是”本地optionId保存提交','旧题仍按发布v1提交201，来源证据与旧发布BSON不变')],['毫秒精度时间窗口有效，哈希不用业务函数计算。'])
    api(9,f,'test_working_draft_freeze_and_permissions','私有工作稿固定与权限',{'fixed':False,'title':'稿2'},[
        ('A创建工作稿，PATCH /question-resources/R/draft修改标题','200并返回更新ETag'),('用新ETag POST /question-resources/R/versions固定；B读取R；A持旧ETag改稿','固定201含版本ID；B为404；旧ETag为409')],['固定内容不被旧稿修改覆盖。'])
    api(11,f,'test_bound_content_guard_and_invalid_rebind_rollback','绑定内容保护和非法换版全状态回滚',{'题型':'SINGLE','rule':'首选项EQ→END','换版':'另一个单选资源，无明确optionMap'},[
        ('保存完整问卷/ETag/使用行/快照基线，PATCH绑定题标题','409，基线全部不变'),('添加END规则并发布，再保存完整基线；带明确幂等键PUT绑定到另资源','422；实例/映射/跳转、修订号、ETag、使用投影及发布快照与基线一致')],['按actorId/keyHash查不到该失败请求的成功幂等凭据；不据此声称PUT支持成功重放。'])
    for kind in ['SINGLE','MULTIPLE','TEXT','NUMBER']:
        bad={'SINGLE':'unknown_option→OPTION_INVALID','MULTIPLE':'只选1项，要求exactSelections=2→SELECTION_COUNT','TEXT':'1字或5字，限制2～4→TEXT_LENGTH','NUMBER':'-1/151→NUMBER_RANGE；20.5→INTEGER_REQUIRED'}[kind]
        good={'SINGLE':'第1项真实localOptionId','MULTIPLE':'两项真实localOptionId数组','TEXT':'正确','NUMBER':'20'}[kind]
        api(11,f,'test_four_types_can_be_reused['+kind+']',kind+'跨两卷复用、校验与提交',{'kind':kind,'非法输入':bad,'合法输入':good},[
            ('固定资源，分别导入两卷并读取完整实例','题干/类型/校验/版本哈希一致；默认非必答无跳转；选项映射一一对应'),('分别发布并开始attempt，PUT非法答案','422且业务码匹配，attempt BSON不变、无成功答卷'),('使用仍有效ETag保存合法答案并提交','201，持久化题目ID/值及publishedVersion=1正确')],['两卷草稿使用行共2；选项ID不要求跨卷相同。'])
    f='test_sharing.py'
    api(13,f,'test_shared_version_scope_consent_fork_and_anonymous_whitelist','共享版本边界、导入摘要确认和派生归属',{'共享':'A的v1→B准确用户名','私有新版':'v2','consent':{'accepted':True,'policyVersion':'SUMMARY_V1'}},[
        ('共享v1后A生成v2；B读v1资源、改作者工作稿、读授权列表与v2','仅共享版本可读且无workingDraft；其余404'),('B创建问卷，不确认导入，再确认导入','先409且无使用行；后201，A看到OTHER摘要'),('A读B整卷；B从v1独立fork；A读B新资源','整卷404；派生201归B并保留derivedFrom；A读派生404')],['摘要严格9字段白名单；题目使用权不授予答案权。'])
    api(12,f,'test_bank_updates_do_not_propagate_and_delete_keeps_history','题库更新/软删除保留历史与填写',{'题库':'常用','原答卷':'18','旧attempt':'20','删除后新attempt':'22'},[
        ('题库收录v1，问卷选用发布；准备成功答卷和未完成attempt','保存发布/答卷BSON'),('生成v2并仅更新题库成员','问卷仍绑定v1，旧发布BSON不变'),('B越权删库，再A删库；比较资源/版本/问卷/快照/答卷/attempt','B404；A204后题库404；所有保护对象BSON相同'),('旧attempt提交20，新attempt提交22','均201、仍按发布v1，原快照与原答卷不变')],['题库删除只影响组织入口，不删资源历史。'])
    api(14,f,'test_summary_close_reopen_and_usage_pagination','摘要授权关闭/重开与分页',{'引用':'B两卷各导入A题v1','分页':'limit=1','policyVersion':'SUMMARY_V1'},[
        ('两卷确认导入，A分页读取usages','totalVisible=2，两页不同问卷'),('B以授权ETag关闭其中一卷摘要，再未经新确认导入','A总数1、B本人总数2；导入409'),('B用最新授权ETag明确重开','A总数恢复2')],['关闭不能删除本人绑定或被导入隐式重开。'])
    api(13,f,'test_adopt_private_source_not_shared','共享ADOPT版本不外泄私有提取来源',{'旧文本题':'私有上下文'},[
        ('A从问卷旧题ADOPT并共享该固定版给B','ADOPT201，共享成功'),('B读取获授权的版本详情','可读内容，不含adoptionSource和createdBy')],['共享权不开放原卷上下文。'])
    f='test_transactions.py'
    tuples=[
      ('test_concurrent_fix_has_one_success_and_one_stale_etag','并发固定同一工作稿',{'并发':2,'If-Match':'同一资源ETag'},'两个线程同时POST固定版本','恰一次201、另一次409，版本数量1'),
      ('test_concurrent_same_key_creates_one_resource_and_replays','资源同键并发重放',{'并发':2,'幂等键':'相同UUID','body':'相同数字题'},'同时创建资源','201/200重放同一resourceId，固定版仅1'),
      ('test_adopt_interruption_rolls_back_every_composed_write','ADOPT组合中断全回滚',{'故障点':'sync_usages前抛RuntimeError'},'保存问卷BSON及相关集合数量，注入故障后ADOPT','500；问卷与资源/版本/使用行/凭据数量完全不变'),
      ('test_summary_close_import_race_never_silently_reopens_grant','摘要关闭与无确认导入竞争',{'竞争':'关闭授权 vs 不带新确认导入'},'B同时发关闭与导入请求','关闭200；导入201或409；最终enabled=false，A可见数0'),
      ('test_unknown_commit_result_retries_same_commit_not_business_callback','未知提交结果重试同一commit',{'故障':'第一次commit成功后注入UnknownTransactionCommitResult'},'创建固定资源并记录commit session_id','commit两次同session，资源固定版仅1'),
      ('test_bank_creation_idempotent_and_no_orphan_receipt','题库创建同键重放',{'name':'幂等题库','key':'同一UUID'},'连续两次POST /question-banks，第二次使用相同请求和键','201后200，bankId相同，replayed=true')]
    for name,title,inp,action,expected in tuples:
        api(18,f,name,title,inp,[('按输入准备本例对象及必要基线','无其他用例数据参与断言'),(action,expected)],[expected])
    f='test_cross_statistics.py'
    api(15,f,'test_latest_whole_distinct_accounts_and_anonymous_output','最新整卷与跨卷贡献及匿名',{'A卷':'X=18→20；Y=30→空','B卷':'X=22'},[
        ('同版本导入A/B并完成输入中的各次提交','所有提交201'),('POST /question-statistics/cross-survey，targets=[R/v1]、surveyIds=[A,B]','latestWholeResponseCount=3；有效2、独立账号1、均值21、频次20/22各1')],['不含账号ID、participationId、internalAccountId、respondentUserId、responseId、answers。'])
    for damage in ['guard_missing','snapshot_missing','guard_wrong_survey','guard_invalid_account','questions_malformed']:
        api(17,f,'test_integrity_failure_never_returns_partial_report['+damage+']','关联完整性异常：'+damage,{'正常数据':'A/B标准年龄场景','故障':damage},[
            ('先创建正常答卷，再仅在隔离库损坏B卷对应关联','至少还有正常候选，不改业务库'),('发跨卷统计请求','500 DATA_INTEGRITY_ERROR，响应无data，错误无内部details')],['不静默过滤异常，不返回部分统计。'])
    api(16,f,'test_merge_declaration_bins_and_unit_rejection','两个版本真实答卷合并、分箱与单位拒绝',{'v1':[18,22],'v2':[30,50],'bins':['0','21','40','150'],'mode':['GROUPED','MERGED']},[
        ('两卷分别绑定v1/v2，两个账号每卷各成功提交一次；GROUPED查询','两组各有效2、均值20/40，频次匹配输入'),('缺比较声明发MERGED；补contextConfirmed=true与rationale后再查询','先422 COMPARISON_REQUIRED；后有效4、账号2、均值30，频次独立加总120'),('加分箱，再加单位为月的v3目标','分箱[0,1,2,1,0]；不同单位422 NOT_COMPARABLE')],['公开结果无sum字段；测试自行Decimal计算总和。'])
    api(16,f,'test_duplicate_instance_requires_selector_and_empty_group','重复实例零样本和18/30明确选择',{'同卷实例':'Q1、Q2均绑定R/v1','一份答卷':{'Q1':'18','Q2':'30'}},[
        ('发布两实例但尚无答卷；无selector/有selector分别查询','无选择422；选Q1均值null、有效0'),('同卷成功提交18和30，无选择再次查询','仍422'),('依次选择Q1/Q2/Q1（surveyId、publishedVersion=1、questionId）','有效/独立账号均1；均值18/30/18，频次仅所选值、最新整卷1')],['不把两个实例混合或重复计入。'])
    api(15,f,'test_other_survey_rejected_even_without_responses','他人问卷无答卷也不得统计',{'surveyIds':['B创建的空卷'],'targets':['A资源v1']},[('A显式请求B的问卷统计','404，不以无答卷为由放行')],['共享题不扩大统计问卷权限。'])
    api(15,f,'test_latest_version_without_target_does_not_revive_old_answer','最新版无目标题不回填旧答',{'v1':'年龄18','v2':'删除年龄，文本=新版回答'},[('同账号先提交v1，再发布不含目标题的v2并提交','两次提交201'),('统计原年龄版本','所有组有效数0，不恢复18')],['先选最新成功整卷，再筛目标。'])
    f='test_statistics_edges.py'
    tuples=[
      (17,'test_late_cursor_interruption_never_exposes_earlier_metrics','消费游标中途失败不返回先前指标',{'注入':'读到1个正常候选后AutoReconnect'},'查询跨卷统计并记录已消费候选','确实消费1个候选；503且无data/groups'),
      (15,'test_latest_actual_jump_does_not_backfill_old_target_answer','最新实际跳题不回填',{'首题':'继续/结束，结束→END','旧答':'继续+18','最新答':'结束'},'同账号依次提交两份整卷，再统计年龄','SKIPPED排除1、均值null，不回填18'),
      (16,'test_choice_merge_requires_complete_semantic_mapping','选择题完整显式语义映射',{'两版本':'是否参加；各选是','categories':['0=是','1=否']},'完整映射MERGED，再移除一个映射重发','完整时有效2、账号1、是2否0；缺映射422'),
      (15,'test_decimal_average_and_text_count','Decimal均值与文本只计数',{'数字':['0.1','0.2'],'integerOnly':False,'文本':'不应公开的原始文本'},'两账号提交数字后统计，再提交文本并统计','均值精确0.15；文本有效1，不含原文'),
      (17,'test_duplicate_snapshot_lookup_is_an_error_not_double_count','重复快照关联不得双计',{'故障':'临时移除快照唯一索引，在随机库插入同卷同发布版重复快照'},'有正常结果候选时插入重复记录并统计，finally还原索引','500 DATA_INTEGRITY_ERROR，无data')]
    for mod,name,title,inp,action,expected in tuples:
        api(mod,f,name,title,inp,[('按输入建立独立问卷、版本和成功答卷','故障只影响随机测试库'),(action,expected)],[expected])
    f='test_migration.py'
    tuples=[
      ('test_old_schema_detected_and_upgrade_idempotent_keeps_history','旧结构升级重跑与历史保护','BASE DDL + fixtures/legacy.ejson；backup/apply两次/verify/activate','旧8集合逻辑BSON哈希不变，无推断资源/使用行；激活前启动阻断、激活后stage2且拒绝直接rollback'),
      ('test_partial_ddl_resume_and_preactivation_rollback','部分DDL续跑与激活前回退','备份后手工创建目标question_resources，apply→rollback','中间态阻断；续跑后可回退stage1'),
      ('test_backup_restore_copy_verifies_original_hashes','恢复副本哈希校验与拒绝覆盖原库','backup→随机全新目标库restore-copy；再以原库名恢复','新副本hashesVerified且逻辑哈希相等；原库名恢复抛错，原数据保留'),
      ('test_unknown_validator_and_orphans_are_blocked_without_deletion','孤儿参与关联阻断且答卷不删','保存原response BSON，删除其guard，仅运行preflight','关联完整性错误；原response BSON相同'),
      ('test_changed_history_blocks_apply','备份后旧内容漂移阻断','backup后改draft.title=备份后被修改，再apply','旧8集合内容变化错误；不继续升级'),
      ('test_corrupt_backup_is_rejected','损坏备份阻断','向surveys.bson追加corrupt，再apply','哈希无效错误，不把损坏备份当可靠基线'),
      ('test_unknown_schema_and_legacy_response_shape_are_blocked','未知validator及旧响应格式保留阻断','增加unknownRequiredField→preflight；还原后将response.schemaVersion改1再预检','两次均拒绝，旧格式记录仍存在'),
      ('test_failed_ddl_keeps_ledger_and_can_resume','DDL失败台账、续跑和回退归档','question_versions.create_index注入异常；移除故障后apply/verify/rollback','FAILED台账保留，后续可续跑；ROLLED_BACK归档含台账且stage1'),
      ('test_complete_old_schema_runs_original_features_without_auto_upgrade','完整旧库兼容模式原功能可用','create_app读旧库；注册/登录/创建问卷，再调用新增资源接口','原操作成功，capabilities.schemaStage=1；新功能409 SCHEMA_UPGRADE_REQUIRED；集合仍原8个'),
      ('test_same_index_name_with_different_meaning_stops_without_overwrite','同名异义索引不覆盖','备份后新增question_resources，按目标索引名建立wrongField索引再apply','同名异义错误，wrongField索引原样保留')]
    for name,title,action,expected in tuples:
        api(19,f,name,title,{'操作与故障':action},[('创建随机隔离旧库，安装原DDL并导入虚构legacy.ejson','不是业务库备份，不操作真实旧库'),(action,expected)],[expected],['迁移写入/故障注入只在随机库，备份放临时目录，finally清理本例库。'])


def manuals():
    data=[
      (9,'私有工作稿创建、固定与刷新',{'题型':'NUMBER','题干':'实验年龄','校验':'整数0～150'},[('A登录→我的题目→创建未固定工作稿，修改题干为实验年龄2并保存/刷新','修改可恢复，未固定不当成可选固定版'),('固定初版并再次刷新详情','显示固定v1与历史入口，原问卷导航仍可达')],['截图：工作稿、固定后详情。']),
      (9,'四题型表单校验提示',{'单选':'2选项','多选':'exactSelections=2','文本':'minLength=2/maxLength=4','数字':'0～150整数'},[('按输入分别创建四种固定题，再尝试空题干、不合法范围、过多/少选项配置','表单或服务端有明确错误，不出现误导成功提示'),('改为合法配置保存，各选用到一卷并在填写页输入非法/合法值','限制可理解，错误定位清楚，合法操作可继续；数据准确性由对应API另验')],['截图：各题型配置和错误提示，不把页面提示视为已核对数据库。']),
      (10,'历史查看与恢复确认',{'v1':'年龄','v2':'今年年龄'},[('A创建v1→改题干生成v2→展开两版历史','能辨认版本/来源，v1显示年龄'),('在v1点恢复并取消，再确认恢复','取消无成功提示；确认新增v3，显示恢复源v1和父v2，已有问卷不自动换版')],['截图：历史、确认框和恢复结果。']),
      (11,'旧题ADOPT入口与来源展示',{'问卷':'实验旧题卷','旧题':'参加吗[是,否]'},[('用原编辑器建未绑定题并发布，返回编辑，点保存为独立题/ADOPT','明确告知提取而不是重写历史'),('确认后查看题目详情和原问卷','独立题可见，问卷仍有原题及本地选项，填写链接可打开原发布内容')],['截图：ADOPT前后及来源提示；BSON/哈希由API验证。']),
      (11,'选用入口和本地配置隔离',{'资源':'年龄v1','问卷':['A卷','B卷']},[('选同一固定版本到两卷，查看实例初始配置','默认非必答、无跳转，绑定内容显示只读'),('仅在A卷修改必答/顺序/合法跳转，刷新两卷','A修改保留，B本地设置不跟随；原加题/发布入口保留')],['截图：选题页、两卷不同设置。']),
      (11,'派生改绑与非法换版错误体验',{'共享题':'A的参加吗v1→B','原跳转':'是→END'},[('B选用共享题，编辑器点修改内容并派生改绑，题干改为是否参加','说明生成归B的新资源及来源，不显示可修改作者原版'),('另造选项不兼容版本尝试换版，不补映射','错误提示清楚且保留编辑上下文；不宣称已成功，重新加载后原绑定/跳转可见')],['截图：派生入口/结果、非法换版提示。']),
      (12,'题库创建、改名、成员换版和软删除',{'题库名':'常用题→课程题','成员':'年龄v1→v2'},[('创建题库，加成员，改名并刷新','名称和指定版本可辨认且持久'),('只将成员升级v2，再看已选用v1的问卷','明确不自动更新问卷'),('取消删除后再确认删除','取消仍可见；确认后题库消失，独立题和问卷仍可打开')],['截图：成员版本、改名、删除确认及独立题保留。']),
      (13,'按账号共享与接收者入口',{'作者':'A','接收者':'B准确用户名','共享版本':'v1'},[('A在固定v1输入不存在账号，再输入B用户名共享','错误或成功反馈明确，不展示全站账号列表'),('B独立浏览器登录→共享给我→打开v1；A生成私有v2','B可辨共享v1，不出现可编辑作者资源或私有v2的入口')],['截图：准确账号授权、共享列表和版本详情。']),
      (14,'导入前摘要字段告知及明确确认',{'共享题':'A给B的v1','政策':'SUMMARY_V1'},[('B选用共享题但不勾摘要确认，点导入','停留确认页，提示需确认，不能显示导入成功'),('阅读可显示字段，再明确勾选并导入','能理解会向题目所有者提供哪些问卷摘要；成功后回原编辑器')],['截图：完整摘要字段告知、未确认错误、确认后的结果。']),
    ]
    for mod,title,inp,pairs,expected in data:
        if mod == 14:
            inp['SUMMARY_V1允许的9字段'] = ['surveyId','surveyTitle','ownership','resourceId','questionVersionId','publishedVersion','usageStage','surveyStatus','instanceCount']
            expected = expected + ['摘要仅有上述业务字段及coverage/totalVisible/nextCursor分页元数据；不得出现账号/联系方式、填写链接、其他题目、答卷、作答量或统计指标。']
        case(mod,'manual',title,inp,pairs,expected,pre=['手工由用户在被测系统页面操作；本收集页只读用例、保存截图和实际判定，不代替执行业务。'])


def pending():
    data=[
      (10,'历史分页、越权谱系和授权版本过滤',{'A版本':'v1/v2/v3','B只获v1':'limit=1、篡改cursor'},'A分页；B按列表/详情/lineage访问未授权v2并串用游标','A无重漏；B不见私有版本且越权404；游标按作用域拒绝'),
      (11,'成功显式选项映射并保留本地跳转',{'旧规则':'是→END','新版':'新semanticId，完整映射到旧localId'},'提交完整optionMap换版，再发布并分别填是/否','换版200，跳转继续正确；映射缺项/重复应整体422回滚'),
      (11,'问卷内派生并原子改绑归属',{'来源':'A共享v1','接收者':'B已选用','content.title':'派生题'},'B调用问卷实例derivations，再在投影写前注入故障重复独立场景','成功归B且来源保留；失败时资源/版本/绑定/使用/凭据全回滚'),
      (12,'题库改名、移除、多题库及分页',{'题库':['常用','课程'],'资源':'同一R/v1','改名':'常用2'},'两库收录R，改名/移除一库成员；分页列库并对另一库查成员','修改仅作用于选定库；另一库/独立题/问卷不变；分页不重漏'),
      (13,'授权异常账号及新版本不继承权限',{'username':['不存在账号','B'],'版本':'仅v1共享'},'共享不存在账号/重复授权/指定v2；B访问作者资源和授权管理','准确账号检查；B仅获明确版，不可改作者或代共享；不得列全站账号'),
      (14,'三账号摘要隔离与过滤后可见总数',{'引用':'A自己1卷，B授权1卷，C未授权1卷','limit':1},'A/C分别分页查询A资源使用摘要，记录totalVisible及全部页','A只计本人及获授权B；C不见他人；分页和计数不得泄露未授权卷'),
      (14,'草稿、当前发布、历史及重复实例分组',{'同卷':'R/v1两实例→改绑一实例v2→再发布'},'分别筛usageStage、版本并翻页，独立核对真实定义','按规定阶段/版本分组，instanceCount准确，不把历史行误当当前绑定'),
      (14,'脏使用投影必须复核真实绑定',{'故障':'插入无真实实例的投影、删除真实投影','授权':'部分关闭'},'对资源查询usages并比较真实草稿/快照；必要时运行明确重建','脏行不扩大可见范围/总数；缺投影按设计报告覆盖边界，不伪造引用'),
      (16,'多选完整映射与计数分母',{'版本1':'[红,蓝]','版本2':'[红色,蓝色]','答卷':'一份多选红蓝'},'完整映射至红/蓝类别，MERGED后漏/重复映射重试','完整时有效作答1、两类各1，不能把两选当两人；不完整422'),
      (16,'同哈希不同上下文不自动合并',{'题干':'同年龄','上下文':['本人','家庭成员'],'内容哈希':'相同'},'GROUPED检查上下文分组；MERGED缺声明/冲突语义请求','GROUPED区分；同哈希本身不放行MERGED，缺上下文确认422'),
      (16,'非法分箱与统计范围边界',{'bins':[['0','0'],['10','0'],['NaN','10']],'范围':'他人问卷、重复目标、非法ID'},'逐一提交并核对错误及无部分data','非法参数明确拒绝，不生成貌似完整的指标；空样本合法请求均值null'),
      (17,'整卷路径、答案与账号关联异常全集',{'故障':'缺/重复答案、伪造visitedQuestionIds、guard账号错误','位置':'正常候选之后'},'每次仅破坏一种隔离数据后生成报告','按已确认完整性规则整份失败，无部分指标或身份详情，不能回退旧答'),
      (18,'全部新增路由未登录、跨账号及CSRF矩阵',{'身份':['未登录','A','B','C'],'请求':['无CSRF','错误Origin','非法ID/字段']},'按新增路由逐一发读写请求，对照已确认权限表','未登录401，受保护资源越权404，CSRF/Origin按原协议拒绝；无业务副作用，错误含requestId'),
      (18,'幂等键跨载荷/账号与对象ETag矩阵',{'对象':['资源','题库','摘要授权'],'key':'同键不同请求/不同账号'},'对支持幂等的POST重放/换载荷；各对象用错/过期ETag更新','正确重放同结果、异载荷409；账号隔离；错ETag不覆盖；不假定换版PUT有POST幂等机制'),
      (19,'旧数据完整生命周期跨升级',{'旧数据':'未绑定题、已成功答卷、升级前未完成attempt'},'备份/升级后执行ADOPT、改版、恢复、换版、再发布，最后提交旧attempt','旧快照/既有答卷BSON不变，旧attempt按原快照；单卷统计仅因预期新答卷变化'),
      (19,'真实绑定使用投影重建与重跑防重',{'数据':'有真实来源绑定的草稿和历史快照','迁移':'同一计划运行两次'},'按源对象重建使用投影并中断/续跑','业务唯一键和台账防重，投影准确，不按旧题同名或qid推断来源'),
      (18,'摘要关闭/导入失败边界与成功凭据',{'故障':'授权写后/绑定写后中断','key':'同一创建型POST键'},'分别故障注入并查询授权/绑定/使用投影/operation_keys','失败全回滚且无成功凭据；只对真正成功POST允许同键重放'),
      (17,'全递归匿名输出与填写执行白名单',{'输入':'匿名/非匿名卷、共享ADOPT、文本和数字答卷'},'对公开填写定义、跨卷成功/错误响应递归检查字段及敏感样本','无账户关联、参与ID、逐条答案、私有来源/授权；文本只计数，摘要严格9字段'),
    ]
    for mod,title,inp,action,expected in data:
        case(mod,'api',title,inp,[('按具体输入独立准备角色/版本/问卷/基线','记录真实ID/ETag，故障只限随机隔离库'),(action,expected)],[expected],pending=True)


def build():
    ROWS.clear(); SEQ.clear()
    old_path=ROOT/'test/自动化脚本/第一阶段/cases.json'; old=read(old_path)
    originals=copy.deepcopy(old['cases'])
    for c in originals:
        c['phase']=1; c['specificationReuse']='test/自动化脚本/第一阶段/cases.json#'+c['id']
        c['automation']={'state':'implemented' if c['execution']=='api' else 'manual'}
    old_actual=ROOT/'test/执行记录/第一阶段历史/统一目录基线/合并实际结果.json'
    baseline={r['caseId']:{'status':r['status'],'source':'test/执行记录/第一阶段历史/统一目录基线/合并实际结果.json'} for r in read(old_actual)['results']}
    supplement=copy.deepcopy(next(c for c in originals if c['id']=='TC06-A08'))
    supplement.update(id='TC06-M01-AUTO', title='21账号统计计数、最新答卷替换与答案分页（不代验页面）', execution='api',
        input={'账号数':21,'每账号':'先18后20','分页':'limit=20'},
        preconditions=[AUTH,'独立问卷已发布，允许重复填写，21个批次唯一填写者账号。'],
        steps=[{'action':'21账号各提交18再20，查询统计及两页答案','expected':'只贡献21份最新整卷、均值20，分页完整无重漏'}],
        expected=['不据API结果判定TC06-M01页面可读性通过。'],specificationReuse='test/自动化脚本/第一阶段/api/test_submission_statistics.py::test_21_accounts_replaces_manual_workload')
    originals.append(supplement)
    existing(); manuals(); pending()
    junit=ROOT/'test/执行记录/第二阶段/20261029-七项用例加强/phase2-20261029T050119Z-988f3b/junit.xml'
    by_node={r['automation']['node']:r for r in ROWS if r['automation'].get('node')}
    seen=[]
    for t in ET.parse(junit).getroot().iter('testcase'):
        c=by_node[t.attrib['name']];seen.append(c['id'])
        status='failed' if t.find('failure') is not None or t.find('error') is not None else 'skipped' if t.find('skipped') is not None else 'passed'
        baseline[c['id']]={'status':status,'source':junit.relative_to(ROOT).as_posix(),'node':t.attrib['name']}
    assert len(seen)==43==len(by_node)
    implementations = {
        'TC10-A02': ('test_extended_resources.py','test_history_pagination_and_shared_lineage_filter'),
        'TC11-A07': ('test_extended_resources.py','test_explicit_rebind_option_mapping_preserves_execution'),
        'TC11-A08': ('test_extended_resources.py','test_derive_and_bind_atomic_owner_and_source'),
        'TC12-A02': ('test_extended_resources.py','test_bank_rename_remove_multibank_and_pagination'),
        'TC13-A03': ('test_extended_resources.py','test_grant_invalid_accounts_and_private_successors'),
        'TC14-A02': ('test_extended_resources.py','test_three_actor_summary_visibility_and_visible_counts'),
        'TC14-A03': ('test_extended_resources.py','test_usage_stage_versions_and_duplicate_instances'),
        'TC14-A04': ('test_extended_resources.py','test_stale_usage_projection_is_rechecked'),
        'TC16-A04': ('test_extended_statistics.py','test_multiple_complete_mapping_and_response_denominator'),
        'TC16-A05': ('test_extended_statistics.py','test_same_hash_context_needs_explicit_comparison'),
        'TC16-A06': ('test_extended_statistics.py','test_invalid_bins_scope_and_zero_sample_boundaries'),
        'TC17-A08': ('test_extended_statistics.py','test_whole_path_answer_and_account_anomalies_fail_entire_report'),
        'TC17-A09': ('test_extended_statistics.py','test_recursive_privacy_execution_and_summary_allowlists'),
        'TC18-A07': ('test_extended_guards.py','test_all_incremental_route_auth_csrf_and_foreign_objects'),
        'TC18-A08': ('test_extended_guards.py','test_idempotency_payload_actor_and_etag_matrix'),
        'TC18-A09': ('test_extended_guards.py','test_summary_import_interruption_and_success_receipts'),
        'TC19-A11': ('test_extended_migration.py','test_legacy_lifecycle_across_upgrade_keeps_pending_attempt'),
        'TC19-A12': ('test_extended_migration.py','test_real_binding_projection_rebuild_resumes_without_duplicates'),
    }
    for row in ROWS:
        if row['id'] in implementations:
            file, node = implementations[row['id']]
            row['automation'] = {'state':'implemented','node':node}
            row['implementation'] = 'test/自动化脚本/第二阶段/' + file
    allcases=originals+ROWS
    assert len({c['id'] for c in allcases})==len(allcases)
    # {} is a concrete input (e.g. an intentionally empty create body).
    assert all(c['preconditions'] and isinstance(c['input'], dict) and c['steps'] and c['expected'] and all(x['action'] and x['expected'] for x in c['steps']) for c in allcases)
    out={'schemaVersion':2,'catalogVersion':'2026-10-29.4','title':'第二阶段增量与第一阶段回归统一用例',
         'source':{'path':old_path.relative_to(ROOT).as_posix(),'sha256':sha(old_path)},
         'sources':[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in [ROOT/'documents/系统说明/第二阶段/系统说明.md',ROOT/'documents/数据库设计/第二阶段/数据库设计.md',ROOT/'documents/API说明/第二阶段/API说明.md',ROOT/'documents/关键逻辑说明/第二阶段/关键逻辑说明.md']],
         'modules':old['modules']+[{'id':n,'name':name} for n,name in MODULES.items()], 'cases':allcases}
    CATALOG.write_bytes(encoded(out))
    BASELINE.write_bytes(encoded({'kind':'comparison-only-never-current-results','cases':baseline,
         'sources':[{'path':p.relative_to(ROOT).as_posix(),'sha256':sha(p)} for p in [old_actual,junit]],
         'note':'原有失败按稳定caseId比较；同ID通过只说明本轮该测试条件下修复，必须保留规格与代码哈希。'}))
    for n,name in MODULES.items():
        lines=[f'# {n:02d} {name}','','所有实例ID、版本ID、ETag取实际响应；示例变量不是可直接发送的ID。手动由用户操作，API不得人工勾选通过。','']
        for c in [r for r in ROWS if r['moduleId']==n]:
            a=c['automation'];lines += [f"## {c['id']} {c['title']}",'',f"执行方式：**{'手动' if c['execution']=='manual' else 'API自动化'}**；脚本状态：{a['state']}；优先级：{c['priority']}。",'']
            if a.get('node'):lines += [f"现有脚本：`{c['implementation']}::{a['node']}`。",'']
            lines+=['前置条件：','']+['- '+x for x in c['preconditions']]+['','具体输入：','','```json',encoded(c['input']).decode().strip(),'```','','操作步骤与预期输出：','','| 步骤 | 操作 | 预期 |','| --- | --- | --- |']
            lines += [f"| {i} | {x['action'].replace('|','/')} | {x['expected'].replace('|','/')} |" for i,x in enumerate(c['steps'],1)]
            lines += ['','最终检查：','']+['- '+x for x in c['expected']]+['','实际结果：新批次默认未执行，由人工页面或当轮自动化结果填写；不继承历史通过。','']
        (CATALOG.parent/f'{n:02d}_{name.replace("/","、")}.md').write_text('\n'.join(lines),encoding='utf-8')
    counts=Counter((c['phase'],c['execution'],c['automation']['state']) for c in allcases)
    lines=['# 用例填写与执行方式','','第一阶段原142项已完整填写，不重写；另沿用1项多账号自动化补充。第二阶段原43项与新增18项均已编写API脚本；按用户要求移除末七项手工及对应规格，当前第二阶段手工9项。','','| 范围 | 手动 | 已有API | 待编写API |','| --- | ---: | ---: | ---: |','| 第一阶段回归 | 13 | 130 | 0 |','| 第二阶段 | 9 | 61 | 0 |','| 合计 | **22** | **191** | **0** |','','完整清单共213项；新一轮报告应包含全部213项，未执行与未编写不得计为通过。','','第一阶段规格直接复用 [原cases.json](../../自动化脚本/第一阶段/cases.json) 和 [原测试步骤](../第一阶段/README.md)，原始文件保持原字节。统一机器清单见 [cases.json](cases.json)，基线只供比较见 [baseline.json](baseline.json)。','','第二阶段详细用例：','']
    lines += [f'- [{p.stem}]({p.name})' for p in sorted(CATALOG.parent.glob('[0-9][0-9]_*.md'))]
    lines += ['','手动用例以页面体验/截图为主，API验证权限、版本、统计、并发、迁移及持久化；同一主题的两类用例验证不同层面，互不替代。','', '原待编写18项已实现于test_extended_*.py；API必须本轮实跑，工作台禁止人工给API判通过。移除的七项不再出现在当前目录/新报告中；旧导出包和历史执行记录保留。','', '通用失败处理：记录实际请求/错误码、步骤与现象；不降低预期、不修改业务代码。阻塞/跳过必须说明原因，通过/失败必须上传真实截图。']
    (CATALOG.parent/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(dict(counts))
    return out


if __name__=='__main__':
    build()
