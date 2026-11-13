// ══ modals ══
// ─── Admin ─────────────────────────────────────
function openAdmin(){$('#admin-panel').classList.add('open');loadAdminUsers()}
function closeAdmin(){$('#admin-panel').classList.remove('open')}
async function loadAdminUsers(){
  const r=await fetch('/auth/users?token='+_token);const users=await r.json();
  $('#admin-user-list').innerHTML=users.map(u=>'<tr><td>'+esc(u.username)+'</td><td>'+esc(u.display_name||'')+'</td><td><select onchange="adminSetRole(\''+u.user_id+'\',this.value)"><option value="admin" '+(u.role==='admin'?'selected':'')+'>管理员</option><option value="developer" '+(u.role==='developer'?'selected':'')+'>研发</option><option value="guest" '+(u.role==='guest'?'selected':'')+'>游客</option></select></td><td><button class="btn-danger" onclick="adminDeleteUser(\''+u.user_id+'\')">删除</button></td></tr>').join('');
}
async function adminSetRole(uid,role){await fetch('/auth/users/'+uid+'/role',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:_token,role})});loadAdminUsers();refreshAgents()}
async function adminDeleteUser(uid){if(!confirm('确定删除该用户?'))return;await fetch('/auth/users/'+uid+'?token='+_token,{method:'DELETE'});loadAdminUsers()}
async function adminAddUser(){
  const u=$('#au-username').value.trim(),p=$('#au-password').value,d=$('#au-display').value.trim(),r=$('#au-role').value;
  if(!u||!p)return alert('用户名和密码必填');
  const res=await fetch('/auth/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:_token,username:u,password:p,display_name:d,role:r})});
  const j=await res.json();
  if(j.ok){$('#au-username').value='';$('#au-password').value='';$('#au-display').value='';loadAdminUsers()}else{alert(j.error)}
}

// ─── Settings ──────────────────────────────────
async function openSettings(){
  $('#settings-panel').classList.add('open');
  try{
    const r=await fetch('/settings');const s=await r.json();
    // 主模型
    $('#set-model').value=s.llm.model||'deepseek-chat';
    $('#set-base-url').value=s.llm.base_url||'';
    $('#set-api-key').value=s.llm.api_key||'';
    // 搜索
    $('#set-tavily').value=s.search.tavily_api_key||'';
    $('#set-brave').value=s.search.brave_api_key||'';
    $('#set-bing').value=s.search.bing_api_key||'';
    const fb=s.search.fallback_order||['baidu','brave'];
    $('#fb-baidu').checked=fb.includes('baidu');
    $('#fb-brave').checked=fb.includes('brave');
    $('#fb-tavily').checked=fb.includes('tavily');
    // 多模型
    const mm=s.models||{};
    $('#set-multi-enabled').checked=mm.enabled||false;
    if(mm.orchestrator){$('#set-orch-model').value=mm.orchestrator.model||'deepseek-reasoner';$('#set-orch-url').value=mm.orchestrator.base_url||'';$('#set-orch-key').value=mm.orchestrator.api_key||''}
    if(mm.worker){$('#set-worker-model').value=mm.worker.model||'deepseek-chat';$('#set-worker-url').value=mm.worker.base_url||'';$('#set-worker-key').value=mm.worker.api_key||''}
    if(mm.evaluator){$('#set-eval-model').value=mm.evaluator.model||'deepseek-chat';$('#set-eval-url').value=mm.evaluator.base_url||'';$('#set-eval-key').value=mm.evaluator.api_key||''}
    toggleMultiModel(); // sync UI state
  }catch(e){}
}
function closeSettings(){$('#settings-panel').classList.remove('open')}
function toggleMultiModel(){
  const on=$('#set-multi-enabled').checked;
  $('#multi-model-section').style.display=on?'block':'none';
  $('#multi-model-status').textContent=on?'已启用':'已禁用';
  $('#multi-model-status').style.color=on?'var(--green)':'var(--text3)';
}
async function saveSettings(){
  const msg=$('#set-msg');msg.style.color='var(--yellow)';msg.textContent='保存中...';
  try{
    // 保存主模型
    await fetch('/settings/llm',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({model:$('#set-model').value,base_url:$('#set-base-url').value.trim()||null,api_key:$('#set-api-key').value.trim()||null})});
    // 保存搜索
    const order=[];
    if($('#fb-baidu').checked)order.push('baidu');
    if($('#fb-brave').checked)order.push('brave');
    if($('#fb-tavily').checked)order.push('tavily');
    await fetch('/settings/search',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({tavily_api_key:$('#set-tavily').value.trim()||'',brave_api_key:$('#set-brave').value.trim()||'',bing_api_key:$('#set-bing').value.trim()||'',fallback_order:order})});
    // 保存多模型配置
    const multiEnabled=$('#set-multi-enabled').checked;
    await fetch('/settings/models',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        enabled:multiEnabled,
        orchestrator:{model:$('#set-orch-model').value,base_url:$('#set-orch-url').value.trim()||null,api_key:$('#set-orch-key').value.trim()||null},
        worker:{model:$('#set-worker-model').value,base_url:$('#set-worker-url').value.trim()||null,api_key:$('#set-worker-key').value.trim()||null},
        evaluator:{model:$('#set-eval-model').value,base_url:$('#set-eval-url').value.trim()||null,api_key:$('#set-eval-key').value.trim()||null}
      })});
    msg.style.color='var(--green)';msg.textContent='已保存，模型即时切换生效';
    loadMode();setTimeout(()=>{msg.textContent=''},3000);
  }catch(e){msg.style.color='var(--red)';msg.textContent='保存失败: '+e.message}
}