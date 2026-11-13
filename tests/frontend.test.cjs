const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function element(id = '') {
  const classes = new Set();
  return { id, value: '', textContent: '', innerHTML: '', style: {}, dataset: {}, children: [], disabled: false,
    classList: { add: (...v) => v.forEach(x => classes.add(x)), remove: (...v) => v.forEach(x => classes.delete(x)), contains: v => classes.has(v), toggle(v) { if (classes.has(v)) { classes.delete(v); return false; } classes.add(v); return true; } },
    listeners: {}, addEventListener(name, fn) { this.listeners[name] = fn; },
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
    querySelector(selector) { if (selector === '.content') return this.content ||= element(); return null; },
    querySelectorAll() { return []; }, setAttribute(k,v) { this[k]=v; }, removeAttribute(k) { delete this[k]; },
    focus() {}, select() {}, remove() {}, closest() { return null; }, contains() { return false; },
  };
}

function app() {
  const nodes = new Map();
  const get = id => { if (!nodes.has(id)) nodes.set(id, element(id)); return nodes.get(id); };
  const listeners = {};
  const calls = [];
  const sandbox = { console, URL, Blob, Date, Math, JSON, Promise, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
    location: { protocol: 'http:', host: 'localhost' }, localStorage: { getItem() {}, setItem() {} },
    document: { readyState: 'loading', body: element(), documentElement: element(), querySelector: s => get(s.replace(/^#/, '')), getElementById: get, createElement: () => element(), querySelectorAll: () => [], addEventListener: (n,f) => { (listeners[n] ||= []).push(f); } },
    fetch: async (url, options) => { calls.push({ url, options }); return { ok: true, json: async () => url === '/sessions/create' ? { session_id: 'session-1' } : [] }; },
    WebSocket: Object.assign(function () { this.readyState = 1; }, { OPEN: 1 }),
    alert() {}, confirm: () => true, prompt: () => null,
  };
  sandbox.window = sandbox; sandbox.innerWidth = 1440; sandbox.addEventListener = () => {};
  const ctx = vm.createContext(sandbox);
  for (const name of ['state','auth','ws','ui','panels','sessions','agents','skills','modals','extras']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js', name + '.js'),'utf8'), ctx, { filename: name + '.js' });
  vm.runInContext(`showToast=()=>{};addNotification=()=>{};refreshAgents=()=>{};loadHist=()=>{};loadSessionList=async()=>{};updateMetrics=()=>{};updateWorkspace=()=>{};updateWSMeta=()=>{};scrollDown=()=>{};_token='test-token';_user={role:'developer',display_name:'测试'};wsOk=true;`, ctx);
  return { ctx, get, calls, listeners, run: source => vm.runInContext(source, ctx) };
}

test('send waits for session creation and sends its explicit id once', async () => {
  const a=app(); let resolve; const sent=[];
  a.ctx.fetch=async (url,options)=>{ a.calls.push({url,options}); if(url==='/sessions/create') return {ok:true,json:()=>new Promise(r=>resolve=r)}; return {ok:true,json:async()=>[]}; };
  a.ctx.socket={readyState:1,send:raw=>sent.push(JSON.parse(raw))}; a.run('ws=socket'); a.get('inp').value='第一条消息';
  const sending=a.ctx.send(); await new Promise(setImmediate);
  assert.equal(sent.length,0,'must not send before session exists');
  assert.equal(a.run('streaming'),true,'creation must block a second send/session switch');
  resolve({session_id:'saved-session'}); await sending;
  assert.equal(sent.length,1); assert.equal(sent[0].session_id,'saved-session');
  assert.equal(a.calls.some(c=>c.url==='/sessions/message'),false);
});

test('failed session creation preserves the typed draft and unlocks composer',async()=>{
  const a=app(); a.ctx.socket={readyState:1,send(){assert.fail('must not send after create failure')}};a.run('ws=socket');a.ctx.fetch=async()=>{throw new Error('offline')}; a.get('inp').value='保留这个草稿';
  await a.ctx.send(); assert.equal(a.get('inp').value,'保留这个草稿'); assert.equal(a.run('streaming'),false); assert.equal(a.get('inp').disabled,false);
});

test('markdown escapes hostile content and keeps code fences literal',()=>{
  const a=app(); const html=a.ctx.renderMD('```html\n<img src=x onerror=alert(1)>\n**literal**\n```\n# 标题');
  assert.ok(!html.includes('<img')); assert.ok(html.includes('&lt;img')); assert.ok(html.includes('**literal**'),'code must not be formatted as bold'); assert.ok(!html.includes('<code><br>')); assert.ok(html.includes('<h2>标题</h2>'));
});

test('result displays aggregate output without a browser persistence call',()=>{
  const a=app(); const response=element(); a.ctx.ensureAI=()=>response; a.run('streaming=true;_currentSid="s";_textBuf="partial"');
  a.ctx.onDone({content:'完整聚合结果',session_id:'s',latency:1});
  assert.ok(response.querySelector('.content').innerHTML.includes('完整聚合结果'));
  assert.equal(a.calls.some(c=>c.url==='/sessions/message'),false); assert.equal(a.run('streaming'),false);
});

test('expired authentication response unlocks the composer and requests a new login',()=>{
  const a=app();const sockets=[];
  a.ctx.WebSocket=function(url){this.readyState=1;sockets.push(this)};
  a.run('streaming=true;_runState="working";conn()');
  sockets[0].onmessage({data:JSON.stringify({event:'auth',data:{error:'请先登录'}})});
  assert.equal(a.run('streaming'),false);
  assert.equal(a.get('login-overlay').style.display,'flex');
  assert.equal(a.get('login-err').textContent,'登录已失效，请重新登录。');
});

test('new and loaded sessions cannot replace an in-flight conversation',async()=>{
  const a=app(); a.run('streaming=true;_currentSid="active"');
  await a.ctx.newSession(); await a.ctx.loadSession('other');
  assert.equal(a.run('_currentSid'),'active'); assert.equal(a.calls.length,0);
});

test('local search Enter selects the highlighted result without dispatching a model',async()=>{
  const a=app(); const selected=[]; a.ctx.loadSession=id=>selected.push(id); a.ctx.socket={send:()=>assert.fail('search must not invoke a model')}; a.run('ws=socket;_searchResults=[{type:"session",id:"found",title:"Found"}];_searchIdx=0'); a.get('global-search').value='Found';
  await a.ctx.doGlobalSearch(); assert.deepEqual(selected,['found']);
});

test('new session resets metrics and workspace even without a session id',async()=>{
  const a=app(); a.get('ws-progress-fill').style.width='100%'; a.get('ws-name-text').textContent='旧会话'; a.run('_currentSid="old";_taskHistory=[{success:true}]');
  await a.ctx.newSession(); assert.equal(a.get('ws-progress-fill').style.width,'0%'); assert.equal(a.get('ws-name-text').textContent,'新会话'); assert.equal(a.run('_taskHistory.length'),0);
});

test('composer Enter ignores IME and Shift+Enter',()=>{
  const a=app(); let sends=0; a.ctx.send=()=>sends++;
  const handler=a.get('inp').listeners.keydown; assert.equal(typeof handler,'function');
  handler({key:'Enter',isComposing:true,preventDefault(){}}); handler({key:'Enter',shiftKey:true,preventDefault(){}}); assert.equal(sends,0);
  handler({key:'Enter',preventDefault(){}}); assert.equal(sends,1);
});

test('system metrics render server values and label unavailable values',async()=>{
  const a=app(); a.ctx.fetch=async()=>({ok:true,json:async()=>({cpu_percent:12.5,memory_percent:null,disk_percent:40})});
  await a.ctx.updateSystemStatus(); assert.equal(a.get('sys-cpu').textContent,'13%'); assert.equal(a.get('sys-mem').textContent,'不可用'); assert.equal(a.get('sys-disk').textContent,'40%');
});

test('session timestamps use Unix seconds supplied by the backend',()=>{
  const a=app();const seconds=Date.UTC(2026,8,27)/1000;
  assert.equal(a.ctx.formatSessionDate(seconds),new Date(seconds*1000).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'}));
});

test('result uses backend summary token totals for estimated cost',()=>{
  const a=app();a.ctx.ensureAI=()=>element();a.run('streaming=true');
  a.ctx.onDone({content:'结果',summary:{tokens:{total:1500}}});assert.equal(a.run('_taskHistory[0].tokens'),1500);
});

test('DAG escapes untrusted descriptions and agent names',()=>{
  const a=app();a.ctx.drawDAG([{agent:'<img src=x>',description:'<script>x</script>'}]);
  assert.ok(!a.get('dag-svg').innerHTML.includes('<img'));assert.ok(!a.get('dag-svg').innerHTML.includes('<script>'));
});

test('text attachments read contents and reject unsupported binary files',async()=>{
  const a=app();a.get('inp').value='已有草稿';await a.ctx.insertTextFile({name:'notes.md',size:30,type:'text/markdown',text:async()=>'文件内容'});
  assert.ok(a.get('inp').value.includes('已有草稿'));assert.ok(a.get('inp').value.includes('文件内容'));
  await assert.rejects(a.ctx.insertTextFile({name:'photo.png',size:1,type:'image/png'}),/仅支持文本/);
});

test('slash persistence explicitly binds both messages to the active session',async()=>{
  const a=app();a.run('_currentSid="slash-session";streaming=true');a.ctx.fetch=async(url,options)=>{a.calls.push({url,options});return {ok:true,json:async()=>url==='/slash'?{result:'完成'}:{ok:true}}};
  await a.ctx.handleSlash('/review test');const messages=a.calls.filter(c=>c.url==='/sessions/message').map(c=>JSON.parse(c.options.body));
  assert.deepEqual(messages.map(m=>m.session_id),['slash-session','slash-session']);assert.deepEqual(messages.map(m=>m.role),['user','assistant']);assert.equal(a.run('streaming'),false);
});
