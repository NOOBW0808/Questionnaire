'use strict';
const $ = s => document.querySelector(s);
const labels = {not_run:'未执行', passed:'通过', failed:'失败', blocked:'阻塞', skipped:'跳过'};
const state = {token:'', catalog:null, baseline:null, run:null, current:null, dirty:false, busy:false};

function element(tag, content, className) {
  const el = document.createElement(tag);
  if (content !== undefined) el.textContent = content;
  if (className) el.className = className;
  return el;
}
function message(text, error=false) { $('#message').textContent = text; $('#message').className = error ? 'error' : ''; }
async function request(path, options={}) {
  options.headers = {...options.headers, 'X-Collector-Token':state.token};
  if (options.body && !(options.body instanceof FormData)) {
    options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(options.body);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `请求失败 ${response.status}`);
  return data;
}
async function action(fn) {
  if (state.busy) return;
  state.busy=true;
  try { await fn(); } catch (error) { message(error.message, true); }
  finally { state.busy=false; }
}
function catalog() { return state.run?.catalog || state.catalog; }
function caseById(id) { return catalog().cases.find(c=>c.id===id); }
function row(id) { return state.run?.results[id] || {status:'not_run', actual:'', attachments:[]}; }
function old(id) {return (state.run?.baseline || state.baseline)?.cases[id]?.status || '无基线';}
function delta(status, previous) {
 if(status==='not_run') return '未执行'; if(status==='skipped')return '跳过项'; if(status==='blocked')return '阻塞';
 if(status==='failed')return ['failed','broken'].includes(previous)?'原有失败':'新增失败';
 return ['failed','broken'].includes(previous)?'已修复项':'本轮通过';
}
function autoText(c) {return c.automation?.state==='implemented'?'API 自动化 · 已有脚本':'API 自动化 · 待编写脚本';}
function guard() { return !state.dirty || window.confirm('当前判定或实际结果尚未保存。继续会放弃这些文字修改，已上传截图仍保留。'); }
function setDirty(value) {
  state.dirty=value;
  if ($('#save-state')) $('#save-state').textContent=value ? '有未保存修改' : '判定与文字已保存';
}
function moduleOptions() {
  const target=$('#module'), selected=target.value;
  target.replaceChildren(new Option('全部模块',''));
  for (const m of catalog().modules) target.add(new Option(`${m.id}. ${m.name}`,m.id));
  target.value=selected;
}
function summary() {
  const comparison=$('#comparison'); if(comparison && state.current) comparison.textContent=`历史：${labels[old(state.current)] || old(state.current)} · 本轮：${labels[row(state.current).status]} · 对比：${delta(row(state.current).status,old(state.current))}。历史结果只供比较。`;
  const target=$('#progress'); target.replaceChildren();
  const counts=Object.fromEntries(Object.keys(labels).map(k=>[k,0]));
  for (const c of catalog().cases.filter(c=>c.execution==='manual')) counts[row(c.id).status]++;
  for (const [key,count] of Object.entries(counts)) target.append(element('span',`${labels[key]} ${count}`,`pill ${key}`));
  target.append(element('span',`API 已有脚本 ${catalog().cases.filter(c=>c.execution==='api'&&c.automation.state==='implemented').length} · 待编写 ${catalog().cases.filter(c=>c.automation.state==='not_implemented').length}`,'pill'));
  target.append(element('span','历史通过不计入本轮','pill')); 
  document.querySelectorAll('[data-export]').forEach(b=>b.disabled=!state.run);
  if (state.run) {
    const r=state.run;
    $('#allure-command').textContent=`& './.venv/Scripts/python.exe' 'test/第二阶段/run_allure_cycle.py' --manual-run '${r.id}' --allure 'D:/allure-2.45.0/bin/allure.bat'`;
    $('#batch-meta').textContent=`执行人：${r.tester} · 被测地址：${r.targetUrl} · ${r.environment} · 批次ID：${r.id} · 目录版本：${r.catalog.catalogVersion} · 最新保存：${new Date(r.updatedAt).toLocaleString()}`;
  }
}
function renderList() {
  const list=$('#case-list'); list.replaceChildren();
  const items=catalog().cases.filter(c=>(!$('#module').value || c.moduleId===Number($('#module').value)) &&
    (!$('#phase').value || c.phase===Number($('#phase').value)) && (!$('#mode').value || c.execution===$('#mode').value) && (!$('#status-filter').value || row(c.id).status===$('#status-filter').value) &&
    (`${c.id} ${c.title}`.toLowerCase().includes($('#search').value.toLowerCase())));
  $('#case-count').textContent=`${items.length} 项`;
  for (const c of items) {
    const button=element('button',undefined,'case-item'+(state.current===c.id?' selected':''));
    button.type='button'; button.dataset.caseId=c.id;
    const code=element('span',undefined,'code'); code.append(element('span',c.id));
    code.append(element('span',c.execution==='api'?(c.automation.state==='implemented'?'已有脚本':'待编写'):labels[row(c.id).status],`badge ${row(c.id).status}`));
    button.append(code,element('span',c.title));
    button.addEventListener('click',()=>{if(state.busy || !guard()) return; state.current=c.id;setDirty(false);renderList();renderDetail();});
    list.append(button);
  }
  if (!items.length) list.append(element('p','没有匹配的用例','muted'));
}
function section(container, title, entries, ordered=true) {
  container.append(element('h3', title));
  const list=element(ordered?'ol':'ul');
  for (const entry of entries) list.append(element('li', entry));
  container.append(list);
}
function renderAttachments() {
  const target=$('#attachments'); if(!target) return;
  target.replaceChildren();
  for (const attachment of row(state.current).attachments) {
    const card=element('div',undefined,'attachment');
    const link=element('a',attachment.name);link.href=`/api/runs/${state.run.id}/attachments/${attachment.id}`;
    link.target='_blank';link.rel='noopener';
    const image=element('img'); image.src=link.href;image.alt=attachment.name;image.loading='lazy';
    const preview=element('a');preview.href=link.href;preview.target='_blank';preview.rel='noopener';preview.append(image);
    const remove=element('button','删除截图');remove.type='button';
    remove.addEventListener('click',()=>action(async()=>{
      state.run=await request(`/api/runs/${state.run.id}/cases/${state.current}/attachments/${attachment.id}`,
        {method:'DELETE',headers:{'X-Run-Revision':String(state.run.revision)}});
      renderAttachments();summary();message('截图已删除。');
    }));
    card.append(preview,link,remove);target.append(card);
  }
  if(!target.children.length) target.append(element('p','尚未上传截图。可多选文件，也可从剪贴板粘贴截图。','muted'));
}
async function uploadFiles(files) {
  await action(async()=>{
    const images=Array.from(files);if(!images.length)return;
    for(let i=0;i<images.length;i++) {
      const form=new FormData();form.append('file',images[i]);form.append('revision',String(state.run.revision));
      state.run=await request(`/api/runs/${state.run.id}/cases/${state.current}/attachments`,{method:'POST',body:form});
      renderAttachments();summary();message(`已保存截图 ${i+1}/${images.length}。判定与实际结果需另点保存。`);
    }
    if($('#files')) $('#files').value='';
  });
}
function renderDetail() {
  const target=$('#detail');target.replaceChildren();
  const c=caseById(state.current);
  if(!c){target.append(element('p','请选择左侧用例。','muted'));return;}
  target.append(element('span',`${c.id} · ${c.execution==='manual'?'手动执行':autoText(c)} · ${c.priority}`,'eyebrow'));
  target.append(element('h2',c.title));
  target.append(element('p',`${c.phase===1?'第一阶段回归':'第二阶段增量'} / ${c.module} / ${c.requirementRefs?.join('、') || '原清单步骤 '+c.sourceSteps.join('、')}`,'muted'));
  const comparison=element('p',`历史：${labels[old(c.id)] || old(c.id)} · 本轮：${labels[row(c.id).status]} · 对比：${delta(row(c.id).status,old(c.id))}。历史结果只供比较。`,'notice');comparison.id='comparison';target.append(comparison);
  section(target,'前置条件',c.preconditions);
  target.append(element('h3','具体输入'),element('pre',JSON.stringify(c.input,null,2)));
  target.append(element('h3','操作步骤与预期输出'));
  const list=element('ol');
  for(const s of c.steps){const li=element('li',s.action);li.append(element('div','预期：'+s.expected,'expect'));list.append(li);}
  target.append(list);section(target,'最终检查',c.expected,false);
  if(c.execution!=='manual' && c.implementation)target.append(element('pre',`${c.implementation}::${c.automation.node}`));
  if(c.execution!=='manual'){target.append(element('p',c.automation.state==='implemented'?'此用例已有脚本，请执行本轮API命令。收集页不人工判定API结果，完整报告通过汇总命令合并。':'此用例规格已填写，自动化脚本待后续编写，不计为通过。','notice'));return;}
  if(!state.run){target.append(element('p','请先在上方创建测试批次，再上传截图和记录判定。','notice'));return;}
  const record=element('form',undefined,'record');record.id='result-form';
  record.append(element('h3','实验截图与实际结果'));
  const statusLabel=element('label','判定'), status=element('select');status.id='result-status';
  for(const [key,name] of Object.entries(labels))status.add(new Option(name,key));status.value=row(c.id).status;statusLabel.append(status);
  const actualLabel=element('label','实际现象 / 失败详情 / 阻塞或跳过原因'), actual=element('textarea');actual.id='actual';actual.maxLength=20000;
  actual.placeholder='写实际输入、观察到的结果，以及与预期不一致的位置。失败时写复现信息，阻塞时写缺少的条件。';actual.value=row(c.id).actual;actualLabel.append(actual);
  const upload=element('label','上传截图（PNG / JPEG / WebP；单张不超过10 MiB，每用例最多30张）','upload');
  const file=element('input');file.type='file';file.id='files';file.accept='image/png,image/jpeg,image/webp';file.multiple=true;upload.append(file);
  const attachments=element('div',undefined,'attachments');attachments.id='attachments';
  const actions=element('div',undefined,'record-actions'),save=element('button','保存判定与实际结果','primary');save.type='submit';save.id='save-result';
  const saveState=element('span',undefined,'dirty');saveState.id='save-state';actions.append(save,saveState);
  record.append(statusLabel,actualLabel,upload,attachments,element('p','通过/失败需填写实际结果并上传截图；任何步骤不符则整例判失败。阻塞/跳过需说明原因。','muted'),actions);
  target.append(record);renderAttachments();setDirty(false);
  status.addEventListener('change',()=>setDirty(true));actual.addEventListener('input',()=>setDirty(true));
  file.addEventListener('change',()=>uploadFiles(file.files));
  record.addEventListener('submit',e=>{e.preventDefault();action(async()=>{
    const sent={revision:state.run.revision,status:status.value,actual:actual.value};
    state.run=await request(`/api/runs/${state.run.id}/cases/${c.id}`,{method:'PUT',
      body:sent});
    const changed=sent.status!==status.value||sent.actual!==actual.value;
    setDirty(changed);summary();renderList();
    message(changed?'已保存提交时的结果；刚新增的文字或判定仍需保存。':`${c.id} 已保存：${labels[status.value]}。`);
  });});
}
async function loadRun(id) {
  state.run=await request('/api/runs/'+id);localStorage.setItem('phase2-manual-run',id);$('#runs').value=id;
  if(!caseById(state.current))state.current=catalog().cases.find(c=>c.execution==='manual').id;
  setDirty(false);moduleOptions();summary();renderList();renderDetail();
}
async function init() {
  await action(async()=>{
    const data=await request('/api/bootstrap');state.token=data.token;state.catalog=data.catalog;state.baseline=data.baseline;
    if(data.archivedRunCount)message(`已隐藏 ${data.archivedRunCount} 个旧规格批次；原记录仍保留，当前只显示22项手工用例。`);
    for(const run of data.runs)$('#runs').add(new Option(`${run.name} / ${run.tester}`,run.id));
    const remembered=localStorage.getItem('phase2-manual-run');
    if(data.runs.length)await loadRun(data.runs.some(r=>r.id===remembered)?remembered:data.runs[0].id);
    else {state.current=catalog().cases[0].id;moduleOptions();summary();renderList();renderDetail();$('#new-run').open=true;}
  });
}
$('#create-form').addEventListener('submit',e=>{e.preventDefault();if(!guard())return;action(async()=>{
  const data=Object.fromEntries(new FormData(e.target));
  const run=await request('/api/runs',{method:'POST',body:data});$('#runs').add(new Option(`${run.name} / ${run.tester}`,run.id));
  await loadRun(run.id);$('#new-run').open=false;message('批次已建立。所有用例初始均为未执行。');
});});
$('#runs').addEventListener('change',()=>{const id=$('#runs').value;if(!id)return;if(state.busy||!guard()){$('#runs').value=state.run?.id||'';return;}action(()=>loadRun(id));});
$('#reload').addEventListener('click',()=>{if(guard()&&state.run)action(async()=>{await loadRun(state.run.id);message('已读取磁盘中的最新记录。');});});
for(const name of ['module','phase','mode','status-filter','search'])$('#'+name).addEventListener('input',renderList);
document.querySelectorAll('[data-export]').forEach(button=>button.addEventListener('click',()=>{
  if(state.busy){message('正在保存，请稍后导出。',true);return;}
  if(state.dirty){message('请先保存当前判定与实际结果，再导出。',true);return;}
  window.location.assign(`/api/runs/${state.run.id}/export/${button.dataset.export}`);
}));
document.addEventListener('paste',e=>{
  if(!state.run||caseById(state.current)?.execution!=='manual')return;
  const files=Array.from(e.clipboardData?.items||[]).filter(i=>i.kind==='file'&&i.type.startsWith('image/')).map(i=>i.getAsFile());
  if(files.length){e.preventDefault();uploadFiles(files);}
});
window.addEventListener('beforeunload',e=>{if(state.dirty||state.busy){e.preventDefault();e.returnValue='';}});
init();
