// Slash commands use REST and persist with an explicit session.
function quickCmd(cmd){$('#inp').value=cmd+' ';resizeComposer();$('#inp').focus()}
async function handleSlash(cmd){
  const [command,...parts]=cmd.split(' '),context=parts.join(' ')||'最近变更',sid=_currentSid;
  const save=(role,content)=>apiJSON('/sessions/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,role,content})});
  try{await save('user',cmd);const d=await apiJSON('/slash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command,context})});const content=String(d.result??'');await save('assistant',content);onDone({session_id:sid,content,latency:0})}catch(error){onError({error:error.message})}
}
async function loadHist(){try{const items=await apiJSON('/history');$('#hist-list').innerHTML=items.slice(0,15).map(i=>'<div class="hist-item"><div class="hi-title">'+esc((i.description||'').slice(0,70))+'</div><div class="hi-meta">'+(i.status==='completed'?'已完成':'执行失败')+' · '+(Number(i.latency)||0).toFixed(1)+' 秒 · '+((Number(i.tokens)||0)/1000).toFixed(1)+'k Token</div></div>').join('')||'<div class="session-empty">暂无执行记录</div>'}catch{$('#hist-list').textContent='暂时无法加载执行记录'}}
async function loadMode(){try{const d=await apiJSON('/mode');$('#md-badge').textContent=d.mode==='live'?'实时模型':'演示模式';$('#md-badge').className='badge '+(d.mode==='live'?'b-live':'b-echo')}catch{}}
let _searchDebounce=null,_searchResults=[],_searchIdx=-1,_searchSequence=0;
async function doGlobalSearch(){if(_searchIdx>=0&&_searchResults[_searchIdx])return selectSearchResult(_searchIdx);const q=$('#global-search').value.trim();if(q)await doLiveSearch(q)}
function onSearchFocus(){if($('#search-kbd'))$('#search-kbd').style.display='none';const q=$('#global-search').value.trim();if(q.length>=2)doLiveSearch(q)}
function onSearchBlur(){setTimeout(()=>{if($('#search-dropdown'))$('#search-dropdown').style.display='none'},200);if(!$('#global-search').value&&$('#search-kbd'))$('#search-kbd').style.display=''}
async function doLiveSearch(q){const request=++_searchSequence;try{const d=await apiJSON('/search/local?q='+encodeURIComponent(q));if(request!==_searchSequence)return;_searchResults=d.results||[];_searchIdx=-1;renderSearchDropdown()}catch(error){showToast('warn','搜索暂不可用',error.message)}}
function renderSearchDropdown(){
  let dd=document.getElementById('search-dropdown');if(!dd){dd=document.createElement('div');dd.id='search-dropdown';dd.className='search-dropdown';$('#global-search').parentElement.appendChild(dd)}
  dd.style.display='block';const labels={session:'会话',agent:'智能体',skill:'技能',task:'任务'};
  dd.innerHTML=_searchResults.length?_searchResults.map((r,i)=>'<button type="button" class="search-result-item'+(i===_searchIdx?' selected':'')+'" data-idx="'+i+'"><span>'+esc(labels[r.type]||'结果')+'</span><span><strong>'+esc(r.title||r.id)+'</strong><small>'+esc(r.detail||'')+'</small></span></button>').join(''):'<div class="session-empty">没有找到相关结果</div>';
  dd.querySelectorAll('[data-idx]').forEach(button=>button.onmousedown=e=>{e.preventDefault();selectSearchResult(Number(button.dataset.idx))});
}
function selectSearchResult(idx){
  const result=_searchResults[idx];if(!result)return;$('#search-dropdown').style.display='none';_searchIdx=-1;
  if(result.type==='session')return loadSession(result.id);
  if(result.type==='agent')return toggleAgentDetail(result.id);
  if(result.type==='skill'){
    $('#skill-pool').style.display='';_skillPoolCollapsed=false;if($('#skill-pool-hidden'))$('#skill-pool-hidden').style.display='block';
    const chip=[...document.querySelectorAll('.skill-chip')].find(el=>el.textContent.includes(result.id));if(chip)chip.scrollIntoView({block:'nearest'});
    showToast('info',result.title||result.id,result.detail||'已在技能池中显示');return;
  }
  addNotification('search',result.title||result.id,result.detail||'历史任务');showToast('info',result.title||'搜索结果',result.detail||'已找到历史任务');
}
function toggleNotifPanel(){$('#notif-panel').classList.toggle('open');renderNotifications()}
function clearNotifications(){_notifications=[];$('#notif-dot').classList.remove('show');renderNotifications()}
function addNotification(type,title,detail){_notifications.unshift({type,title,detail,time:new Date().toLocaleTimeString('zh-CN',{hour12:false})});if(_notifications.length>50)_notifications.length=50;$('#notif-dot').classList.add('show');if($('#notif-panel').classList.contains('open'))renderNotifications()}
function renderNotifications(){$('#notif-list').innerHTML=_notifications.length?_notifications.slice(0,20).map(n=>'<div class="np-item"><div>'+esc(n.title)+'</div><div class="np-detail">'+esc(n.detail||'')+'</div><div class="np-time">'+esc(n.time)+'</div></div>').join(''):'<div class="np-empty">暂无通知</div>'}
function toggleFullscreen(){if(!document.fullscreenElement)document.documentElement.requestFullscreen().catch(()=>showToast('warn','无法全屏','浏览器拒绝了全屏请求'));else document.exitFullscreen()}
function toggleSidebar(){if(window.innerWidth<=900){document.body.classList.toggle('sidebar-open');document.body.classList.remove('inspector-open')}else{_sidebarCollapsed=!_sidebarCollapsed;$('#left-panel').classList.toggle('collapsed');document.body.classList.toggle('sidebar-collapsed')}}
function toggleInspector(){if(window.innerWidth<=1150){document.body.classList.toggle('inspector-open');document.body.classList.remove('sidebar-open')}else $('#right-panel').classList.toggle('collapsed')}
function closeSidePanels(){document.body.classList.remove('sidebar-open','inspector-open')}
function toggleUserMenu(){$('#ud-menu').classList.toggle('open');$('#notif-panel').classList.remove('open')}
function toggleUserMore(){openSettings()}
function toggleSkillPool(){_skillPoolCollapsed=!_skillPoolCollapsed;$('#skill-pool').style.display=_skillPoolCollapsed?'none':'';$('#sp-toggle').textContent=_skillPoolCollapsed?'+':'−'}
function expandSkillPool(){window._skillPoolExpanded=!window._skillPoolExpanded;if($('#skill-pool-hidden'))$('#skill-pool-hidden').style.display=window._skillPoolExpanded?'block':'none';$('#sp-expand-btn').textContent=window._skillPoolExpanded?'收起技能':'查看全部技能'}
function updateWSConnStatus(ok){const el=$('#ws-conn-status');if(el){el.textContent=ok?'实时连接正常':'实时连接已断开';el.style.color=ok?'var(--green)':'var(--red)'}}
function resizeComposer(){const inp=$('#inp');if(!inp)return;inp.style.height='auto';inp.style.height=Math.min(inp.scrollHeight||48,180)+'px'}
function insertEmoji(){const inp=$('#inp'),start=inp.selectionStart??inp.value.length,end=inp.selectionEnd??start;inp.value=inp.value.slice(0,start)+'💡'+inp.value.slice(end);inp.selectionStart=inp.selectionEnd=start+2;resizeComposer();inp.focus()}
async function insertTextFile(file){
  if(!file)return;if(file.size>256*1024)throw new Error('文件过大，请选择不超过 256 KB 的文本文件。');
  const extension=(file.name.split('.').pop()||'').toLowerCase(),allowed=['txt','md','csv','json','js','ts','jsx','tsx','py','html','css','yaml','yml','xml','log','sql','toml','ini','sh','c','cpp','h','go','rs'];
  if(!allowed.includes(extension)&&!file.type.startsWith('text/'))throw new Error('仅支持文本、代码和 Markdown 文件；图片、PDF 和二进制文件暂不支持。');
  const text=await file.text();if(text.includes('\u0000'))throw new Error('该文件包含二进制内容，无法作为文本附加。');
  const inp=$('#inp');inp.value+=(inp.value?'\n\n':'')+'文件：'+file.name+'\n```\n'+text+'\n```';resizeComposer();inp.focus();setComposerStatus('已读取 '+file.name+'，内容已放入草稿，发送前可编辑。');
}
function uploadFile(){const picker=document.createElement('input');picker.type='file';picker.accept='text/*,.md,.json,.js,.ts,.tsx,.jsx,.py,.yaml,.yml,.sql,.toml,.log';picker.onchange=async()=>{try{await insertTextFile(picker.files[0])}catch(error){showToast('error','无法附加文件',error.message);setComposerStatus(error.message)}};picker.click()}
function openInputSettings(){const row=$('#input-bar').querySelector('.slash-btns');if(row)row.style.display=row.style.display==='none'?'flex':'none'}
function esc(value){return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
function scrollDown(){const area=$('#msg-area');if(area)setTimeout(()=>{area.scrollTop=area.scrollHeight},10)}
function showToast(type,title,message,duration=4000){
  const container=$('#toast-container');if(!container)return;const toast=document.createElement('div');toast.className='toast toast-'+type;
  toast.innerHTML='<span class="toast-icon">'+({success:'✓',error:'×',warn:'!',info:'i'}[type]||'i')+'</span><div class="toast-body"><div class="toast-title">'+esc(title)+'</div>'+(message?'<div class="toast-msg">'+esc(message)+'</div>':'')+'</div><button class="toast-close" aria-label="关闭通知">×</button>';
  toast.querySelector('.toast-close').onclick=()=>toast.remove();container.appendChild(toast);if(duration>0)setTimeout(()=>toast.remove(),duration);const all=container.querySelectorAll('.toast');if(all.length>3)all[0].remove();
}
let _healthInterval=null;
async function checkHealth(){try{const data=await apiJSON('/health');if(data.status!=='healthy')setComposerStatus('部分服务暂不可用，请检查系统状态。')}catch{}}
function startHealthCheck(){if(!_healthInterval){checkHealth();_healthInterval=setInterval(checkHealth,30000)}}
function openShortcutPanel(){$('#shortcut-panel').classList.add('open')}
function closeShortcutPanel(){$('#shortcut-panel').classList.remove('open')}
function switchSession(direction){if(sessionLocked())return;const index=_sessions.findIndex(s=>s.session_id===_currentSid),next=_sessions[index+direction];if(next)loadSession(next.session_id)}
const _templates={
  'code-review':{prompt:'请审查以下代码，重点检查潜在错误、安全风险和性能问题，并按严重程度给出可执行建议。\n\n代码或项目说明：\n',label:'审查代码',copy:'发现潜在问题，让每次交付更可靠。',icon:'⌘'},
  research:{prompt:'请调研以下主题，比较主流方案、列出信息来源，并给出结论与适用场景。\n\n调研主题：\n',label:'深入调研',copy:'整理多方信息，形成清晰的判断。',icon:'◎'},
  architecture:{prompt:'请分析以下系统架构，梳理模块职责、依赖关系与数据流，并提出分阶段的改进建议。\n\n项目背景与当前架构：\n',label:'规划架构',copy:'理清系统边界，规划下一步演进。',icon:'◇'},
};
function renderWelcome(){
  const hour=new Date().getHours(),greeting=hour<6?'夜深了':hour<12?'上午好':hour<18?'下午好':'晚上好';
  $('#msg-area').innerHTML='<section class="welcome"><div class="welcome-kicker"><span class="welcome-mark">✳</span><span>你的智能工作空间</span></div><h1 class="welcome-title">'+greeting+'，<br>今天想完成什么？</h1><p class="welcome-description">从一个想法开始。让智能体协同处理研究、代码与复杂任务，<br>你可以专注于真正重要的事。</p><div class="starter-grid">'+Object.entries(_templates).map(([id,t])=>'<button type="button" class="starter-card" data-template="'+id+'"><span class="starter-icon">'+t.icon+'</span><span class="starter-label">'+t.label+' <span aria-hidden="true">↗</span></span><span class="starter-copy">'+t.copy+'</span></button>').join('')+'</div></section>';
  $('#msg-area').querySelectorAll('[data-template]').forEach(button=>button.onclick=()=>quickStart(button.dataset.template));
}
function useTemplate(id){const template=_templates[id];if(!template)return;const inp=$('#inp');if(inp.value.trim()){showToast('info','已有草稿','请先发送或清空当前草稿，再使用模板。');inp.focus();return}inp.value=template.prompt;resizeComposer();inp.focus();setComposerStatus('已准备'+template.label+'模板，请补充具体内容后发送。')}
function quickStart(id){useTemplate(id)}
function exportChat(){
  const messages=$('#msg-area').querySelectorAll('.msg-wrapper');if(!messages.length){showToast('info','暂无内容','发送任务后可导出会话。');return}
  let markdown='# '+(_sessionMeta?.name||'Agent Workspace 对话')+'\n\n';messages.forEach(message=>{const user=message.classList.contains('msg-user');markdown+='## '+(user?'用户':'助手')+'\n\n'+(message.dataset.markdown||message.querySelector(user?'.bubble':'.content')?.textContent||'')+'\n\n'});
  const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([markdown],{type:'text/markdown;charset=utf-8'}));link.download='对话-'+new Date().toISOString().slice(0,10)+'.md';link.click();URL.revokeObjectURL(link.href);showToast('success','已导出','对话已保存为 Markdown 文件。');
}
// A single handler per input; one global shortcut listener.
$('#inp')?.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&event.keyCode!==229){event.preventDefault();send()}});
$('#inp')?.addEventListener('input',resizeComposer);
$('#login-pass')?.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing)doLogin()});
$('#global-search')?.addEventListener('input',()=>{clearTimeout(_searchDebounce);_searchIdx=-1;const query=$('#global-search').value.trim();if(query.length<2){_searchSequence++;_searchResults=[];if($('#search-dropdown'))$('#search-dropdown').style.display='none';return}_searchDebounce=setTimeout(()=>doLiveSearch(query),200)});
$('#global-search')?.addEventListener('keydown',event=>{
  if(event.isComposing)return;
  if(event.key==='Enter'){event.preventDefault();doGlobalSearch()}
  else if(event.key==='ArrowDown'){event.preventDefault();_searchIdx=Math.min(_searchIdx+1,_searchResults.length-1);renderSearchDropdown()}
  else if(event.key==='ArrowUp'){event.preventDefault();_searchIdx=Math.max(_searchIdx-1,-1);renderSearchDropdown()}
  else if(event.key==='Escape'){$('#search-dropdown').style.display='none';_searchIdx=-1}
});
document.addEventListener('keydown',event=>{
  if(event.isComposing)return;const modifier=event.ctrlKey||event.metaKey,key=event.key.toLowerCase();
  if(modifier&&key==='k'){event.preventDefault();$('#global-search').focus();$('#global-search').select();return}
  if(modifier&&key==='/'){event.preventDefault();openShortcutPanel();return}
  if(key==='escape'){['shortcut-panel','notif-panel','ud-menu','settings-panel','admin-panel','new-agent-modal'].forEach(id=>$('#'+id)?.classList.remove('open'));closeSidePanels();if($('#search-dropdown'))$('#search-dropdown').style.display='none';return}
  if(['INPUT','TEXTAREA','SELECT'].includes(event.target.tagName))return;
  if(modifier&&key==='b'){event.preventDefault();toggleSidebar()}
  if(modifier&&key==='n'){event.preventDefault();newSession()}
  if(modifier&&key===','){event.preventDefault();switchSession(-1)}
  if(modifier&&key==='.'){event.preventDefault();switchSession(1)}
});
document.addEventListener('click',event=>{if(!$('#user-dropdown')?.contains(event.target))$('#ud-menu')?.classList.remove('open');if(!$('#notif-panel')?.contains(event.target)&&!$('#notif-btn')?.contains(event.target))$('#notif-panel')?.classList.remove('open')});
window.addEventListener('unhandledrejection',event=>{console.error(event.reason);showToast('error','操作未完成',event.reason?.message||'请稍后重试')});
let _appInitialized=false;
function initApp(){if(_appInitialized)return;_appInitialized=true;renderWelcome();updateWSMeta();updateWorkspace();setComposerStatus('正在连接服务器…');conn();startHealthCheck()}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initApp);else initApp();
