'use strict';

// Incremental screens. Existing auth, editor, filling and single-survey metrics stay in app.js.
const Phase2 = (() => {
  const summaryFields = '问卷标识、标题、归属（本人/他人）、资源和版本标识、发布版本、使用阶段、问卷状态、实例数量';
  const names = {measure: '测量说明', unit: '单位', timeBasis: '时间口径', population: '适用人群', notes: '补充说明'};
  let available = false;
  async function capability() {
    available = (await api('/capabilities')).data.phase2Available;
    return available;
  }
  function nav(node) {
    if (available) node.append(...[['我的题目', 'resources'], ['共享给我', 'shared'], ['题库', 'banks'], ['跨卷统计', 'cross']]
      .map(([label, route]) => el('a', {href: '/#' + route}, label)));
  }
  function pendingPrefix() { return 'phase2-pending:' + currentUser.userId + ':'; }
  async function post(path, data, tag) {
    const storageKey = pendingPrefix() + path;
    const signature = JSON.stringify({data, tag});
    let pending;
    try { pending = JSON.parse(sessionStorage.getItem(storageKey)); } catch (_) { /* no receipt */ }
    if (pending && pending.signature !== signature) throw new Error('此操作上次结果尚未确认，请使用“重试未确认请求”，确认后再修改内容。');
    if (!pending) {
      pending = {path, data, tag, signature, key: crypto.randomUUID()};
      sessionStorage.setItem(storageKey, JSON.stringify(pending));
    }
    try {
      const result = await api(path, {method: 'POST', data, etag: tag, key: pending.key});
      sessionStorage.removeItem(storageKey);
      return result;
    } catch (e) {
      if (e.status && e.status < 500) sessionStorage.removeItem(storageKey);
      throw e;
    }
  }
  async function retryPending() {
    const prefix = pendingPrefix();
    for (const key of Object.keys(sessionStorage).filter(k => k.startsWith(prefix))) {
      const p = JSON.parse(sessionStorage.getItem(key));
      await post(p.path, p.data, p.tag);
    }
    notify('未确认请求已核实，重新加载最新内容'); await render();
  }
  function heading(title) {
    return row(el('h1', {}, title), button('重试未确认请求', retryPending));
  }
  async function all(path, fieldName = 'items') {
    const rows = []; let cursor;
    do {
      const r = (await api(path + (path.includes('?') ? '&' : '?') + 'limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''))).data;
      rows.push(...r[fieldName]); cursor = r.nextCursor;
    } while (cursor);
    return rows;
  }
  function showContent(c) {
    return el('div', {}, el('h3', {}, c.title), muted(typeNames[c.type]),
      c.options ? el('ol', {}, c.options.map(o => el('li', {}, o.label))) : null,
      el('p', {class: 'muted'}, Object.entries(c.semantics || {}).map(([k, v]) => `${names[k]}：${v}`).join('；')));
  }
  function contentEditor(original = {type: 'NUMBER', title: '', validation: {}}, onSave, label = '保存') {
    const c = structuredClone(original), type = select(Object.entries(typeNames), c.type);
    const title = input(c.title, 'text', {required: true, maxlength: 500});
    const details = el('div'); let read;
    function draw() {
      const same = c.type === type.value, validation = same ? c.validation || {} : {};
      const controls = {}, options = el('div');
      function add(o = {}) {
        const value = input(o.label || '', 'text', {required: true, maxlength: 200, 'aria-label': '资源选项'});
        const line = row(value, button('移除选项', () => line.remove()));
        if (o.semanticOptionId) line.dataset.semanticId = o.semanticOptionId;
        options.append(line);
      }
      const kind = type.value;
      const labels = {minValue: '最小值', maxValue: '最大值', minLength: '最少字符', maxLength: '最多字符',
        minSelections: '最少选择数', maxSelections: '最多选择数', exactSelections: '恰好选择数'};
      const keys = {NUMBER: ['minValue', 'maxValue'], TEXT: ['minLength', 'maxLength'], SINGLE: [],
        MULTIPLE: ['minSelections', 'maxSelections', 'exactSelections']}[kind];
      const grid = el('div', {class: 'grid'});
      keys.forEach(k => {
        controls[k] = input(validation[k] ?? '', k.endsWith('Value') ? 'text' : 'number', {placeholder: '不限'});
        grid.append(field(labels[k], controls[k]));
      });
      const integer = check('只允许整数', validation.integerOnly);
      details.replaceChildren(grid);
      if (kind === 'NUMBER') details.append(integer.node);
      if (['SINGLE', 'MULTIPLE'].includes(kind)) {
        (same && c.options?.length ? c.options : [{label: ''}, {label: ''}]).forEach(add);
        details.append(options, button('添加资源选项', () => add()));
      }
      read = () => {
        const value = {};
        Object.entries(controls).forEach(([k, v]) => { if (v.value !== '') value[k] = k.endsWith('Value') ? v.value.trim() : Number(v.value); });
        if (kind === 'NUMBER') value.integerOnly = integer.control.checked;
        const result = {type: kind, title: title.value, validation: value};
        if (['SINGLE', 'MULTIPLE'].includes(kind)) result.options = [...options.children].map(line => ({label: line.querySelector('input').value,
          ...(line.dataset.semanticId ? {semanticOptionId: line.dataset.semanticId} : {})}));
        return result;
      };
    }
    type.addEventListener('change', draw); draw();
    const semantic = el('div', {class: 'grid'}), semantics = {};
    Object.entries(names).forEach(([k, v]) => {
      semantics[k] = input(c.semantics?.[k] || '', 'text', {maxlength: k === 'notes' ? 2000 : k === 'unit' ? 128 : 500});
      semantic.append(field(v, semantics[k]));
    });
    const readContent = () => ({...read(), semantics: Object.fromEntries(Object.entries(semantics).filter(([, v]) => v.value.trim()).map(([k, v]) => [k, v.value.trim()]))});
    const node = form(() => onSave(readContent()), field('资源题型', type), field('资源题干', title), details,
      el('details', {open: true}, el('summary', {}, '测量口径（跨卷合并时需要明确）'), semantic), submit(label));
    return {node, read: readContent};
  }
  async function resources(shared = false) {
    const list = await all('/question-resources?scope=' + (shared ? 'SHARED' : 'OWN'));
    const boxes = [];
    for (const r of list) {
      const versions = await all(`/question-resources/${r.resourceId}/versions`);
      boxes.push(card(el('h2', {}, versions[0]?.content.title || r.workingDraft?.title || '私有工作稿'),
        muted(shared ? `已获授权 ${versions.length} 个版本` : `固定版本 ${r.versionCounter} 个`),
        link('内容、历史与授权', '#resource/' + r.resourceId)));
    }
    app.replaceChildren(heading(shared ? '共享给我' : '我的题目'),
      muted('固定版本不会被覆盖。问卷选用后，题目更新、恢复及题库更新均不会自动改动已选实例。'), ...boxes);
    if (!boxes.length) app.append(card(muted('暂无题目')));
    if (!shared) {
      const fixed = check('直接固定为可选版本', true);
      const edit = contentEditor(undefined, async content => {
        const r = await post('/question-resources', {content, fixed: fixed.control.checked});
        navigate('resource/' + r.data.resourceId);
      }, '创建独立题目');
      app.append(card(el('h2', {}, '新建独立题目'), fixed.node, edit.node));
    }
  }
  async function resourcePage(rid) {
    const {data: r, etag: tag} = await api('/question-resources/' + rid);
    const versions = await all(`/question-resources/${rid}/versions`);
    const own = r.ownership === 'SELF';
    app.replaceChildren(heading(own ? '题目内容与版本历史' : '获授权的题目版本'),
      row(link('我的题目', '#resources'), link('共享给我', '#shared')),
      muted(`题目身份：${rid}。历史恢复会生成新版本；派生建立归当前账号所有的新题目和版本链。`));
    if (own) {
      const initial = r.workingDraft || versions[0]?.content;
      const working = contentEditor(initial, async content => {
        await api(`/question-resources/${rid}/draft`, {method: 'PATCH', data: {content}, etag: tag});
        notify('私有工作稿已保存'); await resourcePage(rid);
      }, '保存私有工作稿');
      app.append(card(el('h2', {}, '工作稿 / 生成后继版本'), working.node,
        button(versions.length ? '将当前内容固定为新版本' : '固定初版', async () => {
          await post(`/question-resources/${rid}/versions`, {content: working.read(), ...(r.latestVersionId ? {baseVersionId: r.latestVersionId} : {})}, tag);
          notify('已创建固定版本，已发布内容保持不变'); await resourcePage(rid);
        }, 'primary')));
    }
    for (const v of versions) {
      const box = card(el('h2', {}, `版本 ${v.versionNo} · ${v.originKind}`), showContent(v.content),
        muted(`版本身份：${v.questionVersionId}；上级：${v.parentVersionId || '初版'}`),
        v.derivedFrom ? muted(`派生来源：${v.derivedFrom.resourceId} / ${v.derivedFrom.questionVersionId}`) : null,
        v.restoredFromVersionId ? muted('恢复来源：' + v.restoredFromVersionId) : null,
        row(button('选用到问卷', () => chooseSurvey(rid, v.questionVersionId)), button('派生为我的题目', async () => {
          const made = await post(`/question-resources/${rid}/versions/${v.questionVersionId}/forks`, {});
          navigate('resource/' + made.data.resourceId);
        })));
      if (v.adoptionSource) box.append(el('details', {}, el('summary', {}, 'ADOPT提取前证据（仅所有者）'),
        el('pre', {}, JSON.stringify(v.adoptionSource, null, 2))));
      if (own) {
        box.append(button('从此历史版本恢复', async () => {
          if (!confirm('恢复会新增版本，不覆盖现有固定版本或问卷。继续？')) return;
          await post(`/question-resources/${rid}/restorations`, {baseVersionId: r.latestVersionId, restoreFromVersionId: v.questionVersionId}, tag);
          await resourcePage(rid); notify('恢复已生成新版本');
        }));
        const username = input('', 'text', {required: true, maxlength: 64});
        box.append(el('details', {}, el('summary', {}, '按账号授权此版本'),
          muted('仅授予此版本的读取、选用、派生权；不授予问卷或答案访问权。'),
          form(async () => {
            await api(`/question-resources/${rid}/versions/${v.questionVersionId}/grants`, {method: 'PUT', data: {username: username.value}, noneMatch: true});
            notify('此版本已共享');
          }, field('接收者的完整用户名', username), submit('授权使用')),
          button('查看已有授权', async () => {
            const grants = await all(`/question-resources/${rid}/versions/${v.questionVersionId}/grants`);
            grantBox.replaceChildren(...grants.map(g => row(muted(`接收者：${g.username || g.granteeId}；权限：${g.actions.join('、')}`),
              g.username ? button('确认保留此授权', async () => {
                await api(`/question-resources/${rid}/versions/${v.questionVersionId}/grants`, {method: 'PUT', data: {username: g.username}, etag: g.etag});
                notify('授权已确认；再次操作请重新加载已有授权');
              }) : null)));
            if (!grants.length) grantBox.append(muted('尚无授权'));
          }), el('div', {id: 'grants-' + v.questionVersionId})));
        const grantBox = box.querySelector('#grants-' + v.questionVersionId);
      }
      app.append(box);
    }
    app.append(button('查看使用摘要', () => usages(rid)));
  }
  async function chooseSurvey(rid, vid) {
    const surveys = await all('/surveys');
    const picker = select(surveys.map(s => [s.surveyId, s.title]));
    const consent = check('我已阅读下列字段，同意向题目所有者公开本问卷的最小使用摘要', false);
    app.replaceChildren(heading('选用到问卷'), card(field('目标问卷', picker),
      muted('跨账号选用时需授权 SUMMARY_V1：' + summaryFields + '。不包含分享链接、填写者或答案。'), consent.node,
      button('确认选用', async () => {
        if (!picker.value) throw new Error('请先创建问卷');
        const s = await api('/surveys/' + picker.value);
        const data = {resourceId: rid, questionVersionId: vid};
        if (consent.control.checked) data.usageSummaryConsent = {accepted: true, policyVersion: 'SUMMARY_V1'};
        await post(`/surveys/${picker.value}/question-imports`, data, s.etag);
        navigate('edit/' + picker.value);
      }, 'primary')));
  }
  async function picker(s, tag, q = null) {
    const list = el('div'), scope = select([['OWN', '我的题目'], ['SHARED', '共享给我'], ['BANK', '从题库选择']]);
    const bankSelect = select((await all('/question-banks')).map(b => [b.bankId, b.name]));
    const consent = check('同意向原题目所有者公开此问卷的最小使用摘要（跨账号使用时）');
    const mapping = el('textarea', {placeholder: '仅需要显式映射时填写 JSON 数组，参见下方说明', 'aria-label': '显式选项映射'});
    async function load() {
      bankSelect.hidden = scope.value !== 'BANK'; list.replaceChildren(muted('加载中…'));
      let entries = [];
      if (scope.value === 'BANK') {
        if (bankSelect.value) entries = await all('/question-banks/' + bankSelect.value, 'members');
      } else {
        for (const r of await all('/question-resources?scope=' + scope.value)) {
          entries.push(...(await all(`/question-resources/${r.resourceId}/versions`)).map(v => ({...v, resourceId: r.resourceId})));
        }
      }
      list.replaceChildren();
      for (const e of entries) {
        const v = e.content ? e : (await api(`/question-resources/${e.resourceId}/versions/${e.questionVersionId}`)).data;
        list.append(card(showContent(v.content), muted('固定版本 ' + v.versionNo), button(q ? '明确换用此版本' : '选用此版本', async () => {
          const data = {resourceId: e.resourceId, questionVersionId: e.questionVersionId};
          if (consent.control.checked) data.usageSummaryConsent = {accepted: true, policyVersion: 'SUMMARY_V1'};
          if (mapping.value.trim()) data.optionMap = JSON.parse(mapping.value);
          const path = `/surveys/${s.surveyId}/` + (q ? `questions/${q.questionId}/binding` : 'question-imports');
          if (q) await api(path, {method: 'PUT', data, etag: tag}); else await post(path, data, tag);
          navigate('edit/' + s.surveyId); notify('选用已保存，请检查本卷必答与跳转后发布');
        }, 'primary')));
      }
      if (!entries.length) list.append(muted('暂无可选固定版本，请先创建或获取共享授权'));
    }
    scope.addEventListener('change', () => action(load)); bankSelect.addEventListener('change', () => action(load));
    app.replaceChildren(heading(q ? '显式换版 / 改绑' : '从独立题目选用'), link('返回编辑', '#edit/' + s.surveyId),
      card(field('选用范围', scope), field('题库', bankSelect), muted('SUMMARY_V1字段：' + summaryFields), consent.node,
        q ? el('details', {}, el('summary', {}, '高级：选项映射'),
          muted('仅按明确语义映射，禁止按文字猜测。格式：[ {"semanticOptionId":"目标版本语义选项ID","localOptionId":"原本卷选项ID"} ]。规则不适用会整体拒绝。'), field('显式选项映射', mapping),
          el('pre', {}, JSON.stringify(q.options || [], null, 2))) : null), list);
    await load();
  }
  function editorTools(s, tag) {
    if (!available) return card(muted('当前为第一阶段兼容模式；使用新增功能前请按 README_PHASE2 显式升级数据库。'));
    return card(row(button('从我的题目 / 共享 / 题库选用', () => picker(s, tag)), button('管理使用摘要授权', () => summaryPage(s))));
  }
  function questionTools(s, q, tag) {
    if (!available) return null;
    if (!q.reuseBinding) return button('保存为常用题并绑定（ADOPT）', async () => {
      if (!confirm('将保存当前实例的提取前证据并创建独立题目，历史发布保持原状。继续？')) return;
      await post(`/surveys/${s.surveyId}/questions/${q.questionId}/adoption`, {}, tag);
      await editor(s.surveyId, routeGeneration); notify('已保存并绑定独立初版');
    });
    return el('div', {class: 'p2-binding'}, muted('已绑定固定题目版本；本页只修改本卷必答、顺序和跳转。'),
      row(link('历史 / 版本', '#resource/' + q.reuseBinding.resourceId), button('显式换版', () => picker(s, tag, q)),
        button('修改内容并派生改绑', async () => {
          const v = (await api(`/question-resources/${q.reuseBinding.resourceId}/versions/${q.reuseBinding.questionVersionId}`)).data;
          const edit = contentEditor(v.content, async content => {
            await post(`/surveys/${s.surveyId}/questions/${q.questionId}/derivations`, {content}, tag);
            navigate('edit/' + s.surveyId);
          }, '创建自己的派生题并改绑');
          app.replaceChildren(heading('派生并改绑'), link('返回编辑', '#edit/' + s.surveyId),
            card(muted('保留原来源；新资源和版本链归你所有。跳转失效则整体拒绝，请先调整原跳转。'), edit.node));
        })));
  }
  async function banks() {
    const list = await all('/question-banks'), name = input('', 'text', {maxlength: 200, required: true});
    app.replaceChildren(heading('我的题库'), ...list.map(b => card(el('h2', {}, b.name), link('组织与版本管理', '#bank/' + b.bankId))),
      card(form(async () => { const r = await post('/question-banks', {name: name.value}); navigate('bank/' + r.data.bankId); },
        field('新题库名称', name), submit('创建题库'))));
  }
  async function bankPage(bid) {
    const {data: b, etag: tag} = await api('/question-banks/' + bid);
    const members = await all('/question-banks/' + bid, 'members'), name = input(b.name, 'text', {required: true, maxlength: 200});
    app.replaceChildren(heading(b.name), link('返回题库', '#banks'), card(form(async () => {
      await api('/question-banks/' + bid, {method: 'PATCH', data: {name: name.value}, etag: tag}); await bankPage(bid);
    }, field('题库名称', name), submit('保存名称')), button('删除此题库', async () => {
      if (!confirm('只软删除题库，不删除资源、版本或问卷。继续？')) return;
      await api('/question-banks/' + bid, {method: 'DELETE', etag: tag}); navigate('banks');
    }, 'danger')));
    for (const m of members) {
      const v = (await api(`/question-resources/${m.resourceId}/versions/${m.questionVersionId}`)).data;
      app.append(card(showContent(v.content), muted('当前指定版本 ' + v.versionNo), button('移出题库', async () => {
        await api(`/question-banks/${bid}/members/${m.resourceId}`, {method: 'DELETE', etag: tag}); await bankPage(bid);
      })));
    }
    const entries = [];
    for (const scope of ['OWN', 'SHARED']) for (const r of await all('/question-resources?scope=' + scope)) {
      entries.push(...(await all(`/question-resources/${r.resourceId}/versions`)).map(v => ({...v, resourceId: r.resourceId})));
    }
    const choice = select(entries.map((v, i) => [i, v.content.title + ' · v' + v.versionNo + ' · ' + v.resourceId.slice(-6)]));
    app.append(card(field('添加 / 更新成员的指定版本', choice), button('保存题库成员版本', async () => {
      const v = entries[Number(choice.value)]; if (!v) throw new Error('暂无可选题目');
      await api(`/question-banks/${bid}/members/${v.resourceId}`, {method: 'PUT', data: {questionVersionId: v.questionVersionId}, etag: tag});
      await bankPage(bid); notify('题库已更新，问卷已选实例不自动更新');
    })));
  }
  async function usages(rid, cursor = null) {
    const r = (await api(`/question-resources/${rid}/usages?limit=20` + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''))).data;
    app.replaceChildren(heading('题目使用摘要'), link('返回题目', '#resource/' + rid),
      muted(`范围：本人及获授权的他人使用；当前可见分组 ${r.totalVisible}。字段：${summaryFields}。`),
      el('table', {}, el('thead', {}, el('tr', {}, ...['问卷', '归属', '发布版本', '阶段', '状态', '实例数'].map(x => el('th', {}, x)))),
        el('tbody', {}, r.items.map(x => el('tr', {}, ...[x.surveyTitle, x.ownership === 'SELF' ? '本人' : '已授权他人', x.publishedVersion ?? '草稿', x.usageStage, x.surveyStatus, x.instanceCount].map(v => el('td', {}, v)))))),
      r.nextCursor ? button('下一页', () => usages(rid, r.nextCursor)) : null);
  }
  async function summaryPage(s) {
    const previous = await all(`/surveys/${s.surveyId}/usage-summary-grants`);
    const resources = [...new Set([...s.draft.questions.map(q => q.reuseBinding?.resourceId).filter(Boolean), ...previous.map(g => g.resourceId)])];
    app.replaceChildren(heading('本问卷使用摘要授权'), link('返回编辑', '#edit/' + s.surveyId), muted('SUMMARY_V1字段：' + summaryFields));
    for (const rid of resources) {
      const r = (await api('/question-resources/' + rid)).data;
      if (r.ownership === 'SELF') continue;
      const path = `/surveys/${s.surveyId}/usage-summary-grants/${rid}`;
      let grant = null; try { grant = await api(path); } catch (e) { if (e.status !== 404) throw e; }
      const enabled = check('允许题目所有者查看此问卷的上述使用摘要', grant?.data.enabled || false);
      app.append(card(muted('资源：' + rid), enabled.node, button('保存摘要授权', async () => {
        await api(path, {method: 'PUT', data: {enabled: enabled.control.checked, policyVersion: 'SUMMARY_V1'},
          etag: grant?.etag, noneMatch: !grant});
        notify('授权已更新'); await summaryPage(s);
      })));
    }
    app.append(muted('显示当前草稿及已有摘要授权的他人资源。关闭摘要不删除问卷或历史绑定；后续再次导入须重新明确确认。'));
  }
  async function cross() {
    const surveys = await all('/surveys'), versions = [];
    for (const scope of ['OWN', 'SHARED']) for (const r of await all('/question-resources?scope=' + scope)) {
      versions.push(...(await all(`/question-resources/${r.resourceId}/versions`)).map(v => ({...v, resourceId: r.resourceId})));
    }
    const surveyChoices = surveys.map(s => ({id: s.surveyId, c: check(s.title)}));
    const targets = versions.map(v => ({v, c: check(v.content.title + ' · v' + v.versionNo + ' · ' + v.resourceId.slice(-6))}));
    const mode = select([['GROUPED', '按版本与上下文分组'], ['MERGED', '明确比较依据后合并']]);
    const rationale = input('', 'text', {maxlength: 2000}), confirmed = check('已核对题意、测量口径及前置路径，上下文具有可比性');
    const advanced = el('textarea', {rows: 7, 'aria-label': '比较与实例选择附加配置', placeholder: '可选：JSON对象，填写 categories、optionMappings、semanticDeclarations、bins、instanceSelectors'});
    const results = el('div');
    app.replaceChildren(heading('跨问卷单题统计'), muted('仅统计自己的问卷。各卷先取账号最新成功整卷，再判断目标题目及实际路径；同账号跨卷各计一次。只输出聚合指标。'),
      card(form(async () => {
        const data = {targets: targets.filter(t => t.c.control.checked).map(t => ({resourceId: t.v.resourceId, questionVersionId: t.v.questionVersionId})),
          surveyIds: surveyChoices.filter(t => t.c.control.checked).map(t => t.id), mode: mode.value};
        const extra = advanced.value.trim() ? JSON.parse(advanced.value) : {};
        if (extra.instanceSelectors) data.instanceSelectors = extra.instanceSelectors;
        delete extra.instanceSelectors;
        if (mode.value === 'MERGED') data.comparisonSpec = {...extra, rationale: rationale.value, contextConfirmed: confirmed.control.checked};
        else if (Object.keys(extra).length) data.comparisonSpec = extra;
        results.replaceChildren(muted('正在检查完整性并统计…'));
        try {
          const report = (await api('/question-statistics/cross-survey', {method: 'POST', data})).data;
          results.replaceChildren(card(el('h2', {}, '统计结果'), muted(`最新成功整卷：${report.latestWholeResponseCount} 份；模式：${report.mode}`),
            muted('各问卷各贡献一次；独立账号数在各分组内去重。没有数值样本时均值为空。')),
            ...report.groups.map((g, index) => card(el('h2', {}, `分组 ${index + 1} · ${typeNames[g.type]}`),
              row(el('span', {class: 'badge'}, `有效作答 ${g.answeredParticipationCount} 次`),
                el('span', {class: 'badge'}, `独立账号 ${g.distinctAnsweredAccountCount} 个`)),
              muted(`实际到达 ${g.reachedCount} 次；目标版本：${g.questionVersionIds.join('、')}`),
              g.type === 'NUMBER' ? el('h3', {}, '平均值：' + (g.average ?? '暂无数值样本')) : null,
              g.options ? el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, '选项'), el('th', {}, '次数'))),
                el('tbody', {}, g.options.map(o => el('tr', {}, el('td', {}, o.label), el('td', {}, o.count))))) : null,
              g.distribution?.kind === 'FREQUENCY' ? el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, '数值'), el('th', {}, '频数'))),
                el('tbody', {}, g.distribution.items.map(o => el('tr', {}, el('td', {}, o.value), el('td', {}, o.count))))) : null,
              g.distribution?.kind === 'BINS' ? muted('分箱边界：' + g.distribution.boundaries.join(' / ') + '；各区间计数：' + g.distribution.counts.join(' / ')) : null)),
            card(muted('排除原因：' + (Object.entries(report.exclusions).map(([k, v]) => `${k} ${v}`).join('；') || '无')),
              el('details', {}, el('summary', {}, '统计范围与比较依据'), el('pre', {}, JSON.stringify(report, null, 2)))));
        } catch (e) { results.replaceChildren(card(muted('未生成报告；没有展示部分统计结果。'))); throw e; }
      }, el('h2', {}, '1. 选择明确的目标版本'), ...targets.map(t => t.c.node),
        el('h2', {}, '2. 选择本人问卷'), ...surveyChoices.map(t => t.c.node),
        field('3. 统计模式', mode), field('合并比较依据', rationale), confirmed.node,
        el('details', {}, el('summary', {}, '高级：显式选项映射、语义声明、分箱及重复实例选择'),
          muted('合并选择题必须填写完整映射。数值bins为十进制字符串边界数组；不支持单位换算。实例选择格式：[{surveyId,publishedVersion,questionId}]。'),
          el('pre', {}, JSON.stringify({categories: [{categoryId: 'YES', label: '是'}], optionMappings: [{questionVersionId: '版本ID', mapping: [{semanticOptionId: '语义选项ID', categoryId: 'YES'}]}]}, null, 2)),
          field('比较与实例选择附加配置', advanced),
          el('pre', {}, JSON.stringify(versions.map(v => ({resourceId: v.resourceId, questionVersionId: v.questionVersionId, title: v.content.title, options: v.content.options})), null, 2))), submit('检查并生成统计'))), results);
  }
  async function route(name, id) {
    if (!['resources', 'shared', 'resource', 'banks', 'bank', 'cross'].includes(name)) return false;
    if (!available) throw new Error('第二阶段功能需要显式升级数据库。原问卷功能可继续使用。');
    if (name === 'resources' || name === 'shared') await resources(name === 'shared');
    else if (name === 'resource') await resourcePage(id);
    else if (name === 'banks') await banks();
    else if (name === 'bank') await bankPage(id);
    else await cross();
    return true;
  }
  return {capability, nav, route, editorTools, questionTools};
})();
