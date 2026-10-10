"use strict";
let state=null,filter="active",view="tasks",editing=null,settingsDirty=false,refreshing=false,serverReceived=0;
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
 view=next;document.querySelectorAll('.nav[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 for(const v of ["tasks","history","events","settings"])$(`#${v}-view`).hidden=v!==view;
 const titles={tasks:["待办清单","让每件事，都有着落。","提醒自己，也提醒别人。记下来，剩下的交给时间。"],history:["历史待办","做完的事，也值得记住。","完成的事项在这里留档，想继续时随时重新开启。"],events:["提醒记录","每一次提醒，都有记录。","看看哪些已经提醒，哪些正在等待重试。"],settings:["提醒设置","按你的节奏，适时提醒。","选择提醒时段，检查桌面通知与微信连接。"]};
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
 $("#test-notification").disabled=!state.desktop_notifications;$("#version-label").textContent=`v${state.version||"1.0.0"} · 纯脚本 · Asia/Shanghai`;
 renderTasks();renderHistory();renderEvents();
 if(!settingsDirty){const f=$("#settings-form");f.elements.mode.value=cfg.mode;for(const k of ["start","end","daily_limit"])f.elements[k].value=cfg[k];f.elements.weekdays.checked=cfg.weekdays}
}
function card(t,history=false){
 const local=t.target_type==="local",self=t.target_type==="self",busy=pending.has(t.id);
 const target=local?'<span class="chip">仅记录</span>':self?'<span class="chip">提醒自己</span>':t.target_type==="contact"?`<span class="chip">私聊</span><span>♧ ${escapeHTML(t.group_name)}</span>`:`<span class="chip">@ ${escapeHTML(t.member)}</span><span>▤ ${escapeHTML(t.group_name)}</span>`;
 const cadence=local?"":t.repeat?`<span>◷ 每 ${escapeHTML(t.interval_hours)} 小时</span>`:'<span>◷ 提醒一次</span>';
 const timing=history?(t.completed_at?"完成于 "+formatTime(t.completed_at):"已完成，停止提醒"):t.status==="paused"?"已暂停":local?"仅记录，不提醒":t.reminder_finished?"已提醒，等待完成":"下次提醒 "+formatTime(t.next_due)+(state.settings.enabled?"":" · 全部提醒已暂停");
 const action=(name,label,cls="")=>`<button class="${cls}" data-action="${name}" data-id="${t.id}" ${busy?"disabled":""}>${label}</button>`;
 let actions=history?action("resume","重新开启"):t.status==="active"?action("complete","✓ 完成","done-button")+(local?"":action("remind",busy?"处理中…":state.settings.mode==="preview"?"预览一次":"额外提醒"))+action("pause","暂停"):action("resume","恢复");
 if(!history){actions+=action("edit","编辑");if(!local&&!self)actions+=action("check","检查会话")}
 return `<article class="task-card ${escapeHTML(t.status)}" data-task="${t.id}"><div class="task-marker">${history?"✓":t.status==="paused"?"Ⅱ":self?"◷":"↗"}</div><div class="task-body"><h3>${escapeHTML(t.title)}</h3><div class="task-meta">${target}${cadence}</div><div class="task-time">${escapeHTML(timing)}</div></div><div class="task-actions">${actions}</div></article>`;
}
function empty(title,description,create=false){return `<div class="empty"><div class="empty-icon">✓</div><h3>${title}</h3><p>${description}</p>${create?'<button class="secondary" data-action="new">＋ 添加第一件待办</button>':""}</div>`}
function renderTasks(){
 const q=$("#search").value.trim().toLowerCase();const tasks=state.tasks.filter(t=>t.status!=="done"&&(filter==="all"||t.status===filter)&&[t.title,t.member,t.group_name].some(v=>(v||"").toLowerCase().includes(q)));
 const key=JSON.stringify([tasks,filter,q,state.settings.enabled,state.settings.mode,[...pending]]);
 writeHTML("#task-list",key,tasks.length?tasks.map(t=>card(t)).join(""):empty(q?"没找到这件事":filter==="paused"?"没有暂停的待办":"清单很轻，开始记下一件事吧",q?"换一个关键词试试。":"自己的事和需要提醒别人的事，都可以放在这里。",!q&&filter==="active"));
 document.querySelectorAll('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter===filter));
}
function renderHistory(){
 const q=$("#history-search").value.trim().toLowerCase();const tasks=state.tasks.filter(t=>t.status==="done"&&[t.title,t.member,t.group_name].some(v=>(v||"").toLowerCase().includes(q))).sort((a,b)=>String(b.completed_at||b.created).localeCompare(String(a.completed_at||a.created)));
 writeHTML("#history-list",JSON.stringify([tasks,q,[...pending]]),tasks.length?tasks.map(t=>card(t,true)).join(""):empty("这里保存你做完的事","标记完成后，会自动归入历史，不再打扰你。"));
}
function renderEvents(){
 const statuses={preview:"预览 · 未发送",notified:"已提交桌面通知",operated:"微信已执行发送",sending:"正在处理",retry:"等待自动重试",unknown:"状态待核对 · 已暂停"};
 writeHTML("#event-list",JSON.stringify(state.events),state.events.length?state.events.map(e=>`<article class="event-card"><div class="event-head"><span>待办 #${e.task_id} · ${formatTime(e.at)}</span><span class="event-status ${escapeHTML(e.status)}">${escapeHTML(statuses[e.status]||e.status)}</span></div><div class="event-content">${escapeHTML(e.content)}</div><div class="event-detail">${escapeHTML(e.detail)}</div></article>`).join(""):empty("还没有提醒记录","保存计划后，到点自动提醒，并在这里留下记录。"));
}
function setDue(date){
 $("#task-form").elements.first_due.value=bjParts(date).slice(0,16);updateScheduleSummary();
}
function updateScheduleSummary(){
 const f=$("#task-form");$("#schedule-summary").textContent=f.elements.first_due.value?"首次提醒："+formatTime(f.elements.first_due.value+":00+08:00")+" · 北京时间":"请选择提醒时间";
}
function updateTargetFields(){
 const f=$("#task-form"),type=f.elements.target_type.value,local=type==="local",wx=type==="group"||type==="contact";
 $("#recipient-fields").hidden=!wx;$("#member-field").hidden=type!=="group";$("#schedule-fields").hidden=local;
 f.elements.group_name.disabled=!wx;f.elements.group_name.required=wx;f.elements.member.disabled=type!=="group";f.elements.member.required=type==="group";
 for(const k of ["first_due","interval_hours","repeat","auto_start"])f.elements[k].disabled=local;
 $("#target-label").textContent=type==="group"?"完整微信群名":"联系人名称或备注名";f.elements.group_name.placeholder=type==="group"?"完整、唯一的群名":"微信里完整、唯一的备注名";
 $("#target-hint").textContent={local:"只保存到清单，不发送任何提醒。",self:"到点弹出 Windows 通知，不需要打开微信。",contact:"自动搜索联系人并私聊，无需置顶。",group:"在指定群中选中负责人，发出真正的 @ 提醒。"}[type];
 $("#save-hint").textContent=wx?"保存后按计划自动发微信。微信需登录，电脑桌面需解锁；可随时暂停。":"关闭页面后仍可通知你，请让软件在托盘保持运行。";
 $("#repeat-fields").hidden=!f.elements.repeat.checked;
}
function openTask(task=null){
 editing=task?.id??null;const f=$("#task-form");f.reset();$("#dialog-title").textContent=task?"编辑待办":"新建待办";
 if(task)for(const k of ["title","group_name","member","interval_hours"])f.elements[k].value=task[k];
 f.elements.target_type.value=task?.target_type??"self";f.elements.repeat.checked=task?Boolean(task.repeat):false;
 setDue(task&&task.target_type!=="local"?new Date(task.next_due):serverNow());updateTargetFields();$("#task-dialog").showModal();f.elements.title.focus();
}
$("#new-task").onclick=()=>openTask();for(const id of ["#close-dialog","#cancel-dialog"])$(id).onclick=()=>$("#task-dialog").close();
$("#task-dialog").addEventListener('click',e=>{if(e.target===$("#task-dialog")){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close()}});
$("#task-form").elements.target_type.onchange=()=>{if(!editing)$("#task-form").elements.repeat.checked=['group','contact'].includes($("#task-form").elements.target_type.value);updateTargetFields()};
$("#task-form").elements.repeat.onchange=updateTargetFields;
for(const b of document.querySelectorAll('[data-interval]'))b.onclick=()=>{$("#task-form").elements.interval_hours.value=b.dataset.interval;document.querySelectorAll('[data-interval]').forEach(x=>x.classList.toggle('selected',x===b))};
$("#task-form").elements.first_due.oninput=updateScheduleSummary;
$("#task-form").onsubmit=async e=>{
 e.preventDefault();const f=e.target,button=f.querySelector('[type=submit]');if(button.disabled)return;button.disabled=true;button.textContent="保存中…";
 const data=Object.fromEntries(new FormData(f));if(data.first_due)data.first_due+=":00+08:00";data.interval_hours=Number(data.interval_hours||3);data.repeat=f.elements.repeat.checked;data.auto_start=data.target_type!=="local"&&f.elements.auto_start.checked;if(editing)data.action="edit";
 try{if(await mutate(editing?`/api/tasks/${editing}`:"/api/tasks",data,data.target_type==="local"?"待办已保存":"计划已保存，到点自动提醒")){$("#task-dialog").close();filter="active";setView("tasks");renderTasks()}}
 finally{button.disabled=false;button.textContent="保存待办"}
};
$("#task-form").addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();e.target.form.requestSubmit()}});
async function taskAction(e){
 const b=e.target.closest('button[data-action]');if(!b||!state)return;const action=b.dataset.action;if(action==='new')return openTask();
 const task=state.tasks.find(t=>t.id===Number(b.dataset.id));if(!task||pending.has(task.id))return;if(action==='edit')return openTask(task);
 pending.add(task.id);renderTasks();renderHistory();
 try{
  if(action==='check'){toast('正在检查微信会话…');const result=await request(`/api/tasks/${task.id}`,{action});toast(result.detail);return}
  if(action==='remind')toast(task.target_type==='self'?'正在提交桌面通知…':'正在处理提醒，请稍候…');
  const labels={complete:'已完成，移入历史待办',resume:'已重新开启，恢复提醒计划',pause:'已暂停这件事的提醒',remind:'提醒已处理，详情见提醒记录'};
  const undo=action==='complete'?()=>mutate(`/api/tasks/${task.id}`,{action:'resume'},'已撤销完成'):action==='pause'?()=>mutate(`/api/tasks/${task.id}`,{action:'resume'},'已恢复'):null;
  if(await mutate(`/api/tasks/${task.id}`,{action},labels[action],undo)){
   const latest=state.events.find(x=>x.task_id===task.id);if(action==='remind'&&['unknown','retry'].includes(latest?.status))toast(latest.detail,true);
  }
 }catch(error){toast(error.message,true)}finally{pending.delete(task.id);renderTasks();renderHistory()}
}
$("#task-list").onclick=taskAction;$("#history-list").onclick=taskAction;
for(const b of document.querySelectorAll('[data-filter]'))b.onclick=()=>{filter=b.dataset.filter;renderTasks()};$("#search").oninput=()=>state&&renderTasks();$("#history-search").oninput=()=>state&&renderHistory();
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>setView(b.dataset.view);
$("#toggle-engine").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const enabled=!state.settings.enabled;await mutate('/api/settings',{enabled},enabled?'自动提醒已开启':'全部提醒已暂停')}finally{b.disabled=false}};
$("#settings-form").oninput=()=>settingsDirty=true;$("#settings-form").onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('[type=submit]');b.disabled=true;try{if(await mutate('/api/settings',{mode:f.elements.mode.value,start:f.elements.start.value,end:f.elements.end.value,daily_limit:Number(f.elements.daily_limit.value),weekdays:f.elements.weekdays.checked},'设置已保存')){settingsDirty=false;render()}}finally{b.disabled=false}};
$("#test-notification").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{await mutate('/api/notifications/test',{},'已发出测试通知，请查看桌面右下角')}finally{b.disabled=false}};
$("#diagnose").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const d=await request('/api/diagnostic');$("#diagnostic-result").textContent=`Windows：${d.windows?'是':'否'} · Python ${d.python}\n微信：${d.wechat_running?'已运行，仍需登录':'尚未运行'}\n${Object.entries(d.dependencies).map(([k,v])=>k+': '+(v?'就绪':'未安装')).join(' · ')}\n${d.detail}`;$("#diagnostic-result").hidden=false}catch(error){toast(error.message,true)}finally{b.disabled=false}};
async function refresh(){if(refreshing||!state&&document.hidden)return;refreshing=true;try{accept(await request('/api/state'))}catch(error){$("#engine-title").textContent='本地服务连接中断';$("#engine-description").textContent='请重新打开软件，或双击 start.bat';$("#engine-dot").classList.remove('running')}finally{refreshing=false}}
window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',()=>!document.hidden&&refresh());refresh();setInterval(refresh,3000);
