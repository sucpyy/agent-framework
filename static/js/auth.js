// ══ auth ══
let _loginLock=false;
async function doLogin(){
  if(_loginLock)return; // 防重入
  const u=$('#login-user').value.trim(),p=$('#login-pass').value;
  const e=$('#login-err');if(!u||!p){e.textContent='请输入用户名和密码';return}
  _loginLock=true;e.textContent='验证中...';e.style.color='var(--yellow)';
  try{
    const r=await fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});
    const d=await r.json();
    if(d.ok){_token=d.token;_user=d.user;$('#login-overlay').style.display='none';_loginLock=false;onAuth()}
    else{e.textContent=d.error||'登录失败';e.style.color='var(--red)';_loginLock=false}
  }catch(ex){e.textContent='网络错误: '+ex.message;e.style.color='var(--red)';_loginLock=false}
}
function doLogout(){_token='';_user=null;_currentSid=null;_authInitDone=false;_loginLock=false;$('#login-overlay').style.display='flex';$('#login-user').value='';$('#login-pass').value='';$('#login-err').textContent='';location.reload()}
let _authInitDone=false;
function onAuth(){
  const dn=_user.display_name||_user.username;
  $('#ud-avatar').textContent=dn.charAt(0).toUpperCase();
  $('#ud-name').textContent=dn;
  $('#u-name').textContent=dn;
  $('#u-role').textContent=_user.role==='admin'?'管理员':_user.role==='developer'?'研发':'游客';
  $('#admin-btn').style.display=_user.role==='admin'?'inline-block':'none';
  $('#guest-banner').style.display=_user.role==='guest'?'block':'none';
  setBusy(streaming);
  if(_authInitDone)return; // 防止重复初始化 interval
  _authInitDone=true;
  loadMode();refreshAgents();loadSkillPool();loadHist();autoLoadLastSession();
  setInterval(refreshAgents,5000);
  setInterval(updateSystemStatus,3000);updateSystemStatus();
}
