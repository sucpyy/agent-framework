// ══ State — Global variables and metadata ══

const $=s=>document.querySelector(s);
let ws,wsOk=false,streaming=false,_currentSid=null;
let _token='',_user=null,_textBuf='',_toolCount=0,_totalTokens=0,_totalCost=0;
let _taskHistory=[];
let _notifications=[],_sidebarCollapsed=false,_skillPoolCollapsed=false;
let _currentResponse=null,_sessionLoading=false,_sessionMeta=null,_sessions=[];
let _lastPrompt='',_activeTaskId=null,_lastTaskId=null,_runState='idle',_subtasks=[];

const agentMeta={
  general_agent:{cat:'通用',icon:'G',cls:'a-general',color:'var(--accent)'},
  research_agent:{cat:'研究',icon:'R',cls:'a-research',color:'var(--green)'},
  code_agent:{cat:'编码',icon:'C',cls:'a-code',color:'var(--orange)'},
  office_agent:{cat:'办公',icon:'O',cls:'a-office',color:'var(--purple)'},
};
const skillCats={
  bash:'code',read_file:'code',write_file:'code',edit_file:'code',glob:'code',grep:'code',
  web_search:'search',search_and_fetch:'search',
  email:'office',document:'office',calendar:'office',translate:'office',clipboard:'office',
  todo_write:'special',spawn_agent:'special',
  search:'special',code:'special',
};
const catLabels={code:'💻 代码工具',search:'🔍 搜索工具',office:'📋 办公工具',special:'⚡ 专用工具'};
const catIconCls={code:'sk-cat-code',search:'sk-cat-search',office:'sk-cat-office',special:'sk-cat-special'};
