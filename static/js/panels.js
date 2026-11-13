// ══ panels ══
// ─── Metrics ──────────────────────────────────
function updateMetrics(){
  const hist=_taskHistory;
  const total=hist.length;
  const success=hist.filter(t=>t.success).length;
  const avgLat=total>0?hist.reduce((s,t)=>s+(t.latency||0),0)/total:0;
  const tools=hist.reduce((s,t)=>s+(t.tools||0),0);
  const tokens=hist.reduce((s,t)=>s+(t.tokens||0),0);
  // Cost: ~$0.002/1K tokens for input, ~$0.006/1K tokens for output (DeepSeek pricing)
  const cost=total>0?hist.reduce((s,t)=>s+(t.cost||((t.tokens||0)*0.000004)),0):0;
  _totalCost=cost;
  $('#met-success').textContent=total>0?Math.round(success/total*100)+'%':'—%';
  $('#met-latency').textContent=total>0?avgLat.toFixed(1)+'s':'—s';
  $('#met-tools').textContent=tools;
  $('#met-cost').textContent='$'+cost.toFixed(3);
}

// ─── System Status ────────────────────────────
async function updateSystemStatus(){
  const render=(name,bar,value)=>{
    const label=$('#'+name),fill=$('#'+bar);
    if(value==null||!Number.isFinite(Number(value))){label.textContent='不可用';fill.style.width='0%';return}
    const pct=Math.max(0,Math.min(100,Number(value)));
    label.textContent=Math.round(pct)+'%';fill.style.width=pct+'%';
  };
  try{
    const response=await fetch('/system/status');
    if(!response.ok)throw new Error('system status unavailable');
    const status=await response.json();
    render('sys-cpu','sys-cpu-bar',status.cpu_percent);
    render('sys-mem','sys-mem-bar',status.memory_percent);
    render('sys-disk','sys-disk-bar',status.disk_percent);
  }catch{
    for(const [name,bar] of [['sys-cpu','sys-cpu-bar'],['sys-mem','sys-mem-bar'],['sys-disk','sys-disk-bar']])render(name,bar,null);
  }
  const netEl=$('#sys-net');
  netEl.textContent=wsOk?'已连接':'已断开';
  netEl.style.color=wsOk?'var(--green)':'var(--red)';
}

// ─── DAG ──────────────────────────────────────
let _dagData=null;
function drawDAG(subtasks){
  _dagData={subtasks:subtasks};
  const svg=document.getElementById('dag-svg');
  if(!subtasks||!subtasks.length){svg.innerHTML='<text x="130" y="85" fill="#64748b" font-size="10" text-anchor="middle">等待任务...</text>';return}
  const colors={research_agent:'#22c5a1',code_agent:'#ff9f43',general_agent:'#3b82f6',office_agent:'#8b5cf6'};
  const n=subtasks.length;
  let html='<defs><marker id="arrowhead" markerWidth="6" markerHeight="4" refX="6" refY="2" orient="auto"><polygon points="0 0,6 2,0 4" fill="#333840"/></marker></defs>';
  // User Request node (top center — green)
  html+='<rect x="90" y="5" width="80" height="20" rx="5" fill="rgba(34,197,161,.12)" stroke="#22c5a1" stroke-width="1.5"/><text x="130" y="18" fill="#22c5a1" font-size="8" text-anchor="middle" font-weight="700">用户请求</text>';
  // Orchestrator node (purple)
  html+='<rect x="90" y="35" width="80" height="20" rx="5" fill="rgba(139,92,246,.12)" stroke="#8b5cf6" stroke-width="1.5"/><text x="130" y="48" fill="#a78bfa" font-size="8" text-anchor="middle" font-weight="600">Orchestrator</text>';
  html+='<line x1="130" y1="25" x2="130" y2="35" stroke="#4a3d6e" stroke-width="1.5" marker-end="url(#arrowhead)"/>';
  // Agent nodes — dynamic spacing
  const totalW=n*56+(n-1)*2;const startX=Math.max(10,(260-totalW)/2);
  for(let i=0;i<n;i++){
    const st=subtasks[i];const x=startX+i*58;const y=96;
    const c=colors[st.agent]||'#3b82f6';
    html+='<rect x="'+x+'" y="'+y+'" width="56" height="30" rx="5" stroke="'+c+'" fill="rgba(0,0,0,.25)" stroke-width="1.5"/>';
    html+='<text x="'+(x+28)+'" y="'+(y+12)+'" fill="'+c+'" font-size="7" text-anchor="middle" font-weight="700">'+esc((st.agent||'agent').replace('_agent',''))+'</text>';
    html+='<text x="'+(x+28)+'" y="'+(y+22)+'" fill="#94a3b8" font-size="6.5" text-anchor="middle">'+esc((st.description||st.skill||'').substring(0,10))+'</text>';
    html+='<line x1="130" y1="55" x2="'+(x+28)+'" y2="96" stroke="#3d3d5c" stroke-width="1.5" marker-end="url(#arrowhead)"/>';
  }
  // Aggregator node (blue/cyan at bottom)
  html+='<rect x="90" y="140" width="80" height="20" rx="5" fill="rgba(59,130,246,.12)" stroke="#3b82f6" stroke-width="1.5"/><text x="130" y="153" fill="#60a5fa" font-size="8" text-anchor="middle" font-weight="700">结果聚合</text>';
  for(let i=0;i<n;i++){
    const x=startX+i*58;
    html+='<line x1="'+(x+28)+'" y1="126" x2="130" y2="140" stroke="#3d3d5c" stroke-width="1.5"/>';
  }
  svg.innerHTML=html;
}
function toggleDAG(){
  const panel=document.getElementById('dag-full');
  if(!panel)return;
  if(panel.style.display==='none'){
    panel.style.display='block';
    if(_dagData)panel.innerHTML=_dagData.subtasks.map((st,i)=>'<div style="font-size:9px;color:var(--text2);padding:2px 0">'+(i+1)+'. <b style="color:'+({research_agent:'#22c5a1',code_agent:'#ff9f43',general_agent:'#3b82f6'}[st.agent]||'var(--text)')+'">'+esc((st.agent||'agent').replace('_agent',''))+'</b>: '+esc((st.description||st.skill||'').substring(0,40))+'</div>').join('');
    else panel.innerHTML='<span style="font-size:9px;color:var(--text3)">暂无数据</span>';
  }else{panel.style.display='none'}
}

// ─── Score Ring ───────────────────────────────
function drawScoreRing(score){
  const r=36,circ=2*Math.PI*r;
  const pct=Math.min(score/10,1);
  const dash=pct*circ;
  const ring=document.getElementById('score-ring');
  ring.setAttribute('stroke-dasharray',dash+' '+(circ-dash));
  ring.setAttribute('stroke',score>=8?'#22c5a1':score>=6?'#f59e0b':'#ef4444');
  document.getElementById('score-total').textContent=score;
}
