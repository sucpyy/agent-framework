// Session identity stays explicit; browsing never changes an active run.
async function apiJSON(url,options){
  const response=await fetch(url,options);const data=await response.json();
  if(!response.ok||data.error)throw new Error(data.error||data.detail||'请求失败（'+response.status+'）');return data;
}
async function autoCreateSession(firstMsg){
  const d=await apiJSON('/sessions/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:firstMsg.slice(0,30)})});
  if(!d.session_id)throw new Error('服务器未返回会话编号');
  _currentSid=d.session_id;_sessionMeta={...d,name:d.name||firstMsg.slice(0,30)};
  updateWSMeta();loadSessionList();return _currentSid;
}
function sessionLocked(){
  if(!streaming&&!_sessionLoading)return false;
  showToast('info','请稍候',streaming?'当前任务执行中，完成后可切换会话。':'正在加载会话。');return true;
}
function resetSessionView(){
  _taskHistory=[];_totalCost=0;_toolCount=0;_totalTokens=0;_textBuf='';_currentResponse=null;_subtasks=[];_lastScores=null;_dagData=null;_runState='idle';_lastPrompt='';
  updateMetrics();drawDAG([]);drawScoreRing(0);$('#score-total').textContent='—';
  ['sc-correct','sc-complete','sc-efficiency','sc-tone'].forEach(id=>$('#'+id).textContent='—');
  $('#score-detail-extra').style.display='none';$('#dag-full').style.display='none';
  $('#ws-progress-text').textContent='0%';$('#ws-progress-fill').style.width='0%';$('#ws-name-text').textContent='新会话';
  $('#ws-status-label').textContent='等待任务';$('#ws-status-dot').style.background='var(--text3)';
  setBusy(false);setComposerStatus('Enter 发送 · Shift + Enter 换行');
}
async function newSession(){
  if(sessionLocked())return;
  _currentSid=null;_sessionMeta=null;resetSessionView();renderWelcome();$('#sess-select').value='';
  updateWSMeta();renderSessionList();closeSidePanels();$('#inp').focus();
}
async function loadSession(sid){
  if(!sid)return newSession();if(sessionLocked())return;
  _sessionLoading=true;setBusy(false);setComposerStatus('正在加载会话…');
  try{
    const d=await apiJSON('/sessions/'+encodeURIComponent(sid)+'/load',{method:'POST'});
    _currentSid=d.session_id;_sessionMeta=d;resetSessionView();$('#msg-area').innerHTML='';
    for(const m of d.messages||[]){
      if(m.role==='user')addUserMsg(m.content||'',m.ts||m.timestamp||m.created_at);
      else if(m.role==='assistant'){
        const response=addAIMsg();response.querySelector('.content').innerHTML=renderMD(m.content||'');
        response.dataset.markdown=m.content||'';finishResponse(response);
      }
    }
    _currentResponse=null;if(!d.messages?.length)renderWelcome();
    updateWSMeta();updateWorkspace();await loadSessionList();closeSidePanels();scrollDown();
  }catch(error){showToast('error','加载会话失败',error.message);setComposerStatus('加载失败，当前草稿已保留。')}
  finally{_sessionLoading=false;setBusy(false)}
}
async function loadSessionList(){
  try{_sessions=await apiJSON('/sessions');renderSessionList();updateWSMeta()}
  catch(error){showToast('warn','会话列表暂不可用',error.message)}
}
function renderSessionList(){
  const sel=$('#sess-select');sel.innerHTML='<option value="">+ 新建会话</option>'+_sessions.map(x=>'<option value="'+esc(x.session_id)+'">'+esc(x.name||'未命名会话')+'</option>').join('');sel.value=_currentSid||'';
  const list=$('#session-list');if(!list)return;
  list.innerHTML='';
  if(!_sessions.length){list.innerHTML='<div class="session-empty">还没有会话<br>发送任务后会自动保存</div>';return}
  _sessions.slice(0,30).forEach(s=>{
    const item=document.createElement('button');item.type='button';item.className='session-item'+(s.session_id===_currentSid?' active':'');
    item.setAttribute('aria-current',s.session_id===_currentSid?'true':'false');
    item.innerHTML='<span class="session-name">'+esc(s.name||'未命名会话')+'</span><span class="session-meta">'+Number(s.message_count||0)+' 条消息'+(s.updated_at?' · '+formatSessionDate(s.updated_at):'')+'</span>';
    item.onclick=()=>loadSession(s.session_id);list.appendChild(item);
  });
}
function sessionDate(value){return new Date(typeof value==='number'&&value<1e12?value*1000:value)}
function formatSessionDate(value){const date=sessionDate(value);return Number.isNaN(date.getTime())?'':date.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'})}
async function renameCurrentSession(){
  if(sessionLocked())return;if(!_currentSid){showToast('info','尚无会话','发送第一条任务后即可重命名。');return}
  const name=prompt('新的会话名称',_sessionMeta?.name||'');if(!name?.trim())return;
  try{await apiJSON('/sessions/'+encodeURIComponent(_currentSid)+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim()})});_sessionMeta={..._sessionMeta,name:name.trim()};await loadSessionList();updateWSMeta();showToast('success','已重命名',name.trim())}
  catch(error){showToast('error','重命名失败',error.message)}
}
async function deleteSession(sid){
  if(sessionLocked())return;if(!confirm('确定删除此会话？此操作不可恢复。'))return;
  try{await apiJSON('/sessions/'+encodeURIComponent(sid),{method:'DELETE'});if(_currentSid===sid)await newSession();await loadSessionList()}
  catch(error){showToast('error','删除失败',error.message)}
}
async function autoLoadLastSession(){await loadSessionList()}
