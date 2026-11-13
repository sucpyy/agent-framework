"""Claude Code 全套工具集 v3

Bash / Read / Write / Edit / Glob / Grep / TodoWrite / SpawnAgent
搜索: web_search.py (缓存+过滤+抓取联动)
"""

from __future__ import annotations
import asyncio, fnmatch, json, os, re, shutil, subprocess, sys, time, uuid
from pathlib import Path
from typing import Any
from .base import Skill, SkillContract
from .workspace_paths import display_workspace_path, get_workspace_root, resolve_workspace_path
from .web_search import WebSearchSkill

# ═══════════════════════════════════════════════════════════
# Bash
# ═══════════════════════════════════════════════════════════
BASH_SCHEMA={"name":"bash","description":"在项目工作区根目录执行终端命令。Windows 使用 Git Bash（未安装时使用 PowerShell），macOS/Linux 使用 Bash。","input_schema":{"type":"object","properties":{"command":{"type":"string","description":"要执行的命令"},"description":{"type":"string"},"timeout":{"type":"integer","description":"超时毫秒数，默认 120000"},"run_in_background":{"type":"boolean"}},"required":["command"]}}
class BashSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="bash",capabilities=["shell","cli","execute"],requires=["command"],produces=["stdout","stderr","exit_code"],cost_score=0.3,latency_score=0.5,quality_score=0.9))
    def to_tool_schema(self)->dict:return BASH_SCHEMA
    async def execute(self,**kw)->dict:
        cmd=str(kw.get("command","")).strip();timeout_ms=kw.get("timeout",120000)
        if not cmd:return {"stdout":"","stderr":"command 不能为空","exit_code":2}
        try:timeout_ms=max(1000,min(600000,int(timeout_ms)))
        except (TypeError,ValueError):timeout_ms=120000
        if kw.get("run_in_background"):
            try:
                _,args=self._shell_command(cmd)
                process=await asyncio.create_subprocess_exec(*args,cwd=str(get_workspace_root()),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                return {"stdout":"","stderr":"","exit_code":0,"background":True,"pid":process.pid,"cwd":str(get_workspace_root())}
            except Exception as ex:return{"stdout":"","stderr":str(ex),"exit_code":1,"background":False}
        return await self._run(cmd,timeout_ms)
    async def _run(self,cmd,to):
        process=None
        try:
            executable,args=self._shell_command(cmd)
            process=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,cwd=str(get_workspace_root()))
            o,e=await asyncio.wait_for(process.communicate(),timeout=to/1000)
            return{"stdout":o.decode("utf-8",errors="replace")[-8000:],"stderr":e.decode("utf-8",errors="replace")[-2000:],"exit_code":process.returncode if process.returncode is not None else 0,"cwd":str(get_workspace_root())}
        except asyncio.TimeoutError:
            if process and process.returncode is None:
                process.kill()
                await process.communicate()
            return{"stdout":"","stderr":f"Timeout {to}ms","exit_code":124}
        except Exception as ex:return{"stdout":"","stderr":str(ex),"exit_code":1}

    @staticmethod
    def _shell_command(command:str)->tuple[str,list[str]]:
        if os.name=="nt":
            configured=os.environ.get("AGENT_BASH_PATH","").strip()
            git=shutil.which("git")
            candidates=[Path(configured)] if configured else []
            if git:
                git_bin=Path(git).resolve().parent.parent/"bin"/"bash.exe"
                candidates.append(git_bin)
            candidates.extend([Path(r"C:\Program Files\Git\bin\bash.exe"),Path(r"C:\Program Files\Git\usr\bin\bash.exe")])
            for bash in candidates:
                if bash.is_file():return str(bash),[str(bash),"--noprofile","--norc","-c",command]
            powershell=shutil.which("pwsh") or shutil.which("powershell.exe") or shutil.which("powershell")
            if not powershell:raise FileNotFoundError("未找到 Git Bash 或 PowerShell")
            return powershell,[powershell,"-NoLogo","-NoProfile","-NonInteractive","-Command",command]
        bash=shutil.which("bash") or "/bin/sh"
        return bash,[bash,"--noprofile","--norc","-c",command]

# ═══════════════════════════════════════════════════════════
# Read
# ═══════════════════════════════════════════════════════════
READ_SCHEMA={"name":"read_file","description":"读取项目文件。相对路径从项目工作区根目录解析；绝对路径按指定位置读取。","input_schema":{"type":"object","properties":{"file_path":{"type":"string","description":"相对项目根目录的路径或绝对路径，不能为空"},"offset":{"type":"integer","description":"起始行（从 1 开始）"},"limit":{"type":"integer","description":"最多读取行数"}},"required":["file_path"]}}
class ReadSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="read_file",capabilities=["file_read","read"],requires=["file_path"],produces=["content","file_path","total_lines"],cost_score=0.1,latency_score=0.2,quality_score=1.0))
    def to_tool_schema(self)->dict:return READ_SCHEMA
    async def execute(self,**kw)->dict:
        path=kw.get("file_path","");offset=max(0,(kw.get("offset",1)-1));limit=kw.get("limit")
        try:
            p=resolve_workspace_path(path)
            if not p.exists():return{"content":f"Not found: {display_workspace_path(p)}","file_path":display_workspace_path(p),"total_lines":0}
            if not p.is_file():return{"content":f"Not a file: {display_workspace_path(p)}","file_path":display_workspace_path(p),"total_lines":0}
            lines=p.read_text(encoding="utf-8",errors="replace").split("\n");total=len(lines)
            if limit is not None:lines=lines[offset:offset+max(0,limit)]
            elif offset>0:lines=lines[offset:]
            else:lines=lines[:min(2000,total)]
            return{"content":"\n".join(f"{i+offset+1}\t{l}" for i,l in enumerate(lines)) or "(empty)","file_path":display_workspace_path(p),"total_lines":total}
        except Exception as e:return{"content":f"Error: {e}","file_path":str(path),"total_lines":0,"error":str(e)}

# ═══════════════════════════════════════════════════════════
# Write
# ═══════════════════════════════════════════════════════════
WRITE_SCHEMA={"name":"write_file","description":"在项目工作区创建或覆写 UTF-8 文件。相对路径从项目工作区根目录解析；绝对路径按指定位置写入。","input_schema":{"type":"object","properties":{"file_path":{"type":"string","description":"相对项目根目录的路径或绝对路径，不能为空"},"content":{"type":"string"}},"required":["file_path","content"]}}
class WriteSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="write_file",capabilities=["file_write","write"],requires=["file_path","content"],produces=["file_path","written","size"],cost_score=0.1,latency_score=0.3,quality_score=1.0))
    def to_tool_schema(self)->dict:return WRITE_SCHEMA
    async def execute(self,**kw)->dict:
        path=kw.get("file_path","");content=kw.get("content","")
        try:
            p=resolve_workspace_path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,encoding="utf-8");return{"file_path":display_workspace_path(p),"written":True,"size":len(content)}
        except Exception as e:return{"file_path":path,"written":False,"error":str(e)}

# ═══════════════════════════════════════════════════════════
# Edit
# ═══════════════════════════════════════════════════════════
EDIT_SCHEMA={"name":"edit_file","description":"精确字符串替换。","input_schema":{"type":"object","properties":{"file_path":{"type":"string"},"old_string":{"type":"string"},"new_string":{"type":"string"},"replace_all":{"type":"boolean"}},"required":["file_path","old_string","new_string"]}}
class EditSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="edit_file",capabilities=["file_edit","edit"],requires=["file_path","old_string","new_string"],produces=["file_path","modified","replacements"],cost_score=0.15,latency_score=0.3,quality_score=1.0))
    def to_tool_schema(self)->dict:return EDIT_SCHEMA
    async def execute(self,**kw)->dict:
        path=kw.get("file_path","");old=kw.get("old_string","");new=kw.get("new_string","");ra=kw.get("replace_all",False)
        try:
            if not old:return{"file_path":str(path),"modified":False,"error":"old_string 不能为空"}
            p=resolve_workspace_path(path)
            if not p.exists() or not p.is_file():return{"file_path":display_workspace_path(p),"modified":False,"error":"File not found"}
            text=p.read_text(encoding="utf-8");count=text.count(old)
            if count==0:return{"file_path":path,"modified":False,"error":"old_string not found"}
            if count>1 and not ra:return{"file_path":path,"modified":False,"error":f"Found {count} times"}
            text=text.replace(old,new) if ra else text.replace(old,new,1)
            p.write_text(text,encoding="utf-8")
            return{"file_path":display_workspace_path(p),"modified":True,"replacements":count if ra else 1}
        except Exception as e:return{"file_path":path,"modified":False,"error":str(e)}

# ═══════════════════════════════════════════════════════════
# Glob
# ═══════════════════════════════════════════════════════════
GLOB_SCHEMA={"name":"glob","description":"从项目工作区按 glob 模式查找文件；默认搜索整个工作区。path 可指定相对目录或绝对目录。","input_schema":{"type":"object","properties":{"pattern":{"type":"string","description":"例如 **/*.py 或 static/*.css"},"path":{"type":"string","description":"搜索根目录，默认为项目工作区根目录"}},"required":["pattern"]}}
class GlobSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="glob",capabilities=["file_search","glob"],requires=["pattern"],produces=["matches","count"],cost_score=0.1,latency_score=0.4,quality_score=1.0))
    def to_tool_schema(self)->dict:return GLOB_SCHEMA
    async def execute(self,**kw)->dict:
        pattern=kw.get("pattern","");base=kw.get("path","")
        if not pattern:return{"matches":[],"count":0,"error":"pattern 不能为空"}
        try:
            root=resolve_workspace_path(base,default=".")
            matches=sorted((x for x in root.glob(pattern) if x.is_file()),key=lambda x:str(x).casefold())
            return{"matches":[display_workspace_path(x) for x in matches[:200]],"count":len(matches),"root":display_workspace_path(root)}
        except Exception as e:return{"matches":[],"count":0,"error":str(e)}

# ═══════════════════════════════════════════════════════════
# Grep
# ═══════════════════════════════════════════════════════════
GREP_SCHEMA={"name":"grep","description":"在项目工作区搜索文件内容（正则）。path 默认是项目根目录，glob 用于筛选文件名；可选择 content、files_with_matches 或 count。","input_schema":{"type":"object","properties":{"pattern":{"type":"string"},"path":{"type":"string"},"glob":{"type":"string"},"output_mode":{"type":"string","enum":["content","files_with_matches","count"]},"-A":{"type":"integer"},"-B":{"type":"integer"},"-C":{"type":"integer"},"-i":{"type":"boolean"},"head_limit":{"type":"integer"}},"required":["pattern"]}}
class GrepSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="grep",capabilities=["content_search","grep"],requires=["pattern"],produces=["matches","count"],cost_score=0.15,latency_score=0.5,quality_score=1.0))
    def to_tool_schema(self)->dict:return GREP_SCHEMA
    async def execute(self,**kw)->dict:
        pat=kw.get("pattern","");base=kw.get("path","");gf=kw.get("glob","");mode=kw.get("output_mode","content")
        ic=kw.get("-i",False);head=kw.get("head_limit",250);ca=kw.get("-A",0);cb=kw.get("-B",0);cc=kw.get("-C",0)
        import re as _re
        if not pat:return{"matches":[],"count":0,"error":"pattern 不能为空"}
        try:rx=_re.compile(pat,_re.IGNORECASE if ic else 0)
        except _re.error as e:return{"matches":[f"Regex: {e}"],"count":0}
        matches=[];count=0
        try:
            bp=resolve_workspace_path(base,default=".")
            files=sorted(bp.rglob("*") if bp.is_dir() else [bp],key=lambda f:str(f).casefold())
            ignored={".git",".memory",".sessions",".venv","venv","node_modules","__pycache__"}
            files=[f for f in files if not ignored.intersection(f.parts) and (not gf or fnmatch.fnmatch(f.name,gf))]
        except Exception as e:return{"matches":[],"count":0,"error":str(e)}
        for fp in files:
            if not fp.is_file():continue
            try:
                if fp.stat().st_size>500_000:continue
                lines=fp.read_text(encoding="utf-8",errors="replace").split("\n")
                for i,line in enumerate(lines):
                    if rx.search(line):
                        count+=1
                        if mode=="count":continue
                        elif mode=="files_with_matches":matches.append(display_workspace_path(fp));break
                        else:
                            if count>head:break
                            s=max(0,i-(cb or cc));e=min(len(lines),i+(ca or cc)+1)
                            for j in range(s,e):matches.append(f"{display_workspace_path(fp)}:{j+1}:{' >' if j==i else '  '}{lines[j]}")
            except:pass
            if count>head:break
        if mode=="count":return{"matches":[f"{count} matches"],"count":count}
        return{"matches":matches[:head],"count":count}

# ═══════════════════════════════════════════════════════════
# TodoWrite
# ═══════════════════════════════════════════════════════════
TODOWRITE_SCHEMA={"name":"todo_write","description":"任务列表管理。","input_schema":{"type":"object","properties":{"todos":{"type":"array","items":{"type":"object","properties":{"content":{"type":"string"},"status":{"type":"string","enum":["pending","in_progress","completed"]},"activeForm":{"type":"string"}},"required":["content","status","activeForm"]}}},"required":["todos"]}}
class TodoWriteSkill(Skill):
    def __init__(self):
        super().__init__(SkillContract(name="todo_write",capabilities=["task_management","todo"],requires=["todos"],produces=["todos","summary"],cost_score=0.05,latency_score=0.1,quality_score=1.0))
        self._path=resolve_workspace_path(".workspace_data/todos.json")
        try:
            loaded=json.loads(self._path.read_text(encoding="utf-8"))
            self._todos=loaded if isinstance(loaded,list) else []
        except (OSError,json.JSONDecodeError):self._todos=[]
    def to_tool_schema(self)->dict:return TODOWRITE_SCHEMA
    async def execute(self,**kw)->dict:
        todos=kw.get("todos")
        if not isinstance(todos,list):return {"todos":self._todos,"summary":"任务列表未更新","error":"todos 必须是数组"}
        clean=[]
        for item in todos:
            if not isinstance(item,dict) or not str(item.get("content","")).strip():
                return {"todos":self._todos,"summary":"任务列表未更新","error":"每项都需要 content 和有效 status"}
            status=item.get("status","pending")
            if status not in {"pending","in_progress","completed"}:
                return {"todos":self._todos,"summary":"任务列表未更新","error":f"无效任务状态：{status}"}
            clean.append({"content":str(item["content"]).strip(),"status":status,"activeForm":str(item.get("activeForm",item["content"])).strip()})
        self._path=resolve_workspace_path(".workspace_data/todos.json")
        try:
            self._path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(clean,ensure_ascii=False,indent=2),encoding="utf-8")
            temporary.replace(self._path)
        except OSError as e:return {"todos":self._todos,"summary":"任务列表未更新","error":f"保存任务失败：{e}"}
        self._todos=clean
        counts={"pending":0,"in_progress":0,"completed":0}
        for t in clean:counts[t["status"]]+=1
        return{"todos":self._todos,"summary":f"{len(clean)} 项任务：{counts['completed']} 已完成，{counts['in_progress']} 进行中，{counts['pending']} 待处理"}

# ═══════════════════════════════════════════════════════════
# SpawnAgent
# ═══════════════════════════════════════════════════════════
SPAWN_SCHEMA={"name":"spawn_agent","description":"派发子Agent并行执行。","input_schema":{"type":"object","properties":{"agents":{"type":"array","items":{"type":"object","properties":{"agent_type":{"type":"string"},"task":{"type":"string"},"input":{"type":"object"}},"required":["agent_type","task"]}}},"required":["agents"]}}
class SpawnAgentSkill(Skill):
    def __init__(self):super().__init__(SkillContract(name="spawn_agent",capabilities=["agent","spawn","parallel"],requires=["agents"],produces=["results","summary"],cost_score=0.4,latency_score=0.5,quality_score=0.9));self._registry=None
    def to_tool_schema(self)->dict:return SPAWN_SCHEMA
    async def execute(self,**kw)->dict:
        agents=kw.get("agents",[])
        if not self._registry:return{"results":[],"summary":"No registry","error":True}
        from ..agent.base import AgentContext
        async def run_one(a):
            agent_type=str(a.get("agent_type", ""))
            ag=self._registry.get(agent_type)
            if not ag:return{"agent":a["agent_type"],"error":"Not found"}
            task=str(a.get("task", "")).strip()
            input_data=dict(a.get("input") or {})
            input_data.setdefault("query",task)
            input_data.setdefault("description",task)
            ctx=AgentContext(agent_name=ag.name,task_id=uuid.uuid4().hex[:12],input_data=input_data)
            try:return {"agent":agent_type,**(await ag.execute(ctx))}
            except Exception as e:return{"agent":agent_type,"error":str(e)}
        results=await asyncio.gather(*[run_one(a) for a in agents],return_exceptions=True)
        return{"results":[r if not isinstance(r,Exception) else{"error":str(r)} for r in results],"summary":f"{len(results)} agent(s)"}

ALL_CLAUDE_TOOLS=[BashSkill,ReadSkill,WriteSkill,EditSkill,GlobSkill,GrepSkill,TodoWriteSkill,SpawnAgentSkill,WebSearchSkill]
