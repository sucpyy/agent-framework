// ══ skills ══
// ─── Drag & Drop Skills ────────────────────────
let dragSkillName=null;
function dragSkill(e,name){dragSkillName=name}
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('drop',async e=>{
  e.preventDefault();
  const row=e.target.closest('.agent-row');
  if(!row||!dragSkillName)return;
  const an=row.querySelector('.a-name')?.textContent;if(!an)return;
  const ar=await fetch('/agents').then(r=>r.json());const a=ar.find(x=>x.name===an);if(!a)return;
  const sk=new Set(a.all_skills);sk.add(dragSkillName);
  await fetch('/agents/'+encodeURIComponent(an)+'/skills',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skills:[...sk]})});
  refreshAgents();dragSkillName=null;
});