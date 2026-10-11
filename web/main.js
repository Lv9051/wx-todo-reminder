"use strict";
let state=null,filter="active",scope="mine",historyStatus="done",selectMode=false,ownerEdited=false,view="tasks",editing=null,settingsDirty=false,refreshing=false,serverReceived=0;
const selected=new Set();
const pending=new Set(), cache={}, $=s=>document.querySelector(s);
const escapeHTML=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const formatTime=v=>v?new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(v)):"未记录";
const serverNow=()=>state?new Date(new Date(state.server_time).getTime()+performance.now()-serverReceived):new Date();
const bjParts=date=>new Date(date.getTime()+8*3600000).toISOString();
function toast(text,error=false,undo=null){
 const el=$("#toast");$("#toast-text").textContent=text;el.classList.toggle("error",error);el.hidden=false;
 $("#toast-undo").hidden=!undo;$("#toast-undo").onclick=undo?()=>{el.hidden=true;undo()}:null;
 clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.hidden=true,error?8000:undo?6500:3500);
}
async function request(path,data){
 const response=await fetch(path,data?{method:"POST",headers:{"Content-Type":"application/json","X-Reminder-Token":state?.token??""},body:JSON.stringify(data)}:{});
 const value=await response.json();if(!response.ok)throw new Error(value.error||"操作失败");return value;
}
function accept(value){state=value;serverReceived=performance.now();render()}
async function mutate(path,data,success,undo){
 try{accept(await request(path,data));if(success)toast(success,false,undo);return true}
 catch(error){toast(error.message,true);return false}
}
function writeHTML(selector,key,html){if(cache[selector]===key)return;cache[selector]=key;$(selector).innerHTML=html}
function setView(next){
 hideMenu();hidePreview();
 view=next;document.querySelectorAll('.nav[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 for(const v of ["tasks","history","events","settings"])$(`#${v}-view`).hidden=v!==view;
 const titles={tasks:["待办清单","记下来，一件件完成。","先记待办。需要时，再添加提醒。"],history:["历史待办","做完的事，也值得记住。","完成的事项在这里留档，想继续时随时重新开启。"],events:["提醒记录","每一次提醒，都有记录。","看看哪些已经提醒，哪些正在等待重试。"],settings:["提醒设置","按你的节奏，适时提醒。","选择提醒时段，检查桌面通知与微信连接。"]};
 $("#breadcrumb").textContent=titles[view][0];$("#page-title").textContent=titles[view][1];$("#page-description").textContent=titles[view][2];$("#new-task").hidden=view!=="tasks";
}
function render(){
 if(!state)return;const cfg=state.settings,active=state.tasks.filter(t=>t.status==="active"),done=state.tasks.filter(t=>t.status==="done");
 $("#active-count").textContent=active.length;$("#nav-count").textContent=active.length;$("#history-count").textContent=done.length;$("#done-count").textContent=done.length;$("#today-count").textContent=state.today_count;
 $("#active-subtitle").textContent=`${active.filter(t=>t.target_type!=="local"&&!t.reminder_finished).length} 件已安排自动提醒`;
 $("#engine-dot").classList.toggle("running",cfg.enabled);$("#engine-title").textContent=cfg.enabled?(cfg.mode==="preview"?"预览调度已开启":"自动提醒已开启"):"自动提醒已暂停";
 $("#engine-description").textContent=cfg.mode==="preview"?"到点只生成预览记录": "提醒自己弹桌面通知；提醒别人自动发微信";
 $("#toggle-engine").textContent=cfg.enabled?"暂停提醒":"开启提醒";$("#clock").textContent=formatTime(state.server_time)+" · 北京时间";
 $("#notification-state").textContent=state.desktop_notifications?"桌面通知已就绪；关闭页面后仍能提醒。":"请使用桌面软件或 start.bat 启动，让程序驻留托盘。";
 $("#test-notification").disabled=!state.desktop_notifications;$("#version-label").textContent=`v${state.version||"1.2.0"} · 本地保存`;
 document.body.classList.toggle("compact",cfg.compact!==false);
 $("#data-location").textContent=state.data_path?"数据位置："+state.data_path:"";
 $("#queue-count").textContent=state.queue_count?state.queue_count+" 条提醒排队中":"";
 for(const id of selected)if(!state.tasks.some(t=>t.id===id&&!['done','deleted'].includes(t.status)))selected.delete(id);
 const issues=state.events.filter(e=>e.status==='unknown'&&state.tasks.some(t=>t.id===e.task_id&&!['done','deleted'].includes(t.status)));
 $("#attention-banner").hidden=!issues.length;$("#attention-banner").textContent=issues.length?'有 '+new Set(issues.map(e=>e.task_id)).size+' 件事项的发送状态待核对 · 查看提醒记录 →':'';
 $("#widget-state").textContent=({visible:'小窗已显示',hidden:'小窗已关闭',failed:'小窗启动失败，请重启软件或查看运行日志',unavailable:'请在桌面软件中开启小窗'})[state.widget_state]||'';
 renderTasks();renderHistory();renderEvents();
 if(!settingsDirty){const f=$("#settings-form");f.elements.mode.value=cfg.mode;for(const k of ["start","end","daily_limit"])f.elements[k].value=cfg[k];f.elements.weekdays.checked=cfg.weekdays;for(const k of ["compact","sound_enabled","widget_enabled"])f.elements[k].checked=k==="widget_enabled"?Boolean(cfg[k]):cfg[k]!==false;f.elements.startup_enabled.checked=Boolean(state.startup_enabled)}
}
function card(t,history=false){
 const local=t.target_type==="local",self=t.target_type==="self",busy=pending.has(t.id);
 const target=local?'<span class="chip">仅记录</span>':self?'<span class="chip">提醒自己</span>':t.target_type==="contact"?`<span class="chip">私聊</span><span>♧ ${escapeHTML((t.recipients||[t.group_name]).join("、"))}</span>`:`<span class="chip">@ ${escapeHTML((t.recipients||[t.member]).join("、"))}</span><span>▤ ${escapeHTML(t.group_name)}</span>`;
 const cadence=local?"":t.repeat?`<span>◷ 每 ${escapeHTML(t.interval_value??t.interval_hours)} ${{hours:"小时",days:"天",weeks:"周"}[t.interval_unit||"hours"]}</span>`:'<span>◷ 提醒一次</span>';
 const deleted=t.status==='deleted';
 const timing=deleted?'删除于 '+formatTime(t.deleted_at):history?(t.completed_at?"完成于 "+formatTime(t.completed_at):"已完成，停止提醒"):t.status==="paused"?"提醒已暂停":local?"仅记录，不提醒":t.queue_status?({queued:"等待发送",waiting:"等待允许时段",retry:"等待重试",sending:"发送中"}[t.queue_status]+" · "+t.queue_detail):t.reminder_finished?"已提醒，等待完成":"下次提醒 "+formatTime(t.next_due)+(state.settings.enabled?"":" · 全部提醒已暂停");
 const action=(name,label,cls="")=>`<button class="${cls}" data-action="${name}" data-id="${t.id}" ${busy?"disabled":""}>${label}</button>`;
 let actions=deleted?action('undelete','恢复'):history?action("resume","重新开启"):action("complete","✓ 完成","done-button")+(t.status==='active'?(local?action("add-reminder","添加提醒"):action("remind",busy?"处理中…":state.settings.mode==="preview"?"预览一次":"额外提醒")):action('resume','恢复提醒'));
 if(!deleted)actions+=action('more','⋯','more-button');
 const checkbox=selectMode&&!history?`<input class="task-select" type="checkbox" data-select="${t.id}" aria-label="选择 ${escapeHTML(t.title)}" ${selected.has(t.id)?"checked":""}>`:"";
 const ownership=t.owner_scope==='delegated'&&t.owner_name?`<span class="chip">${escapeHTML(t.owner_name)}</span>`:'';
 return `<article class="task-card ${escapeHTML(t.status)}" data-task="${t.id}">${checkbox}<div class="task-marker">${deleted?'↶':history?"✓":t.status==="paused"?"Ⅱ":self?"◷":"↗"}</div><div class="task-body"><h3><button class="task-title" data-action="${deleted?'preview':'edit'}" data-id="${t.id}">${escapeHTML(t.title)}</button></h3><div class="task-meta">${ownership}${target}${cadence}</div><div class="task-time">${escapeHTML(timing)}</div></div><div class="task-actions">${actions}</div></article>`;
}
function empty(title,description,create=false){return `<div class="empty"><div class="empty-icon">✓</div><h3>${title}</h3><p>${description}</p>${create?'<button class="secondary" data-action="new">＋ 添加第一件待办</button>':""}</div>`}
function renderTasks(){
 const q=$("#search").value.trim().toLowerCase(),tasks=visibleTasks();
 const key=JSON.stringify([tasks,scope,filter,q,selectMode,state.settings.enabled,state.settings.mode,[...pending],[...selected]]);
 let html=tasks.map(t=>card(t)).join('');
 if(scope==='delegated'&&tasks.length){const groups=new Map();for(const t of tasks){const name=t.owner_name||(t.recipients||[]).join('、')||'未指定负责人';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(t)}html=[...groups].map(([name,items])=>`<details class="person-group" open><summary>${escapeHTML(name)}<span>${items.length} 项</span></summary><div class="task-list">${items.map(t=>card(t)).join('')}</div></details>`).join('')}
 writeHTML("#task-list",key,tasks.length?html:empty(q?"没找到这件事":filter==="paused"?"没有暂停的待办":"这里还没有待办",q?"换一个关键词试试。":scope==='mine'?"记下自己的事，需要提醒时再设置。":"交办事项单独放在这里，设置后按计划提醒。",!q&&scope==='mine'));
 $("#bulk-bar").hidden=!selectMode;$("#selected-count").textContent='已选 '+selected.size+' 项';$("#remind-selected").disabled=!tasks.some(t=>selected.has(t.id)&&t.status==='active'&&t.target_type!=='local');$("#delete-selected").disabled=!selected.size;
 $("#select-visible").checked=!!tasks.length&&tasks.every(t=>selected.has(t.id));
 document.querySelectorAll('[data-scope]').forEach(b=>b.classList.toggle('selected',b.dataset.scope===scope));
}
function visibleTasks(){const q=$("#search").value.trim().toLowerCase();return state.tasks.filter(t=>!['done','deleted'].includes(t.status)&&(scope==='all'||t.owner_scope===scope)&&(filter==='all'||filter==='reminders'&&t.target_type!=='local'||t.status===filter)&&[t.title,t.owner_name,t.member,t.group_name,...(t.recipients||[])].some(v=>(v||'').toLowerCase().includes(q)))}
function renderHistory(){
 const q=$("#history-search").value.trim().toLowerCase();const tasks=state.tasks.filter(t=>t.status===historyStatus&&[t.title,t.owner_name,t.member,t.group_name,...(t.recipients||[])].some(v=>(v||"").toLowerCase().includes(q))).sort((a,b)=>String(b.deleted_at||b.completed_at||b.created).localeCompare(String(a.deleted_at||a.completed_at||a.created)));
 writeHTML("#history-list",JSON.stringify([tasks,q,[...pending]]),tasks.length?tasks.map(t=>card(t,true)).join(""):empty(historyStatus==='deleted'?"回收站是空的":"这里保存你做完的事",historyStatus==='deleted'?"删除的事项可以恢复，有提醒的事项恢复后先暂停。":"标记完成后，会自动归入历史，不再打扰你。"));
 document.querySelectorAll('[data-history]').forEach(b=>b.classList.toggle('selected',b.dataset.history===historyStatus));
}
function renderEvents(){
 const statuses={queued:"已排队",waiting:"等待空闲 / 时段",cancelled:"已取消",preview:"预览 · 未发送",notified:"已提交桌面通知",operated:"微信已执行发送",sending:"正在处理",retry:"等待自动重试",unknown:"状态待核对 · 已暂停"};
 writeHTML("#event-list",JSON.stringify(state.events),state.events.length?state.events.map(e=>`<article class="event-card"><div class="event-head"><span>待办 #${e.task_id} · ${formatTime(e.at)}</span><span class="event-status ${escapeHTML(e.status)}">${escapeHTML(statuses[e.status]||e.status)}</span></div><div class="event-content">${escapeHTML(e.content)}</div><div class="event-detail">${escapeHTML(e.detail)}</div></article>`).join(""):empty("还没有提醒记录","保存计划后，到点自动提醒，并在这里留下记录。"));
}
function setDue(date){
 $("#task-form").elements.first_due.value=bjParts(date).slice(0,16);updateScheduleSummary();
}
function updateScheduleSummary(){
 const f=$("#task-form");$("#schedule-summary").textContent=f.elements.first_due.value?"首次提醒："+formatTime(f.elements.first_due.value+":00+08:00")+" · 北京时间":"请选择提醒时间";
}
function updateTargetFields(){
 const f=$("#task-form"),type=f.elements.reminder_enabled.checked?f.elements.target_type.value:"local",local=type==="local",wx=type==="group"||type==="contact";
 $("#reminder-options").hidden=local;f.elements.target_type.disabled=local;
 $("#recipient-fields").hidden=!wx;$("#member-field").hidden=type!=="group";$("#schedule-fields").hidden=local;
 f.elements.group_name.disabled=!wx;f.elements.group_name.required=wx;f.elements.member.disabled=type!=="group";f.elements.member.required=type==="group";
 for(const k of ["first_due","interval_hours","interval_unit","repeat","auto_start"])f.elements[k].disabled=local;
 $("#target-label").textContent=type==="group"?"完整微信群名":"联系人（每行一位）";f.elements.group_name.rows=type==="group"?1:2;f.elements.group_name.maxLength=type==="group"?100:3030;f.elements.group_name.placeholder=type==="group"?"完整、唯一的群名":"每行一个完整、唯一的联系人备注名";
 $("#target-hint").textContent={local:"只保存到清单，不发送任何提醒。",self:"到点弹出 Windows 通知，不需要打开微信。",contact:"自动搜索联系人并私聊，无需置顶。",group:"每行填写一个成员名称。同群到期事项会汇总，发出真正的 @。"}[type];
 $("#save-hint").textContent=wx?"保存后按计划自动发微信。微信需登录，电脑桌面需解锁；可随时暂停。":"关闭页面后仍可通知你，请让软件在托盘保持运行。";
 $("#repeat-fields").hidden=!f.elements.repeat.checked;
}
function openTask(task=null,options={}){
 hideMenu();hidePreview();
 editing=task?.id??null;const f=$("#task-form");f.reset();$("#dialog-title").textContent=task?"编辑待办":"新建待办";
 if(task)for(const k of ["title","group_name","member","interval_hours"])f.elements[k].value=task[k];
 if(task?.recipients?.length)f.elements[task.target_type==="group"?"member":"group_name"].value=task.recipients.join("\n");
 f.elements.interval_hours.value=task?.interval_value??task?.interval_hours??3;f.elements.interval_unit.value=task?.interval_unit??"hours";
 f.elements.reminder_enabled.checked=Boolean(options.reminder||(task&&task.target_type!=="local"));if(options.title)f.elements.title.value=options.title;
 f.elements.target_type.value=task?.target_type==="local"?"self":task?.target_type??"self";f.elements.repeat.checked=task?Boolean(task.repeat):false;
 f.elements.owner_scope.value=task?.owner_scope??(scope==='delegated'?'delegated':'mine');f.elements.owner_name.value=task?.owner_name??'';ownerEdited=!!task||scope==='delegated';updateOwner();
 setDue(task&&task.target_type!=="local"?new Date(task.next_due):serverNow());updateTargetFields();updateInterval();$("#task-dialog").showModal();f.elements.title.focus();
}
$("#new-task").onclick=()=>openTask();for(const id of ["#close-dialog","#cancel-dialog"])$(id).onclick=()=>$("#task-dialog").close();
$("#task-dialog").addEventListener('click',e=>{if(e.target===$("#task-dialog")){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close()}});
$("#task-form").elements.target_type.onchange=()=>{const f=$('#task-form');if(!editing){const wx=['group','contact'].includes(f.elements.target_type.value);f.elements.repeat.checked=wx;if(!ownerEdited){f.elements.owner_scope.value=wx?'delegated':'mine';updateOwner()}}updateTargetFields()};
$("#task-form").elements.repeat.onchange=updateTargetFields;
$("#task-form").elements.reminder_enabled.onchange=updateTargetFields;
for(const b of document.querySelectorAll('[data-interval]'))b.onclick=()=>{$("#task-form").elements.interval_hours.value=b.dataset.interval;$("#task-form").elements.interval_unit.value=b.dataset.unit;updateInterval();document.querySelectorAll('[data-interval]').forEach(x=>x.classList.toggle('selected',x===b))};
function updateInterval(){const f=$("#task-form"),hours=f.elements.interval_unit.value==="hours";f.elements.interval_hours.min=hours?.1:1;f.elements.interval_hours.step=hours?.1:1;f.elements.interval_hours.max={hours:720,days:30,weeks:4}[f.elements.interval_unit.value];}
function updateOwner(){$('#owner-field').hidden=$('#task-form').elements.owner_scope.value!=='delegated'}
$('#task-form').elements.owner_scope.onchange=()=>{ownerEdited=true;updateOwner()};
$("#task-form").elements.interval_unit.onchange=updateInterval;
$("#task-form").elements.first_due.oninput=updateScheduleSummary;
$("#task-form").onsubmit=async e=>{
 e.preventDefault();const f=e.target,button=f.querySelector('[type=submit]');if(button.disabled)return;button.disabled=true;button.textContent="保存中…";
 const data=Object.fromEntries(new FormData(f));data.target_type=f.elements.reminder_enabled.checked?f.elements.target_type.value:"local";delete data.reminder_enabled;if(data.first_due)data.first_due+=":00+08:00";data.interval_value=Number(data.interval_hours||3);delete data.interval_hours;data.recipients=(data.target_type==="group"?f.elements.member.value:data.target_type==="contact"?f.elements.group_name.value:"").split(/\r?\n/).map(x=>x.trim()).filter(Boolean);data.repeat=f.elements.repeat.checked;data.auto_start=data.target_type!=="local"&&f.elements.auto_start.checked;if(editing)data.action="edit";
 try{if(await mutate(editing?`/api/tasks/${editing}`:"/api/tasks",data,data.target_type==="local"?"待办已保存":"计划已保存，到点自动提醒")){$("#task-dialog").close();filter="active";scope=data.owner_scope;$('#status-filter').value=filter;setView("tasks");renderTasks()}}
 finally{button.disabled=false;button.textContent="保存待办"}
};
$("#task-form").addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();e.target.form.requestSubmit()}});
async function taskAction(e){
 const b=e.target.closest('button[data-action]');if(!b||!state)return;const action=b.dataset.action;if(action==='new')return openTask();
 const task=state.tasks.find(t=>t.id===Number(b.dataset.id));if(!task||pending.has(task.id))return;if(action==='more')return taskMenu(task,b);hideMenu();if(action==='edit')return openTask(task);if(action==='add-reminder')return openTask(task,{reminder:true});if(action==='preview')return showPreview(b,task);
 pending.add(task.id);renderTasks();renderHistory();
 try{
  if(action==='check'){toast('正在检查微信会话…');const result=await request(`/api/tasks/${task.id}`,{action});toast(result.detail);return}
  if(action==='remind')toast(task.target_type==='self'?'正在提交桌面通知…':'正在处理提醒，请稍候…');
  const labels={complete:'已完成，移入历史待办',resume:'已重新开启，恢复提醒计划',pause:'已暂停这件事的提醒',remind:'已加入队列，自动发送',delete:'已移入回收站，未发提醒已取消',undelete:task.target_type==='local'?'待办已恢复':'已恢复；提醒先暂停，确认时间后再开启'};
  const undo=action==='complete'?()=>mutate(`/api/tasks/${task.id}`,{action:'resume'},'已撤销完成'):action==='pause'?()=>mutate(`/api/tasks/${task.id}`,{action:'resume'},'已恢复'):action==='delete'?()=>mutate(`/api/tasks/${task.id}`,{action:'undelete'},'已恢复；有提醒的事项先暂停'):null;
  if(await mutate(`/api/tasks/${task.id}`,{action},labels[action],undo)){
   const latest=state.events.find(x=>x.task_id===task.id);if(action==='remind'&&['unknown','retry'].includes(latest?.status))toast(latest.detail,true);
  }
 }catch(error){toast(error.message,true)}finally{pending.delete(task.id);renderTasks();renderHistory()}
}
$("#task-list").onclick=taskAction;$("#history-list").onclick=taskAction;
for(const b of document.querySelectorAll('[data-scope]'))b.onclick=()=>{scope=b.dataset.scope;selected.clear();hidePreview();hideMenu();renderTasks()};$('#status-filter').onchange=e=>{filter=e.target.value;selected.clear();renderTasks()};for(const b of document.querySelectorAll('[data-history]'))b.onclick=()=>{historyStatus=b.dataset.history;renderHistory()};$("#search").oninput=()=>state&&renderTasks();$("#history-search").oninput=()=>state&&renderHistory();
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>setView(b.dataset.view);
$("#toggle-engine").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const enabled=!state.settings.enabled;await mutate('/api/settings',{enabled},enabled?'自动提醒已开启':'全部提醒已暂停')}finally{b.disabled=false}};
$("#settings-form").oninput=()=>settingsDirty=true;$("#settings-form").onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('[type=submit]');b.disabled=true;try{if(await mutate('/api/settings',{mode:f.elements.mode.value,start:f.elements.start.value,end:f.elements.end.value,daily_limit:Number(f.elements.daily_limit.value),weekdays:f.elements.weekdays.checked,wait_idle:false,compact:f.elements.compact.checked,sound_enabled:f.elements.sound_enabled.checked,widget_enabled:f.elements.widget_enabled.checked,...(f.elements.startup_enabled.checked!==Boolean(state.startup_enabled)?{startup_enabled:f.elements.startup_enabled.checked}:{})},'设置已保存')){settingsDirty=false;render()}}finally{b.disabled=false}};
$('#settings-form').elements.widget_enabled.onchange=async e=>{const desired=e.target.checked;if(!await mutate('/api/settings',{widget_enabled:desired},desired?'桌面小窗已开启':'桌面小窗已关闭'))e.target.checked=!desired};
$('#reset-widget').onclick=()=>mutate('/api/widget/reset',{},'小窗已显示到屏幕中央');
$('#attention-banner').onclick=()=>setView('events');
$("#test-notification").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{await mutate('/api/notifications/test',{},'已发出测试通知，请查看桌面右下角')}finally{b.disabled=false}};
$("#diagnose").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const d=await request('/api/diagnostic');$("#diagnostic-result").textContent=`Windows：${d.windows?'是':'否'} · Python ${d.python}\n微信：${d.wechat_running?'已运行，仍需登录':'尚未运行'}\n${Object.entries(d.dependencies).map(([k,v])=>k+': '+(v?'就绪':'未安装')).join(' · ')}\n${d.detail}`;$("#diagnostic-result").hidden=false}catch(error){toast(error.message,true)}finally{b.disabled=false}};
$("#task-list").onchange=e=>{const id=Number(e.target.dataset.select);if(!id)return;e.target.checked?selected.add(id):selected.delete(id);renderTasks()};
$("#select-visible").onchange=e=>{for(const t of visibleTasks())e.target.checked?selected.add(t.id):selected.delete(t.id);renderTasks()};
async function remindMany(button,ids){button.disabled=true;try{const result=await request('/api/remind-all',ids?{task_ids:ids}:{});accept(result);toast(result.queued_added?"已排队 "+result.queued_added+" 条提醒；同群会汇总发送":"这些事项已在排队，或没有可提醒的事项")}catch(error){toast(error.message,true)}finally{button.disabled=false}}
$("#remind-selected").onclick=e=>remindMany(e.currentTarget,[...selected]);
$('#cancel-select').onclick=()=>{selectMode=false;selected.clear();renderTasks()};
$('#delete-selected').onclick=async()=>{const ids=[...selected];if(!ids.length)return;const ok=await mutate('/api/tasks/delete-many',{task_ids:ids},'已删除 '+ids.length+' 件，可在历史的回收站恢复',()=>mutate('/api/tasks/delete-many',{task_ids:ids,restore:true},'已恢复；有提醒的事项先暂停'));if(ok){selected.clear();renderTasks()}};
$("#backup-data").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const r=await fetch('/api/backup');if(!r.ok)throw new Error('备份失败');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='待办备份-'+bjParts(serverNow()).slice(0,10)+'.db';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('备份已导出')}catch(error){toast(error.message,true)}finally{b.disabled=false}};
$("#restore-data").onclick=()=>$("#restore-file").click();
$("#restore-file").onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>20*1024*1024)throw new Error('备份不能超过 20 MB');if(!confirm('恢复备份会替换当前清单。当前数据会先自动备份，是否继续？'))return;const bytes=new Uint8Array(await file.arrayBuffer());let text='';for(let i=0;i<bytes.length;i+=32768)text+=String.fromCharCode(...bytes.subarray(i,i+32768));await mutate('/api/data/restore',{content:btoa(text)},'已恢复待办备份');selected.clear();settingsDirty=false;render()}catch(error){toast(error.message,true)}finally{e.target.value=''}};
async function refresh(){if(refreshing||!state&&document.hidden)return;refreshing=true;try{accept(await request('/api/state'))}catch(error){$("#engine-title").textContent='本地服务连接中断';$("#engine-description").textContent='请重新打开软件，或双击 start.bat';$("#engine-dot").classList.remove('running')}finally{refreshing=false}}
window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',()=>!document.hidden&&refresh());refresh();setInterval(refresh,3000);

$("#quick-add").onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('[type=submit]'),title=f.elements.title.value.trim();if(!title)return;b.disabled=true;try{if(await mutate('/api/tasks',{title,target_type:'local',owner_scope:'mine',repeat:false},'待办已保存')){f.reset();filter='active';scope='mine';$('#status-filter').value=filter;setView('tasks');renderTasks();f.elements.title.focus()}}finally{b.disabled=false}};
$("#quick-reminder").onclick=()=>openTask(null,{title:$("#quick-add").elements.title.value.trim(),reminder:true});

let menuAnchor=null,previewTimer,previewAnchor=null;
function hideMenu(){const m=$('#action-menu');m.hidden=true;if(menuAnchor?.isConnected)menuAnchor.setAttribute('aria-expanded','false');menuAnchor=null}
function placePopup(el,anchor){const r=anchor.getBoundingClientRect();el.hidden=false;const width=el.offsetWidth,height=el.offsetHeight;el.style.left=Math.max(8,Math.min(r.right-width,innerWidth-width-8))+'px';el.style.top=(r.bottom+height+8<innerHeight?r.bottom+6:Math.max(8,r.top-height-6))+'px'}
function taskMenu(task,anchor){hidePreview();const entries=[['edit','查看 / 编辑']];if(task.status==='active'){entries.push(['add-reminder',task.target_type==='local'?'添加提醒':'修改提醒']);if(task.target_type!=='local')entries.push(['pause','暂停提醒'])}entries.push(['delete','删除待办']);showMenu(entries,anchor,task.id)}
function showMenu(entries,anchor,id=null){hideMenu();menuAnchor=anchor;anchor.setAttribute('aria-expanded','true');const m=$('#action-menu');m.innerHTML=entries.map(([action,label])=>`<button role="menuitem" data-action="${action}" ${id===null?'':`data-id="${id}"`} class="${action==='delete'?'danger':''}">${label}</button>`).join('');placePopup(m,anchor);m.querySelector('button')?.focus()}
$('#list-more').onclick=e=>showMenu([['select','批量选择'],['remind-all','提醒全部进行中事项']],e.currentTarget);
$('#action-menu').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.action==='select'){hideMenu();selectMode=true;renderTasks()}else if(b.dataset.action==='remind-all'){hideMenu();remindMany($('#list-more'))}else taskAction(e)};
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#action-menu')&&!e.target.closest('[data-action=more],#list-more'))hideMenu()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const anchor=menuAnchor;hideMenu();hidePreview();anchor?.focus()}if(!$('#action-menu').hidden&&['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const items=[...$('#action-menu').querySelectorAll('button')],i=items.indexOf(document.activeElement);items[(i+(e.key==='ArrowDown'?1:items.length-1))%items.length].focus()}});
function hidePreview(){clearTimeout(previewTimer);previewTimer=null;$('#task-preview').hidden=true}
function showPreview(anchor,task){previewAnchor=anchor;const p=$('#task-preview');$('#preview-title').textContent=task.title;$('#preview-meta').textContent=(task.owner_name||(task.owner_scope==='mine'?'我的待办':(task.recipients||[]).join('、')||'交办事项'))+(task.target_type==='local'?' · 仅记录':' · '+(task.status==='paused'?'提醒已暂停':'下次提醒 '+formatTime(task.next_due)));placePopup(p,anchor)}
function schedulePreview(b){if(!state)return;previewAnchor=b;const t=state.tasks.find(t=>t.id===Number(b.dataset.id));previewTimer=setTimeout(()=>{previewTimer=null;if(b.isConnected&&b.matches(':hover')&&t)showPreview(b,t)},400)}
for(const list of ['#task-list','#history-list']){const el=$(list);el.addEventListener('pointerover',e=>{const b=e.target.closest('.task-title');if(!b||b.contains(e.relatedTarget)||!state)return;hidePreview();schedulePreview(b)});el.addEventListener('pointermove',e=>{const b=e.target.closest('.task-title');if(b&&!previewTimer&&$('#task-preview').hidden)schedulePreview(b)});el.addEventListener('pointerout',e=>{if(e.target.closest('.task-title')){hidePreview();previewAnchor=null}});el.addEventListener('focusin',e=>{const b=e.target.closest('.task-title');if(b&&state){const t=state.tasks.find(t=>t.id===Number(b.dataset.id));if(t)showPreview(b,t)}});el.addEventListener('focusout',()=>{hidePreview();previewAnchor=null})}
window.addEventListener('scroll',()=>{const b=previewAnchor;hidePreview();hideMenu();if(b?.isConnected&&b.matches(':hover'))schedulePreview(b)},true);
