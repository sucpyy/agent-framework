// Message rendering: every turn owns its response, plan, and tool log.
function addUserMsg(text,timestamp){
  const d=document.createElement('div');d.className='msg-wrapper msg-user';
  const date=timestamp?sessionDate(timestamp):new Date();
  const time=Number.isNaN(date.getTime())?'':date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  d.innerHTML='<div class="avatar">'+esc((_user?.display_name||'你').charAt(0))+'</div><div><div class="message-heading">你 <span class="msg-time">'+esc(time)+'</span></div><div class="bubble">'+esc(text).replace(/\n/g,'<br>')+'</div></div>';
  $('#msg-area').appendChild(d);scrollDown();
}
function addAIMsg(){
  const d=document.createElement('div');d.className='msg-wrapper msg-ai';
  d.innerHTML='<div class="message-heading"><span class="assistant-mark">A</span> Agent Workspace <span class="response-state">正在处理</span></div><div class="content"><span class="thinking-label">正在思考…</span></div>';
  $('#msg-area').appendChild(d);_currentResponse=d;scrollDown();return d;
}
function ensureAI(){return _currentResponse||addAIMsg()}
function finishResponse(response){
  const state=response.querySelector('.response-state');if(state)state.textContent='已完成';
  if(response.querySelector('.message-actions'))return;
  const actions=document.createElement('div');actions.className='message-actions';
  const copy=document.createElement('button');copy.type='button';copy.textContent='复制回复';
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(response.dataset.markdown||response.querySelector('.content').textContent);showToast('success','已复制','回复已复制到剪贴板',1800)}catch{showToast('warn','无法复制','浏览器未允许访问剪贴板。')}};
  actions.appendChild(copy);response.appendChild(actions);
}
function onText(data){
  if(!streaming)return;_textBuf+=String(data.content||'');
  const el=ensureAI();el.dataset.markdown=_textBuf;el.querySelector('.content').innerHTML=renderMD(_textBuf);scrollDown();
}
function renderMD(text){
  const source=String(text??'').replace(/\r\n?/g,'\n');
  const inline=line=>line.split(/(`[^`]*`)/g).map(part=>part.startsWith('`')&&part.endsWith('`')?'<code>'+esc(part.slice(1,-1))+'</code>':esc(part).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')).join('');
  let html='',inCode=false,code=[],list=null;
  const closeList=()=>{if(list){html+='</'+list+'>';list=null}};
  for(const line of source.split('\n')){
    if(/^\s*```/.test(line)){closeList();if(inCode){html+='<pre><code>'+esc(code.join('\n'))+'</code></pre>';code=[]}inCode=!inCode;continue}
    if(inCode){code.push(line);continue}
    const bullet=line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);
    if(bullet){const type=/\d/.test(bullet[1])?'ol':'ul';if(list!==type){closeList();list=type;html+='<'+type+'>'}html+='<li>'+inline(bullet[2])+'</li>';continue}
    closeList();const heading=line.match(/^(#{1,3})\s+(.+)$/);
    if(heading){const tag=heading[1].length===3?'h3':'h2';html+='<'+tag+'>'+inline(heading[2])+'</'+tag+'>'}
    else if(/^\s*---+\s*$/.test(line))html+='<hr>';
    else if(line.startsWith('> '))html+='<blockquote>'+inline(line.slice(2))+'</blockquote>';
    else html+=inline(line)+'<br>';
  }
  closeList();if(inCode)html+='<pre><code>'+esc(code.join('\n'))+'</code></pre>';
  return html;
}
let _toolStartTimes={},_toolLogEntries=[];
function onToolStart(data){
  if(!streaming)return;_toolCount++;
  const response=ensureAI();let section=response.querySelector('.tool-log-section');
  if(!section){
    section=document.createElement('section');section.className='tool-log-section';
    section.innerHTML='<div class="tool-log-hdr"><span>工具调用</span><button class="log-expand" type="button">收起</button></div><div class="tool-log-entries"></div>';
    section.querySelector('.log-expand').onclick=()=>toggleToolLog(section);response.appendChild(section);
  }
  const entry=document.createElement('div');entry.className='tool-entry tool-running';
  entry.innerHTML='<span class="tl-time">'+new Date().toLocaleTimeString('zh-CN',{hour12:false})+'</span><span class="tl-name">'+esc(data.name)+'</span><span class="tl-detail">执行中…</span><span class="tl-latency">—</span><span class="tl-status">运行中</span>';
  (_toolStartTimes[data.name]||= []).push({entry,start:Date.now()});
  section.querySelector('.tool-log-entries').appendChild(entry);scrollDown();
}
function onToolEnd(data){
  const pending=_toolStartTimes[data.name]?.shift();if(!pending)return;
  const {entry,start}=pending;const elapsed=((Date.now()-start)/1000).toFixed(1)+'s';
  const out=typeof data.output==='string'?data.output:JSON.stringify(data.output??'');
  const failed=Boolean(data.error||data.output?.error);entry.className='tool-entry '+(failed?'tool-error':'tool-ok');
  entry.querySelector('.tl-detail').textContent=out;entry.querySelector('.tl-detail').title=out;
  entry.querySelector('.tl-latency').textContent=elapsed;entry.querySelector('.tl-status').textContent=failed?'失败':'完成';
  _toolLogEntries.push({name:data.name,output:out,elapsed});scrollDown();
}
function toggleToolLog(section){
  section=section||_currentResponse?.querySelector('.tool-log-section');if(!section)return;
  const entries=section.querySelector('.tool-log-entries'),button=section.querySelector('.log-expand');
  const open=entries.style.display==='none';entries.style.display=open?'':'none';button.textContent=open?'收起':'展开';
}
function onPlan(data){
  if(!streaming||!Array.isArray(data.subtasks))return;
  _subtasks=data.subtasks.map((task,i)=>({...task,id:String(task.id??task.subtask_id??i),status:task.status||'pending'}));
  updateExecCards();drawDAG(_subtasks);updateWorkspace();scrollDown();
}
function updateExecCards(){
  if(!_currentResponse||!_subtasks.length)return;
  let card=_currentResponse.querySelector('.plan-card');
  if(!card){card=document.createElement('section');card.className='card plan-card';_currentResponse.appendChild(card)}
  const labels={completed:'已完成',in_progress:'进行中',running:'进行中',failed:'失败',cancelled:'已取消',pending:'等待中'};
  const done=_subtasks.filter(s=>s.status==='completed').length;
  card.innerHTML='<div class="plan-header">任务拆解 <span>'+done+' / '+_subtasks.length+' 已完成</span></div>'+_subtasks.map(st=>{
    const done=st.status==='completed',active=['in_progress','running'].includes(st.status),failed=st.status==='failed';
    return '<div class="plan-task"><span class="pt-icon '+(done?'pt-done':active?'pt-active':'pt-wait')+'">'+(done?'✓':failed?'×':active?'●':'○')+'</span><span class="pt-agent">'+esc(st.agent||'智能体')+'</span><span class="pt-desc">'+esc(st.description||'子任务')+'</span><span class="pt-status">'+(labels[st.status]||'等待中')+'</span></div>';
  }).join('');
}
function onDone(data){
  if(!streaming)return;
  const response=ensureAI();const content=typeof data.content==='string'?data.content:_textBuf;
  response.querySelector('.content').innerHTML=renderMD(content||'任务已结束，未返回文本结果。');response.dataset.markdown=content;
  if(data.session_id)_currentSid=data.session_id;
  for(const result of data.subtasks||[]){const task=_subtasks.find(s=>s.id===String(result.subtask_id??result.id));if(task)Object.assign(task,{status:result.status||'completed',error:result.error})}
  const failed=(data.subtasks||[]).some(s=>s.status==='failed'||s.error);
  if(!data.subtasks)_subtasks.forEach(s=>{if(s.status!=='failed')s.status='completed'});
  _totalTokens=Number(data.tokens??data.summary?.tokens?.total??data.usage?.total_tokens??((data.usage?.input_tokens||0)+(data.usage?.output_tokens||0)))||0;
  const cost=_totalTokens*0.000004;
  _taskHistory.push({latency:Number(data.latency)||0,tokens:_totalTokens,tools:_toolCount,success:!failed,cost});
  _runState=failed?'error':'completed';_lastTaskId=data.task_id||_activeTaskId;_textBuf='';
  finishResponse(response);if(failed){const state=response.querySelector('.response-state');if(state)state.textContent='部分任务失败'}
  setBusy(false);updateExecCards();updateMetrics();updateWorkspace();
  setComposerStatus(failed?'任务已结束，部分子任务失败，请查看结果。':'任务已完成 · Enter 发送下一条消息');
  refreshAgents();loadHist();loadSessionList();
  addNotification(failed?'error':'task',failed?'部分任务失败':'任务完成','耗时 '+(Number(data.latency)||0).toFixed(1)+' 秒 · '+_toolCount+' 次工具调用');scrollDown();
}
function onError(data){
  const message=String(data.error||'未知错误');const wasRunning=streaming;_runState='error';_lastTaskId=data.task_id||_activeTaskId;
  if(wasRunning)_taskHistory.push({latency:0,tokens:_totalTokens,tools:_toolCount,success:false,cost:0});
  setBusy(false);if(!$('#inp').value)$('#inp').value=_lastPrompt;resizeComposer();
  _subtasks.forEach(s=>{if(['running','in_progress'].includes(s.status))s.status='failed'});updateExecCards();updateMetrics();updateWorkspace();
  setComposerStatus('执行失败，草稿已保留；可修改后重新发送。');addNotification('error','任务执行失败',message);
  if(_currentResponse){
    if(!_textBuf)_currentResponse.querySelector('.content').innerHTML='';
    const error=document.createElement('div');error.className='response-error';error.textContent='执行失败：'+message;_currentResponse.appendChild(error);
    const retry=document.createElement('button');retry.type='button';retry.className='retry-button';retry.textContent='编辑后重试';const prompt=_lastPrompt;
    retry.onclick=()=>{if(streaming)return;const inp=$('#inp');if(inp.value&&inp.value!==prompt){showToast('info','已有草稿','请先处理输入框中的草稿。');inp.focus();return}inp.value=prompt;resizeComposer();inp.focus()};_currentResponse.appendChild(retry);
    const state=_currentResponse.querySelector('.response-state');if(state)state.textContent='执行失败';
  }
  if(/未登录|登录已过期|token|unauthorized|登录.*失效/i.test(message)){_token='';$('#login-overlay').style.display='flex';$('#login-err').textContent='登录已失效，请重新登录。'}
  scrollDown();
}
function onStatus(data){
  if(!streaming)return;
  const labels={thinking:'正在思考…',planning:'正在规划任务…',executing:'正在执行任务…',done:'正在整理结果…'};
  const message=labels[data.phase]||'正在处理任务…';
  const placeholder=ensureAI().querySelector('.thinking-label');if(placeholder)placeholder.textContent=message;
  setComposerStatus(message+' 可继续准备下一条消息。');
}
function onEvent(data){
  if(!streaming)return;const payload=data.payload||{};
  const task=_subtasks.find(s=>s.id===String(payload.subtask_id));
  if(task){if(data.event==='subtask:started')task.status='in_progress';else if(data.event==='subtask:completed')task.status=payload.error?'failed':'completed';else if(data.event==='task:failed')task.status='failed';updateExecCards();updateWorkspace()}
}
let _lastScores=null;
function onScore(data){
  if(!data.scores||!_currentResponse)return;
  const number=v=>Math.max(0,Math.min(10,Number(v)||0));const raw=data.scores;
  const s={correctness:number(raw.correctness),completeness:number(raw.completeness),efficiency:number(raw.efficiency),tone:number(raw.tone)};_lastScores=s;
  drawScoreRing(raw.overall==null?Math.round((s.correctness+s.completeness+s.efficiency+s.tone)/4):number(raw.overall));
  $('#sc-correct').textContent=stars(s.correctness);$('#sc-complete').textContent=stars(s.completeness);$('#sc-efficiency').textContent=stars(s.efficiency);$('#sc-tone').textContent=stars(s.tone);
}
function stars(v){const n=Math.max(0,Math.min(5,Math.round(Number(v)/2)||0));return'★'.repeat(n)+'☆'.repeat(5-n)}
function toggleScoreDetail(){
  const panel=$('#score-detail-extra');const open=panel.style.display==='none';panel.style.display=open?'block':'none';
  if(open)panel.innerHTML=_lastScores?Object.entries({correctness:'正确性',completeness:'完整性',efficiency:'效率',tone:'语气'}).map(([key,label])=>'<div>'+label+' '+_lastScores[key]+'/10</div>').join(''):'暂无评分数据';
}
function updateWorkspace(){
  const completed=_subtasks.filter(s=>s.status==='completed').length;
  const pct=_subtasks.length?Math.round(completed/_subtasks.length*100):_runState==='completed'?100:0;
  $('#ws-progress-text').textContent=streaming&&!_subtasks.length?'执行中':pct+'%';$('#ws-progress-fill').style.width=pct+'%';
  $('#ws-status-label').textContent=streaming?'执行中':_runState==='completed'?'已完成':_runState==='error'?'执行失败':'等待任务';
  $('#ws-status-dot').style.background=streaming?'var(--accent)':_runState==='completed'?'var(--green)':_runState==='error'?'var(--red)':'var(--text3)';
}
function updateWSMeta(){
  const session=_sessions.find(s=>s.session_id===_currentSid)||_sessionMeta;
  const name=_currentSid?(session?.name||'当前会话'):'新会话';
  $('#ws-name-text').textContent=name;$('#conversation-title').textContent=_currentSid?name:'工作台';
  $('#conversation-subtitle').textContent=_currentSid?'会话自动保存 · 多智能体协作':'把复杂任务交给智能体，把注意力留给创造';
  $('#ws-created').textContent='创建：'+(session?.created_at?formatSessionDate(session.created_at):'—');
  $('#ws-updated').textContent='更新：'+(session?.updated_at?formatSessionDate(session.updated_at):'—');
}
