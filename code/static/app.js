'use strict';

const app = document.querySelector('#app');
let currentUser = null;
let csrf = '';
let routeGeneration = 0;
const typeNames = {SINGLE: '单选题', MULTIPLE: '多选题', TEXT: '文本填空', NUMBER: '数字填空'};
const stateNames = {DRAFT: '草稿', PUBLISHED: '收集中', CLOSED: '已关闭', EXPIRED: '已截止'};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'class') node.className = value;
    else if (['checked', 'disabled', 'hidden', 'required', 'multiple'].includes(key)) node[key] = value;
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function notify(message, error = false) {
  const area = document.querySelector('#message');
  area.hidden = !message;
  area.textContent = message;
  area.className = error ? 'error' : '';
}

async function api(path, {method = 'GET', data, etag, key} = {}) {
  const headers = {'Accept': 'application/json'};
  if (method !== 'GET') Object.assign(headers, {'Content-Type': 'application/json', 'X-CSRF-Token': csrf});
  if (etag) headers['If-Match'] = etag;
  if (key) headers['Idempotency-Key'] = key;
  let response;
  try {
    response = await fetch('/api/v1' + path, {method, headers, credentials: 'same-origin',
      body: method === 'GET' ? undefined : JSON.stringify(data ?? {})});
  } catch (_) {
    throw new Error('网络连接中断。请保留当前页面重试；提交重试会沿用原请求。');
  }
  const json = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    const details = Array.isArray(json.error?.details) ? json.error.details.map(d => d.questionId ? `题目 ${d.questionId}：${d.code || ''}` : '').join('\n') : '';
    const error = new Error(`${json.error?.message || '请求失败'}${details ? '\n' + details : ''}\n请求编号：${json.requestId || '无'}`);
    error.status = response.status;
    error.code = json.error?.code;
    throw error;
  }
  return {data: json.data, etag: response.headers.get('ETag')};
}

async function action(fn, button) {
  if (button) button.disabled = true;
  try { await fn(); } catch (error) { notify(error.message, true); }
  finally { if (button) button.disabled = false; }
}
function button(label, fn, cls = '') {
  const b = el('button', {type: 'button', class: cls});
  b.textContent = label;
  b.addEventListener('click', () => action(() => fn(b), b));
  return b;
}
function form(fn, ...children) {
  const f = el('form', {}, children);
  f.addEventListener('submit', e => { e.preventDefault(); action(() => fn(f), f.querySelector('[type=submit]')); });
  return f;
}
function submit(label) { return el('button', {type: 'submit', class: 'primary'}, label); }
function input(value = '', type = 'text', attrs = {}) { return el('input', {type, value, ...attrs}); }
function field(label, control) {
  if (!control.hasAttribute('aria-label')) control.setAttribute('aria-label', label);
  return el('label', {class: 'field'}, el('span', {}, label), control);
}
function check(label, checked = false) {
  const control = input('', 'checkbox', {checked});
  return {control, node: el('label', {class: 'check'}, control, label)};
}
function select(options, value) {
  const node = el('select', {}, options.map(([id, label]) => el('option', {value: id}, label)));
  if (value !== undefined) node.value = value;
  return node;
}
function card(...children) { return el('section', {class: 'card'}, children); }
function row(...children) { return el('div', {class: 'row'}, children); }
function muted(text) { return el('p', {class: 'muted'}, text); }
function link(label, href) { return el('a', {href, class: 'button'}, label); }
function navigate(route) { if (location.hash === '#' + route) render(); else location.hash = route; }
function displayTime(value) { return value ? new Date(value).toLocaleString('zh-CN', {hour12: false}) : '未设置'; }
function localTime(value) {
  if (!value) return '';
  const d = new Date(value);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function drawNav() {
  const nav = document.querySelector('#nav');
  nav.replaceChildren();
  if (currentUser) nav.append(el('span', {}, currentUser.username), el('a', {href: '/#surveys'}, '我的问卷'),
    button('退出', async () => { await api('/auth/logout', {method: 'POST'}); currentUser = null; csrf = ''; await render(); }));
  else nav.append(el('span', {}, '登录后创建或填写问卷'));
}

function authPage() {
  const username = input('', 'text', {autocomplete: 'username', maxlength: 64, required: true});
  const password = input('', 'password', {autocomplete: 'current-password', minlength: 8, maxlength: 128, required: true});
  let registering = false;
  const heading = el('h1', {}, '登录');
  const send = submit('登录');
  const toggle = button('还没有账号？注册', () => {
    registering = !registering;
    heading.textContent = registering ? '注册账号' : '登录';
    send.textContent = registering ? '注册并登录' : '登录';
    toggle.textContent = registering ? '已有账号，返回登录' : '还没有账号？注册';
    password.autocomplete = registering ? 'new-password' : 'current-password';
  });
  app.replaceChildren(el('section', {class: 'card narrow'}, heading,
    muted(location.pathname.startsWith('/survey/') || location.hash.startsWith('#fill/') ? '请先登录，随后继续填写该问卷。匿名问卷也需要登录。' : '创建问卷、分享链接，或继续填写。'),
    form(async () => {
      const data = {username: username.value, password: password.value};
      if (registering) await api('/auth/register', {method: 'POST', data});
      const result = await api('/auth/login', {method: 'POST', data});
      currentUser = result.data.user; csrf = result.data.csrfToken;
      notify('登录成功'); await render();
    }, field('用户名', username), field('密码（8～128 个字符）', password), row(send, toggle))));
}

async function surveyList(generation, cursor = null, append = false) {
  const result = await api('/surveys' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''));
  if (generation !== routeGeneration) return;
  let list;
  if (!append) {
    const title = input('', 'text', {required: true, maxlength: 200, placeholder: '例如：课程体验调查'});
    const create = form(async () => {
      const made = await api('/surveys', {method: 'POST', data: {title: title.value}});
      navigate('edit/' + made.data.surveyId);
    }, field('问卷标题', title), submit('新建问卷'));
    const code = input('', 'text', {placeholder: '粘贴问卷分享链接或分享码', required: true});
    const open = form(async () => {
      const value = code.value.trim();
      const match = value.match(/(?:\/survey\/|#fill\/)([A-Za-z0-9_-]+)(?:[?#].*)?$/);
      const publicCode = match ? match[1] : value;
      if (!/^[A-Za-z0-9_-]{16,64}$/.test(publicCode)) throw new Error('请填写有效的分享链接或分享码');
      navigate('fill/' + publicCode);
    }, field('填写他人问卷', code), submit('打开问卷'));
    list = el('div', {id: 'survey-list'});
    app.replaceChildren(el('h1', {}, '我的问卷'), el('div', {class: 'grid'}, card(create), card(open)), list);
  } else list = document.querySelector('#survey-list');
  document.querySelector('#more-surveys')?.remove();
  if (!result.data.items.length && !append) list.append(card(muted('还没有问卷，先新建一份。')));
  for (const s of result.data.items) list.append(card(el('h2', {}, s.title),
    row(el('span', {class: 'badge'}, stateNames[s.effectiveState]), el('span', {}, `生效版本：${s.publishedVersion ?? '尚未发布'}`)),
    muted('最后修改：' + displayTime(s.updatedAt)),
    row(link('编辑与发布', '#edit/' + s.surveyId), link('查看统计', '#stats/' + s.surveyId),
      s.publishedVersion ? link('打开填写页', '/survey/' + s.publicCode) : null)));
  if (result.data.nextCursor) {
    const more = button('加载更多问卷', () => surveyList(generation, result.data.nextCursor, true));
    more.id = 'more-surveys'; list.append(more);
  }
}

async function editor(id, generation) {
  const result = await api('/surveys/' + id);
  if (generation !== routeGeneration) return;
  drawEditor(result.data, result.etag);
}

function drawEditor(s, etag) {
  const base = '/surveys/' + s.surveyId;
  async function mutate(path, method, data) {
    const result = await api(base + path, {method, data, etag});
    if (result.data) drawEditor(result.data, result.etag);
    else await editor(s.surveyId, routeGeneration);
    notify('操作已保存');
  }
  const title = input(s.draft.title, 'text', {required: true, maxlength: 200});
  const description = el('textarea', {maxlength: 4000}, s.draft.description);
  const anonymous = check('匿名：向创建者隐藏填写者身份', s.draft.policy.anonymous);
  const repeat = check('允许重复填写（统计以每账号最后一次成功提交为准）', s.draft.policy.allowRepeat);
  const end = input(localTime(s.deadlineAt), 'datetime-local');
  const settings = form(() => mutate('', 'PATCH', {title: title.value, description: description.value,
    anonymous: anonymous.control.checked, allowRepeat: repeat.control.checked,
    deadlineAt: end.value ? new Date(end.value).toISOString() : null}),
    el('h2', {}, '问卷设置'), field('标题', title), field('说明', description), anonymous.node, repeat.node,
    field('截止时间（留空表示不限时间）', end), submit('保存设置'),
    muted('截止时间保存后立即生效；题目、匿名与重复策略需要点击“发布 / 更新 / 重新开放”后生效。每一部分请单独保存。'));
  const share = input(location.origin + s.shareUrl, 'text', {readonly: true, 'aria-label': '分享链接'});
  const toolbar = card(row(link('返回列表', '#surveys'), link('查看统计', '#stats/' + s.surveyId),
    button('重新加载', () => editor(s.surveyId, routeGeneration))),
    row(el('span', {class: 'badge'}, stateNames[s.effectiveState]),
      el('span', {}, `编辑版本 ${s.definitionVersion} · 生效版本 ${s.publishedVersion ?? '无'}`)),
    row(button('发布 / 更新 / 重新开放', () => mutate('/publish', 'POST', {}), 'primary'),
      s.publishedVersion ? button('关闭收集', () => mutate('/close', 'POST', {}), 'danger') : null),
    field('分享链接（发布后可填写）', share),
    row(button('复制链接', async () => {
      try { await navigator.clipboard.writeText(share.value); notify('链接已复制'); }
      catch (_) { share.focus(); share.select(); notify('请按 Ctrl+C 复制选中的链接'); }
    }), s.publishedVersion ? link('打开填写页', s.shareUrl) : null));
  const questions = el('div');
  s.draft.questions.forEach((q, index) => questions.append(questionEditor(s, q, index, mutate)));
  const kind = select(Object.entries(typeNames));
  const qtitle = input('', 'text', {required: true, maxlength: 500});
  const add = form(() => mutate('/questions', 'POST', {type: kind.value, title: qtitle.value}),
    el('h2', {}, '添加题目'), el('div', {class: 'grid'}, field('题型', kind), field('题干', qtitle)), submit('添加题目'));
  app.replaceChildren(el('h1', {}, '编辑问卷'), toolbar, card(settings), questions, card(add));
}

function questionEditor(s, q, index, mutate) {
  const path = '/questions/' + q.questionId;
  const title = input(q.title, 'text', {maxlength: 500, required: true});
  const required = check('必答题', q.required);
  const optionsBox = el('div');
  function optionRow(option = {}) {
    const label = input(option.label || '', 'text', {maxlength: 200, required: true, 'aria-label': '选项内容'});
    const line = el('div', {class: 'option'}, label, button('移除', () => line.remove(), 'danger'));
    if (option.optionId) line.dataset.optionId = option.optionId;
    optionsBox.append(line);
  }
  (q.options || []).forEach(optionRow);
  const validationBox = el('div', {class: 'grid'});
  const controls = {};
  const names = {minSelections: '最少选择数', maxSelections: '最多选择数', exactSelections: '恰好选择数',
    minLength: '最少字符数', maxLength: '最多字符数', minValue: '最小值', maxValue: '最大值'};
  const keys = {SINGLE: [], MULTIPLE: ['minSelections', 'maxSelections', 'exactSelections'], TEXT: ['minLength', 'maxLength'], NUMBER: ['minValue', 'maxValue']}[q.type];
  for (const key of keys) {
    const numeric = key.endsWith('Value');
    const control = input(q.validation[key] ?? '', numeric ? 'text' : 'number', numeric ? {placeholder: '不限（十进制）'} : {min: 0, step: 1, placeholder: '不限'});
    controls[key] = control;
    validationBox.append(field(names[key], control));
  }
  const integer = check('只允许整数', q.validation.integerOnly);
  const content = form(() => {
    const validation = {};
    for (const [key, control] of Object.entries(controls)) if (control.value !== '') validation[key] = key.endsWith('Value') ? control.value.trim() : Number(control.value);
    if (q.type === 'NUMBER') validation.integerOnly = integer.control.checked;
    const data = {title: title.value, required: required.control.checked, validation};
    if (q.options) data.options = [...optionsBox.children].map(line => ({...(line.dataset.optionId ? {optionId: line.dataset.optionId} : {}), label: line.querySelector('input').value}));
    return mutate(path, 'PATCH', data);
  }, field('题干', title), required.node,
    q.options ? el('div', {}, el('h3', {}, '选项'), optionsBox, button('添加选项', () => optionRow())) : null,
    validationBox, q.type === 'NUMBER' ? integer.node : null,
    muted(q.type === 'MULTIPLE' ? '留空表示不额外限制；可选题允许完全不选。' : q.type === 'TEXT' ? '按去除首尾空白后的字符数校验，最多 10000 字。' : q.type === 'NUMBER' ? '数字使用十进制输入；不设置额外的电话或短信校验。' : '请选择一个选项。'),
    submit('保存本题'));
  function move(offset) {
    const ids = s.draft.questions.map(q => q.questionId);
    [ids[index], ids[index + offset]] = [ids[index + offset], ids[index]];
    return mutate('/question-order', 'PUT', {questionIds: ids});
  }
  const section = card(el('h2', {}, `${index + 1}. ${typeNames[q.type]}`),
    content,
    row(index ? button('上移', () => move(-1)) : null,
      index < s.draft.questions.length - 1 ? button('下移', () => move(1)) : null,
      button('删除本题', () => { if (confirm('确认删除这道题？已发布的历史版本会保留。')) return mutate(path, 'DELETE', {}); }, 'danger')));
  if (q.type !== 'TEXT') section.append(ruleEditor(s, q, index, mutate));
  return section;
}

function ruleEditor(s, q, index, mutate) {
  const list = el('div');
  const readRows = [];
  const operatorNames = {EQ: '等于', ANY_OF: '包含任一', ALL_OF: '包含全部', EXACT_SET: '恰好为这些选项', GT: '大于', GTE: '大于等于', LT: '小于', LTE: '小于等于', BETWEEN: '介于两值之间（含端点）'};
  function add(rule = {}) {
    const priority = input(rule.priority ?? ((list.children.length + 1) * 10), 'number', {min: 0, step: 1, required: true});
    const operators = {SINGLE: ['EQ'], MULTIPLE: ['ANY_OF', 'ALL_OF', 'EXACT_SET'], NUMBER: ['EQ', 'GT', 'GTE', 'LT', 'LTE', 'BETWEEN']}[q.type];
    const op = select(operators.map(k => [k, operatorNames[k]]), rule.operator || operators[0]);
    const target = select([...s.draft.questions.slice(index + 1).map((v, n) => [v.questionId, `${index + n + 2}. ${v.title}`]), ['END', '结束问卷']], rule.target || 'END');
    let values;
    const valueBox = el('div');
    if (q.type === 'SINGLE') {
      values = select(q.options.map(o => [o.optionId, o.label]), rule.value);
      valueBox.append(field('条件选项', values));
    } else if (q.type === 'MULTIPLE') {
      values = q.options.map(o => {
        const c = check(o.label, (rule.value || []).includes(o.optionId));
        valueBox.append(c.node); return {id: o.optionId, control: c.control};
      });
    } else {
      const a = input(Array.isArray(rule.value) ? rule.value[0] : rule.value || '', 'text', {required: true});
      const b = input(Array.isArray(rule.value) ? rule.value[1] : '', 'text');
      const second = field('上界', b);
      const sync = () => { second.hidden = op.value !== 'BETWEEN'; b.required = op.value === 'BETWEEN'; };
      op.addEventListener('change', sync); sync();
      valueBox.append(field('数字 / 下界', a), second);
      values = [a, b];
    }
    const line = el('div', {class: 'rule'}, el('div', {class: 'grid'}, field('优先级（数值越小越先判断）', priority), field('条件', op)),
      valueBox, field('跳转到', target), button('移除此规则', () => line.remove(), 'danger'));
    const read = () => ({...(rule.ruleId ? {ruleId: rule.ruleId} : {}), priority: Number(priority.value), operator: op.value,
      target: target.value, value: q.type === 'SINGLE' ? values.value : q.type === 'MULTIPLE' ? values.filter(c => c.control.checked).map(c => c.id) : op.value === 'BETWEEN' ? values.map(c => c.value.trim()) : values[0].value.trim()});
    readRows.push({line, read}); list.append(line);
  }
  q.jumpRules.forEach(add);
  const f = form(() => mutate('/questions/' + q.questionId + '/jump-rules', 'PUT', {rules: readRows.filter(r => r.line.parentNode === list).map(r => r.read())}),
    muted('先保存选项，再配置规则。按优先级依次判断，首条匹配生效；未匹配时进入下一题。只能向后跳转。'),
    list, row(button('添加规则', () => add()), submit('保存全部跳转规则')));
  return el('details', {}, el('summary', {}, `条件跳转（${q.jumpRules.length} 条）`), f);
}

async function fill(publicCode, generation) {
  const {data: p} = await api('/public-surveys/' + publicCode);
  if (generation !== routeGeneration) return;
  app.replaceChildren(el('h1', {}, p.definition.title), card(el('p', {}, p.definition.description),
    muted(`当前版本 ${p.publishedVersion} · 截止时间：${displayTime(p.deadlineAt)}`),
    el('p', {}, p.definition.policy.anonymous ? '本问卷匿名，创建者看不到你的身份。' : '本问卷非匿名，创建者可在答案中看到你的用户名。'),
    el('p', {}, p.definition.policy.allowRepeat ? '允许重复填写；再次提交后，统计以本次完整答案为准。' : '本问卷不允许重复提交。'),
    p.hasSubmitted ? muted('你已经成功提交过本问卷。') : null,
    row(p.resumeAttemptId ? button('继续已保存的填写', async () => {
      const r = await api('/attempts/' + p.resumeAttemptId); drawAttempt(r.data, r.etag, publicCode);
    }, 'primary') : null,
    p.canStart ? button(p.hasSubmitted ? '再次填写' : '开始新的填写', async () => {
      const r = await api('/public-surveys/' + publicCode + '/attempts', {method: 'POST'});
      drawAttempt(r.data, r.etag, publicCode);
    }, 'primary') : null)));
}

function pendingKey(attemptId) { return `survey-submit:${currentUser.userId}:${attemptId}`; }
function storedPending(attemptId) {
  try { return JSON.parse(localStorage.getItem(pendingKey(attemptId))); } catch (_) { return null; }
}
function drawAttempt(a, etag, publicCode, focusQuestion = null) {
  if (a.status === 'SUBMITTED') {
    localStorage.removeItem(pendingKey(a.attemptId));
    app.replaceChildren(el('h1', {}, '问卷已提交'), card(el('p', {}, '提交时间：' + displayTime(a.submittedAt)),
      el('p', {}, '统计以你最后一次成功提交的整份答案为准。'),
      button('返回问卷入口', () => fill(publicCode, routeGeneration))));
    return;
  }
  const questions = a.definition.questions;
  const selected = focusQuestion || a.nextQuestionId;
  const q = questions.find(q => q.questionId === selected);
  const savedValue = q ? a.answers.find(v => v.questionId === q.questionId)?.value : null;
  const panel = el('div');
  const knownIds = new Set([...a.visitedQuestionIds, a.nextQuestionId].filter(Boolean));
  if (!knownIds.size && questions.length) knownIds.add(questions[0].questionId);
  const nav = el('div', {class: 'row progress'}, questions.filter(q => knownIds.has(q.questionId)).map(v => {
    const b = button(String(questions.indexOf(v) + 1), () => drawAttempt(a, etag, publicCode, v.questionId));
    b.setAttribute('aria-label', '修改第 ' + (questions.indexOf(v) + 1) + ' 题');
    b.setAttribute('aria-current', String(v.questionId === selected)); return b;
  }));
  if (q) {
    let read;
    const controls = el('div');
    if (q.type === 'SINGLE' || q.type === 'MULTIPLE') {
      const choices = q.options.map(o => {
        const c = input(o.optionId, q.type === 'SINGLE' ? 'radio' : 'checkbox', {name: 'answer', checked: q.type === 'SINGLE' ? savedValue === o.optionId : (savedValue || []).includes(o.optionId)});
        controls.append(el('label', {class: 'check'}, c, o.label)); return c;
      });
      read = () => q.type === 'SINGLE' ? choices.find(c => c.checked)?.value ?? null : choices.filter(c => c.checked).map(c => c.value);
      if (!q.required) controls.append(button('清空选择', () => choices.forEach(c => { c.checked = false; })));
    } else {
      const c = q.type === 'TEXT' ? el('textarea', {maxlength: 10000, 'aria-label': '文本答案'}, savedValue || '') : input(savedValue ?? '', 'text', {inputmode: 'decimal', 'aria-label': '数字答案'});
      controls.append(c); read = () => q.type === 'NUMBER' ? c.value.trim() || null : c.value;
    }
    async function save(advance) {
      if (storedPending(a.attemptId)) throw new Error('有一次提交结果待确认。请先重试提交或重新加载填写记录，再修改答案。');
      const entries = a.answers.filter(v => v.questionId !== q.questionId).concat({questionId: q.questionId, value: read()});
      const result = await api('/attempts/' + a.attemptId + '/answers', {method: 'PUT', etag, data: {answers: entries}});
      drawAttempt({...a, ...result.data}, result.etag, publicCode, advance ? null : q.questionId);
      notify(result.data.removedQuestionIds.length ? '已保存；跳转路径变化后，无效的后续答案已移除。' : '答案已保存');
    }
    const constraintText = Object.entries(q.validation).map(([key, value]) => ({minSelections: '最少选', maxSelections: '最多选', exactSelections: '恰好选', minLength: '最少字数', maxLength: '最多字数', minValue: '最小值', maxValue: '最大值', integerOnly: '仅整数'}[key] + '：' + value)).join('；');
    panel.append(card(el('h2', {}, `${questions.indexOf(q) + 1}. ${q.title}${q.required ? '（必答）' : '（可选）'}`),
      muted(constraintText || typeNames[q.type]), controls,
      row(button('保存并继续', () => save(true), 'primary'), button('仅保存当前答案', () => save(false))),
      muted('可选题留空并点击“保存并继续”即可跳过。修改前题后，后端会重新计算后续路径。')));
  } else panel.append(card(el('h2', {}, '已完成当前填写路径'), el('p', {}, '可以点击上方题号检查答案，确认后提交。')));
  const pending = storedPending(a.attemptId);
  if (!a.nextQuestionId || pending) panel.append(card(
    pending ? el('p', {}, '有一次提交待确认，重试将使用原幂等键与原版本。') : el('p', {}, '确认提交已保存的整份答案？成功后会替换你此前的统计贡献。'),
    button(pending ? '重试并确认提交结果' : '确认提交问卷', async () => {
      let receipt = storedPending(a.attemptId);
      if (!receipt) {
        receipt = {key: crypto.randomUUID(), etag};
        localStorage.setItem(pendingKey(a.attemptId), JSON.stringify(receipt));
      }
      try {
        const result = await api('/attempts/' + a.attemptId + '/submit', {method: 'POST', key: receipt.key, etag: receipt.etag});
        localStorage.removeItem(pendingKey(a.attemptId));
        drawAttempt({...a, status: 'SUBMITTED', submittedAt: result.data.submittedAt}, etag, publicCode);
        notify('提交成功');
      } catch (error) {
        if (error.status && error.status < 500) localStorage.removeItem(pendingKey(a.attemptId));
        throw error;
      }
    }, 'primary')));
  app.replaceChildren(el('h1', {}, a.definition.title), muted(`填写版本 ${a.publishedVersion} · 已保存时间：${displayTime(a.updatedAt)}`),
    row(button('重新加载已保存记录', async () => { const r = await api('/attempts/' + a.attemptId); drawAttempt(r.data, r.etag, publicCode); }),
      button('返回问卷入口', () => fill(publicCode, routeGeneration))), nav, panel);
}

async function stats(id, generation, selected = '', versionCursor = '') {
  const args = new URLSearchParams();
  if (selected) args.set('publishedVersion', selected);
  if (versionCursor) args.set('versionCursor', versionCursor);
  const [s, result] = await Promise.all([api('/surveys/' + id), api('/surveys/' + id + '/statistics?' + args)]);
  if (generation !== routeGeneration) return;
  const filter = input(selected, 'number', {min: 1, step: 1, placeholder: '留空显示所有版本'});
  const filterForm = form(() => stats(id, generation, filter.value),
    field('按生效版本查看', filter), submit('刷新统计'));
  app.replaceChildren(el('h1', {}, s.data.draft.title + ' · 统计'), row(link('返回编辑', '#edit/' + id), link('问卷列表', '#surveys')),
    card(el('h2', {}, `有效提交人数：${result.data.submittedCount}`),
      muted('先按每个账号取整份最新成功提交，再按版本与题目统计。最新留空或跳题不会沿用旧答案。不同发布版本分别展示。'),
      filterForm, muted('生成时间：' + displayTime(result.data.generatedAt))));
  if (!result.data.versions.length) app.append(card(muted('尚无发布版本。')));
  for (const version of result.data.versions) {
    const box = card(el('h2', {}, `发布版本 ${version.publishedVersion} · ${version.submittedCount} 人`));
    for (const q of version.questions) {
      const section = el('section', {}, el('h3', {}, q.title), muted(`${typeNames[q.type]} · 经过 ${q.reachedCount} 人 · 有效作答 ${q.answeredCount} 人`));
      if (q.options) section.append(el('table', {}, el('thead', {}, el('tr', {}, el('th', {}, '选项'), el('th', {}, '选择人数'))),
        el('tbody', {}, q.options.map(o => el('tr', {}, el('td', {}, o.label), el('td', {}, o.count))))));
      if (q.type === 'NUMBER') section.append(el('p', {}, '平均值：' + (q.average ?? '暂无有效数字答案')));
      if (q.type === 'TEXT' || q.type === 'NUMBER') {
        const listing = el('div');
        let cursor = null;
        const more = button('查看填写内容', async () => {
          const params = new URLSearchParams({publishedVersion: version.publishedVersion, limit: 20});
          if (cursor) params.set('cursor', cursor);
          const r = await api(`/surveys/${id}/questions/${q.questionId}/answers?${params}`);
          if (!cursor) listing.replaceChildren();
          if (!r.data.items.length) listing.append(muted('暂无有效答案'));
          for (const item of r.data.items) listing.append(el('div', {class: 'rule'},
            muted(`${item.anonymous ? '匿名填写者' : item.respondent.username} · ${displayTime(item.submittedAt)}`), el('p', {class: 'answer'}, item.value)));
          cursor = r.data.nextCursor;
          more.textContent = cursor ? '加载更多答案' : '已显示全部答案'; more.hidden = !cursor;
        });
        section.append(more, listing);
      }
      box.append(section);
    }
    app.append(box);
  }
  if (result.data.nextVersionCursor) app.append(button('查看更早的发布版本', () => stats(id, generation, '', result.data.nextVersionCursor)));
}

async function render() {
  const generation = ++routeGeneration;
  drawNav();
  if (!currentUser) { authPage(); return; }
  app.replaceChildren(muted('正在加载…'));
  const route = location.hash.slice(1) || (location.pathname.startsWith('/survey/') ? 'fill/' + location.pathname.split('/')[2] : 'surveys');
  const [name, id] = route.split('/');
  try {
    if (name === 'edit' && id) await editor(id, generation);
    else if (name === 'fill' && id) await fill(id, generation);
    else if (name === 'stats' && id) await stats(id, generation);
    else await surveyList(generation);
  } catch (error) {
    if (generation !== routeGeneration) return;
    if (error.status === 401) { currentUser = null; csrf = ''; drawNav(); authPage(); }
    else app.replaceChildren(card(el('h1', {}, '暂时无法打开'), el('p', {}, error.message), row(button('重新加载', render), link('返回我的问卷', '/#surveys'))));
    notify(error.message, true);
  }
}

window.addEventListener('hashchange', () => { notify(''); render(); });
(async () => {
  try { const r = await api('/auth/me'); currentUser = r.data.user; csrf = r.data.csrfToken; }
  catch (error) { if (error.status !== 401) notify(error.message, true); }
  await render();
})();
