// WebSocket turns are persisted only by the server.
let _reconnectTimer=null;
function setComposerStatus(message){const el=$('#composer-status');if(el)el.textContent=message}
function setBusy(busy){
  streaming=busy;
  $('#snd').disabled=busy||_sessionLoading||_user?.role==='guest'||!wsOk;
  $('#inp').disabled=_user?.role==='guest';
  $('#sess-select').disabled=busy||_sessionLoading;
  $('#msg-area').setAttribute('aria-busy',String(busy));
  const run=$('#run-state');if(run){run.textContent=busy?'执行中':_runState==='error'?'执行中断':'就绪';run.dataset.state=busy?'working':_runState}
}
function conn(){
  if(ws&&ws.readyState<=1)return;
  const socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws`);ws=socket;
  socket.onopen=()=>{
    if(ws!==socket)return;wsOk=true;
    $('#ws-dot').className='ws-dot ws-ok';$('#sys-conn').textContent='● 已连接';$('#sys-conn').style.color='var(--green)';
    updateWSConnStatus(true);setBusy(streaming);
    setComposerStatus(_runState==='error'?'连接已恢复。可加载会话检查结果，或编辑草稿后重试。':'Enter 发送 · Shift + Enter 换行');
  };
  socket.onclose=()=>{
    if(ws!==socket)return;wsOk=false;
    $('#ws-dot').className='ws-dot ws-err';$('#sys-conn').textContent='● 已断开';$('#sys-conn').style.color='var(--red)';updateWSConnStatus(false);
    if(streaming)onError({error:'连接中断，结果尚未确认。重新连接后可加载会话查看已保存的结果。'});
    setBusy(false);setComposerStatus('连接已断开，正在重连；草稿已保留。');
    clearTimeout(_reconnectTimer);_reconnectTimer=setTimeout(conn,2000);
  };
  socket.onerror=()=>setComposerStatus('连接异常，正在等待重连；草稿已保留。');
  socket.onmessage=e=>{
    let packet;try{packet=JSON.parse(e.data)}catch{return}
    const {event,data={}}=packet;
    if(streaming&&data.task_id&&data.task_id===_lastTaskId)return;
    if(data.task_id&&streaming&&!_activeTaskId)_activeTaskId=data.task_id;
    if(streaming&&data.task_id&&_activeTaskId!==data.task_id)return;
    const handlers={text:onText,tool_start:onToolStart,tool_end:onToolEnd,result:onDone,error:onError,auth:data=>onError({...data,error:'登录已失效，请重新登录。'}),plan:onPlan,score:onScore,status:onStatus,event:onEvent};
    if(handlers[event])handlers[event](data);
  };
}
async function send(){
  if(streaming||_sessionLoading)return;
  if(!_token){setComposerStatus('请先登录后发送任务。');$('#login-overlay').style.display='flex';return}
  if(_user?.role==='guest'){setComposerStatus('访客账号仅可浏览，请使用研发或管理员账号。');return}
  const inp=$('#inp'),draft=inp.value,msg=draft.trim();if(!msg)return;
  if(!wsOk||!ws||ws.readyState!==WebSocket.OPEN){setComposerStatus('尚未连接服务器，草稿已保留，连接恢复后请重试。');return}
  _runState='working';setBusy(true);setComposerStatus('正在准备任务…');
  _textBuf='';_toolCount=0;_totalTokens=0;_toolStartTimes={};_toolLogEntries=[];_subtasks=[];_activeTaskId=null;_lastPrompt=msg;_currentResponse=null;
  try{
    if(!_currentSid)await autoCreateSession(msg);
    if(!wsOk||ws.readyState!==WebSocket.OPEN)throw new Error('连接已断开，请重试');
    $('#msg-area').querySelector('.welcome')?.remove();$('#msg-area').querySelector('.empty-state')?.remove();
    addUserMsg(msg);addAIMsg();
    if(msg.startsWith('/')){if(inp.value===draft)inp.value='';resizeComposer();await handleSlash(msg);return}
    ws.send(JSON.stringify({token:_token,message:msg,session_id:_currentSid}));
    if(inp.value===draft)inp.value='';resizeComposer();
    setComposerStatus('任务执行中，可在输入框继续准备下一条消息。');updateWorkspace();
  }catch(error){
    if(!inp.value)inp.value=draft;
    if(_currentResponse)onError({error:error.message});
    else{_runState='error';setBusy(false);setComposerStatus('发送失败：'+error.message+'；草稿已保留。');showToast('error','发送失败',error.message)}
  }
}
