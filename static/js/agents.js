// ══ agents ══
// ─── Agents ────────────────────────────────────
async function refreshAgents(){
  try{
    const r=await fetch('/agents');const a=await r.json();
    let html='';let working=0;
    a.forEach(x=>{
      const m=agentMeta[x.name]||{cat:'自定义',icon:'?',cls:'a-custom',color:'var(--cyan)'};
      const w=x.status==='working';if(w)working++;
      html+='<div class="agent-row" onclick="toggleAgentDetail(\''+x.name+'\')" title="'+esc(x.name)+' — '+m.cat+'">';
      html+='<div class="a-icon '+m.cls+'">'+m.icon+'</div>';
      html+='<div class="a-info"><div class="a-name">'+esc(x.name)+'</div><div class="a-type">'+m.cat+'</div>';
      html+='<div class="a-skills">'+x.skills.slice(0,3).map(s=>'<span>'+s.name+'</span>').join('')+(x.skills.length>3?'<span>+'+(x.skills.length-3)+'</span>':'')+'</div></div>';
      html+='<div class="a-status"><span class="status-dot '+(w?'s-work':x.status==='error'?'s-err':'s-idle')+'"></span></div>';
      html+='</div>';
    });
    $('#agent-list').innerHTML=html;
    $('#task-count').textContent=a.length+' 个智能体 · '+working+' 工作中';
    var ac=document.getElementById('agent-count-tag');if(ac)ac.textContent='('+a.length+'/'+a.length+')';
  }catch(e){}
}
async function toggleAgentDetail(name){
  // Remove any existing panel
  const existing=document.getElementById('agent-detail-'+name);
  if(existing){existing.remove();return}
  // Fetch agent info, skills, and thread
  const[ar,sr,th]=await Promise.all([
    fetch('/agents').then(r=>r.json()),
    fetch('/skills').then(r=>r.json()),
    fetch('/agents/'+encodeURIComponent(name)+'/thread').then(r=>r.json()).catch(()=>({threads:[]}))
  ]);
  const agent=ar.find(x=>x.name===name);if(!agent)return;
  const cur=new Set(agent.all_skills);
  const threads=th.threads||[];
  const panel=document.createElement('div');panel.id='agent-detail-'+name;
  panel.style.cssText='background:var(--bg-input);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px 8px;margin:2px 0;font-size:10px;max-height:320px;overflow-y:auto';
  let html='<div style="display:flex;gap:6px;margin-bottom:6px;border-bottom:1px solid var(--border);padding-bottom:4px">';
  html+='<button onclick="switchAgentTab(\''+name+'\',\'skills\')" class="agent-tab active" id="atab-skills-'+name+'" style="background:none;border:none;color:var(--accent);font-size:10px;cursor:pointer;padding:2px 6px;border-bottom:2px solid var(--accent)">技能</button>';
  html+='<button onclick="switchAgentTab(\''+name+'\',\'thread\')" class="agent-tab" id="atab-thread-'+name+'" style="background:none;border:none;color:var(--text3);font-size:10px;cursor:pointer;padding:2px 6px">线程 ('+threads.length+')</button>';
  html+='<span style="flex:1"></span><button onclick="renameAgent(\''+name+'\')" style="background:none;border:1px solid var(--border);color:var(--text2);font-size:9px;padding:1px 4px;border-radius:3px;cursor:pointer">改名</button></div>';
  // Skills tab content
  html+='<div id="atab-content-skills-'+name+'">'+sr.map(s=>'<label style="display:flex;align-items:center;gap:4px;padding:2px 0;cursor:pointer;font-size:10px"><input type="checkbox" '+(cur.has(s.name)?'checked':'')+' onchange="toggleSkill(\''+name+'\',\''+s.name+'\',this.checked)"><span>'+s.name+'</span><span style="color:var(--text3);font-size:8px">'+s.capabilities.slice(0,3).join(', ')+'</span></label>').join('')+'</div>';
  // Thread tab content (hidden by default)
  html+='<div id="atab-content-thread-'+name+'" style="display:none">';
  if(threads.length===0)html+='<div style="color:var(--text3);padding:8px;text-align:center">暂无执行记录</div>';
  else threads.forEach(t=>{
    html+='<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,.03)"><div style="color:var(--text2)">'+esc(t.description||'').substring(0,50)+'</div><div style="font-size:8px;color:var(--text3)">子任务: '+esc(t.subtask||'').substring(0,40)+' · '+(t.status==='completed'?'✓ 完成':t.status||'?')+' · '+t.latency.toFixed(1)+'s · 工具: '+(t.tool_calls||[]).length+'次</div></div>';
  });
  html+='</div>';
  panel.innerHTML=html;
  const rowsArr=[...document.querySelectorAll('.agent-row')];
  const idx=rowsArr.findIndex(r=>r.querySelector('.a-name')?.textContent===name);
  if(idx>=0&&rowsArr[idx].nextSibling)rowsArr[idx].after(panel);else $('#agent-list').appendChild(panel);
}
function switchAgentTab(name,tab){
  ['skills','thread'].forEach(t=>{
    const btn=document.getElementById('atab-'+t+'-'+name);
    const content=document.getElementById('atab-content-'+t+'-'+name);
    if(t===tab){btn.style.color='var(--accent)';btn.style.borderBottom='2px solid var(--accent)';if(content)content.style.display=''}
    else{btn.style.color='var(--text3)';btn.style.borderBottom='none';if(content)content.style.display='none'}
  });
}
async function renameAgent(name){const n=prompt('新名称:',name);if(!n||n===name)return;await fetch('/agents/'+encodeURIComponent(name)+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({new_name:n})});refreshAgents()}
async function toggleSkill(an,sn,on){
  const ar=await fetch('/agents').then(r=>r.json());const a=ar.find(x=>x.name===an);if(!a)return;
  const sk=new Set(a.all_skills);if(on)sk.add(sn);else sk.delete(sn);
  await fetch('/agents/'+encodeURIComponent(an)+'/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skills:[...sk]})});refreshAgents();
}
async function loadSkillPool(){
  const r=await fetch('/skills');const sk=await r.json();
  const total=sk.length;
  $('#sp-count').textContent=total+' 个技能';
  // Group by category
  const groups={code:[],search:[],office:[],special:[]};
  sk.forEach(s=>{const c=skillCats[s.name]||'code';groups[c].push(s)});
  // Flatten and limit initially to ~10 items
  const flat=[];['code','search','office','special'].forEach(cat=>{groups[cat].forEach(s=>flat.push({...s,_cat:cat}))});
  const visible=flat.slice(0,10),hidden=flat.slice(10);
  let html='';
  let lastCat='';
  visible.forEach(s=>{
    if(s._cat!==lastCat){html+='<div class="skill-cat-label">'+catLabels[s._cat]+'</div><div class="skill-grid">';lastCat=s._cat}
    html+='<div class="skill-chip" draggable="true" ondragstart="dragSkill(event,\''+s.name+'\')" title="'+s.capabilities.join(', ')+'">';
    html+='<span class="sk-icon '+catIconCls[s._cat]+'">'+(s.name.charAt(0).toUpperCase())+'</span>';
    html+='<span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+s.name+'</span></div>';
    if(visible.indexOf(s)===visible.length-1||visible[visible.indexOf(s)+1]?._cat!==s._cat)html+='</div>';
  });
  if(hidden.length>0){
    html+='<div id="skill-pool-hidden" style="display:none">';
    hidden.forEach(s=>{
      if(s._cat!==lastCat){html+='<div class="skill-cat-label">'+catLabels[s._cat]+'</div><div class="skill-grid">';lastCat=s._cat}
      html+='<div class="skill-chip" draggable="true" ondragstart="dragSkill(event,\''+s.name+'\')" title="'+s.capabilities.join(', ')+'">';
      html+='<span class="sk-icon '+catIconCls[s._cat]+'">'+(s.name.charAt(0).toUpperCase())+'</span>';
      html+='<span style="flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'+s.name+'</span></div>';
    });
    html+='</div>';
    $('#sp-expand-btn').style.display='block';
  }else{$('#sp-expand-btn').style.display='none'}
  if(lastCat&&html.endsWith('</div>')){} // HTML already closed
  $('#skill-pool').innerHTML=html;
  // Re-bind expand button
  window._skillPoolExpanded=false;
  $('#sp-expand-btn').textContent='查看全部技能 ('+total+' 个) ▼';
}
async function showNewAgent(){
  $('#new-agent-modal').classList.add('open');$('#na-name').value='';$('#na-prompt').value='';
  const r=await fetch('/skills');const sk=await r.json();
  $('#na-skills').innerHTML=sk.map(s=>'<label><input type="checkbox" value="'+s.name+'" class="na-skill-cb"> '+s.name+'</label>').join('');
}
function closeNewAgent(){$('#new-agent-modal').classList.remove('open')}
async function createAgent(){
  const n=$('#na-name').value.trim();if(!n)return alert('请输入名称');
  const skills=[...document.querySelectorAll('.na-skill-cb:checked')].map(cb=>cb.value);
  const p=$('#na-prompt').value.trim()||null;
  await fetch('/agents/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,skills:skills,system_prompt:p})});
  closeNewAgent();refreshAgents();
}