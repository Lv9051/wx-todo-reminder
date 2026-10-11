"use strict";
let state=null,filter="all",scope="mine",historyStatus="done",selectMode=false,ownerEdited=false,view="tasks",editing=null,editingRevision=null,settingsDirty=false,refreshing=false,serverReceived=0;
const selected=new Set();
let historyData={key:null,tasks:[],months:[],total:0,has_more:false},historySequence=0,historyLoading=false,savedDraft=null;
const reducedMotion=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
const findTask=id=>state?.tasks.find(t=>t.id===Number(id))||historyData.tasks.find(t=>t.id===Number(id));
const pending=new Set(), cache={}, $=s=>document.querySelector(s);
const escapeHTML=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const formatTime=v=>v?new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(v)):"未记录";
const serverNow=()=>state?new Date(new Date(state.server_time).getTime()+performance.now()-serverReceived):new Date();
const bjParts=date=>new Date(date.getTime()+8*3600000).toISOString();
const motionEase='cubic-bezier(.16,1,.3,1)';
const animate=(el,frames,options={})=>{if(!el||reducedMotion())return null;el.getAnimations().filter(a=>a.id==='feedback').forEach(a=>a.cancel());const a=el.animate(frames,{duration:190,easing:motionEase,...options});a.id='feedback';return a};
let lastUndo=null;
function toast(text,error=false,undo=null){
 const el=$("#toast");$("#toast-text").textContent=text;el.classList.toggle("error",error);el.hidden=false;
 lastUndo=undo;$("#toast-undo").hidden=!undo;$("#toast-undo").onclick=undo?()=>{lastUndo=null;el.hidden=true;undo()}:null;
 animate(el,[{opacity:0,translate:'0 5px'},{opacity:1,translate:'0 0'}]);
 clearTimeout(toast.timer);toast.timer=setTimeout(()=>{el.hidden=true;lastUndo=null},error?8000:undo?10000:3500);
}
async function request(path,data){
 const response=await fetch(path,data?{method:"POST",headers:{"Content-Type":"application/json","X-Reminder-Token":state?.token??""},body:JSON.stringify(data)}:{});
 const value=await response.json();if(!response.ok)throw new Error(value.error||"操作失败");return value;
}
function accept(value){if(state?.token===value.token&&state.snapshot_sequence>value.snapshot_sequence)return;state=value;serverReceived=performance.now();render()}
async function mutate(path,data,success,undo){
 const animations=[];
 try{
  const value=await request(path,data);
  const current=state?.token!==value.token||!(state.snapshot_sequence>value.snapshot_sequence);
  if(current&&!reducedMotion()&&(data.action==='complete'||data.action==='delete'||path==='/api/tasks/delete-many'&&!data.restore)){
   const ids=data.task_ids||[Number(path.split('/').pop())];
   for(const id of ids){const row=[...document.querySelectorAll(`.task-card[data-task="${id}"]`)].find(el=>el.getClientRects().length);if(row)animations.push(row.animate([{opacity:1,height:row.offsetHeight+'px',marginBottom:'0px'},{opacity:0,height:'0px',minHeight:0,paddingTop:'0px',paddingBottom:'0px',marginBottom:'-6px',overflow:'hidden'}],{duration:170,easing:'cubic-bezier(.2,.7,.2,1)',fill:'forwards'}))}
   await Promise.all(animations.map(a=>a.finished.catch(()=>{})));
  }
  accept(value);if(success)toast(success,false,undo);return true;
 }catch(error){toast(error.message,true);return false}
 finally{animations.forEach(a=>a.cancel())}
}
function nodeKey(node){return node.nodeType===1?(node.getAttribute('data-task')||node.getAttribute('data-group')||node.getAttribute('data-date')||''):''}
function patchChildren(root,template){
 const originals=[...root.childNodes],keyed=new Map(originals.filter(n=>nodeKey(n)).map(n=>[nodeKey(n),n])),used=new Set();let cursor=root.firstChild;
 for(const desired of [...template.childNodes]){
  const key=nodeKey(desired);let current=key?keyed.get(key):cursor;
  if(!current||used.has(current)||current.nodeType!==desired.nodeType||current.nodeName!==desired.nodeName||nodeKey(current)!==key)current=desired.cloneNode(true);
  if(current!==cursor)root.insertBefore(current,cursor);used.add(current);
  if(current.isEqualNode(desired)){cursor=current.nextSibling;continue}
  if(current.nodeType===3){if(current.nodeValue!==desired.nodeValue)current.nodeValue=desired.nodeValue}
  else if(current.nodeType===1){
   const keep=name=>current.tagName==='DETAILS'&&name==='open'||current===menuAnchor&&name==='aria-expanded';
   for(const attr of [...current.attributes])if(!desired.hasAttribute(attr.name)&&!keep(attr.name))current.removeAttribute(attr.name);
   for(const attr of desired.attributes)if(!keep(attr.name)&&current.getAttribute(attr.name)!==attr.value)current.setAttribute(attr.name,attr.value);
   if(current.tagName==='INPUT'&&current.type==='checkbox')current.checked=desired.checked;
   patchChildren(current,desired);
  }
  cursor=current.nextSibling;
 }
 for(const child of originals)if(!used.has(child))child.remove();
}
function writeHTML(selector,key,html){
 if(cache[selector]===key)return;cache[selector]=key;const root=$(selector),positions=new Map(),focused=document.activeElement,oldRows=[...root.querySelectorAll('[data-task]')];
 const context=JSON.stringify([view,scope,filter,selectMode,$('#search').value]);const sameContext=root.dataset.context===context;root.dataset.context=context;
 if(selector==='#task-list'&&sameContext&&!reducedMotion())oldRows.forEach(el=>positions.set(el.dataset.task,el.getBoundingClientRect().top));
 const template=document.createElement('template');template.innerHTML=html;patchChildren(root,template.content);
 for(const el of root.querySelectorAll('[data-task]')){const top=positions.get(el.dataset.task);if(top!==undefined){const delta=top-el.getBoundingClientRect().top;if(Math.abs(delta)>1)animate(el,[{translate:`0 ${delta}px`},{translate:'0 0'}],{duration:220})}else if(selector==='#task-list'&&sameContext&&oldRows.length)animate(el,[{opacity:0,translate:'0 5px'},{opacity:1,translate:'0 0'}])}
 if(focused&&root.contains(focused)&&document.activeElement!==focused)focused.focus({preventScroll:true});
 if(menuAnchor&&!menuAnchor.isConnected)hideMenu();if(previewAnchor&&!previewAnchor.isConnected)hidePreview();
}
function selectPill(buttons,selectedButton){
 for(const b of buttons){b.classList.toggle('selected',b===selectedButton);b.setAttribute('aria-pressed',String(b===selectedButton))}
 placePill(selectedButton?.parentElement);
}
function placePill(container){const b=container?.querySelector('.selected');if(!b?.getClientRects().length)return;container.style.setProperty('--pill-x',b.offsetLeft+'px');container.style.setProperty('--pill-width',b.offsetWidth+'px');container.classList.add('pill-ready')}
function setView(next){
 hideMenu();hidePreview();
 const changed=view!==next;
 view=next;document.querySelectorAll('.nav[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
 for(const v of ["tasks","history","events","settings"])$(`#${v}-view`).hidden=v!==view;
 const titles={tasks:["待办清单","记下来，一件件完成。","先记待办。需要时，再添加提醒。"],history:["历史待办","做完的事，也值得记住。","完成的事项在这里留档，想继续时随时重新开启。"],events:["提醒记录","每一次提醒，都有记录。","看看哪些已经提醒，哪些正在等待重试。"],settings:["提醒设置","按你的节奏，适时提醒。","选择提醒时段，检查桌面通知与微信连接。"]};
 $("#breadcrumb").textContent=titles[view][0];$("#page-title").textContent=titles[view][1];$("#page-description").textContent=titles[view][2];$("#new-task").hidden=view!=="tasks";
 document.querySelectorAll('.nav[data-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.view===view?'page':'false'));
 if(changed)animate($(`#${view}-view`),[{opacity:.4},{opacity:1}],{duration:170});
 if(view==='history'&&state)renderHistory();
 document.querySelectorAll('.tabs').forEach(placePill);
}
function render(){
 if(!state)return;const cfg=state.settings;
 $("#nav-count").textContent=state.tasks.length;$("#history-count").textContent=state.history_counts?.done??0;
 $("#engine-dot").classList.toggle("running",cfg.enabled);$("#engine-title").textContent=cfg.enabled?(cfg.mode==="preview"?"预览调度已开启":"自动提醒已开启"):"自动提醒已暂停";
 $("#engine-description").textContent=cfg.mode==="preview"?"到点只生成预览记录": "提醒自己弹桌面通知；提醒别人自动发微信";
 $("#toggle-engine").textContent=cfg.enabled?"暂停提醒":"开启提醒";$("#clock").textContent=formatTime(state.server_time)+" · 北京时间";
 $("#notification-state").textContent=state.desktop_notifications?"桌面通知已就绪；关闭页面后仍能提醒。":"请使用桌面软件或 start.bat 启动，让程序驻留托盘。";
 $("#test-notification").disabled=!state.desktop_notifications;$("#version-label").textContent=`v${state.version||"1.4.0"} · 本地保存`;
 $("#data-location").textContent=state.data_path?"数据位置："+state.data_path:"";
 $("#queue-count").textContent=state.queue_count?state.queue_count+" 条提醒排队中":"";
 for(const id of selected)if(!state.tasks.some(t=>t.id===id&&!['done','deleted'].includes(t.status)))selected.delete(id);
 const issues=state.attention_tasks||state.events.filter(e=>e.status==='unknown'&&state.tasks.some(t=>t.id===e.task_id&&!['done','deleted'].includes(t.status)));
 $("#attention-banner").hidden=!issues.length;$("#attention-banner").textContent=issues.length?'有 '+issues.length+' 件事项的发送结果待核对，提醒已暂停 · 查看这些待办 →':'';
 $("#widget-state").textContent=({visible:'小窗已显示',hidden:'小窗已关闭',failed:'小窗启动失败，请重启软件或查看运行日志',unavailable:'请在桌面软件中开启小窗'})[state.widget_state]||'';
 renderTasks();renderHistory();renderEvents();
 if(!settingsDirty){const f=$("#settings-form");f.elements.mode.value=cfg.mode;for(const k of ["start","end","daily_limit"])f.elements[k].value=cfg[k];f.elements.weekdays.checked=cfg.weekdays;for(const k of ["sound_enabled","widget_enabled"])f.elements[k].checked=k==="widget_enabled"?Boolean(cfg[k]):cfg[k]!==false;f.elements.startup_enabled.checked=Boolean(state.startup_enabled)}
 if($('#task-dialog').open)updateConflict();
}
function card(t,history=false){
 const local=t.target_type==="local",self=t.target_type==="self",busy=pending.has(t.id),review=state.attention_tasks?.some(item=>item.id===t.id);
 const target=local?'':self?'<span class="chip">提醒自己</span>':t.target_type==="contact"?`<span class="chip">私聊</span><span>♧ ${escapeHTML((t.recipients||[t.group_name]).join("、"))}</span>`:`<span class="chip">@ ${escapeHTML((t.recipients||[t.member]).join("、"))}</span><span>▤ ${escapeHTML(t.group_name)}</span>`;
 const cadence=local?"":t.repeat?`<span>◷ 每 ${escapeHTML(t.interval_value??t.interval_hours)} ${{hours:"小时",days:"天",weeks:"周"}[t.interval_unit||"hours"]}</span>`:'<span>◷ 提醒一次</span>';
 const deleted=t.status==='deleted';
 const days=t.purge_after?Math.max(0,Math.ceil((new Date(t.purge_after)-serverNow())/86400000)):30;
 const timing=deleted?'删除于 '+formatTime(t.deleted_at)+' · '+(days?'保留 '+days+' 天':'即将清理'):history?(t.completed_at?"完成于 "+formatTime(t.completed_at):"已完成，停止提醒"):t.status==="paused"?(review?'发送结果待核对 · 提醒已暂停':local?'':'提醒已暂停'):local?'':t.queue_status?({queued:"等待发送",waiting:"等待允许时段",retry:"等待重试",sending:"发送中"}[t.queue_status]+" · "+t.queue_detail):t.reminder_finished?"已提醒，等待完成":"下次提醒 "+formatTime(t.next_due)+(state.settings.enabled?"":" · 全部提醒已暂停");
 const action=(name,label,cls="")=>`<button class="${cls}" data-action="${name}" data-id="${t.id}" ${name==='more'?`aria-label="${escapeHTML(t.title)}的更多操作" aria-haspopup="menu"`:''} ${busy?"disabled":""}>${label}</button>`;
 let actions=deleted?action('undelete','恢复'):history?action("resume","重新开启"):action("complete","✓ 完成","done-button")+(t.status==='active'?(local?action("add-reminder","添加提醒"):action("remind",busy?"处理中…":state.settings.mode==="preview"?"预览一次":"额外提醒")):action('resume','恢复提醒'));
 if(!deleted)actions+=action('more','⋯','more-button');
 const checkbox=selectMode&&!history?`<input class="task-select" type="checkbox" data-select="${t.id}" aria-label="选择 ${escapeHTML(t.title)}" ${selected.has(t.id)?"checked":""}>`:"";
 const ownership=t.owner_scope==='delegated'&&t.owner_name?`<span class="chip">${escapeHTML(t.owner_name)}</span>`:'';
 return `<article class="task-card ${escapeHTML(t.status)} ${review?'needs-review':''}" data-task="${t.id}">${checkbox}<div class="task-marker">${deleted?'↶':history?"✓":t.status==="paused"?"Ⅱ":self?"◷":"↗"}</div><div class="task-body"><h3><button class="task-title" data-action="${deleted?'preview':'edit'}" data-id="${t.id}">${escapeHTML(t.title)}</button></h3><div class="task-meta">${ownership}${target}${cadence}</div><div class="task-time">${escapeHTML(timing)}</div></div><div class="task-actions">${actions}</div></article>`;
}
function empty(title,description,create=false){return `<div class="empty"><div class="empty-icon">✓</div><h3>${title}</h3><p>${description}</p>${create?'<button class="secondary" data-action="new">＋ 添加第一件待办</button>':""}</div>`}
function renderTasks(){
 const q=$("#search").value.trim().toLowerCase(),tasks=visibleTasks();
 const key=JSON.stringify([tasks,scope,filter,q,selectMode,state.settings.enabled,state.settings.mode,state.attention_tasks,[...pending],[...selected]]);
 let html=tasks.map(t=>card(t)).join('');
 if(scope==='delegated'&&tasks.length){const groups=new Map();for(const t of tasks){const name=t.owner_name||(t.recipients||[]).join('、')||'未指定负责人';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(t)}html=[...groups].map(([name,items])=>`<details class="person-group" data-group="${escapeHTML(name)}" open><summary>${escapeHTML(name)}<span>${items.length} 项</span></summary><div class="task-list">${items.map(t=>card(t)).join('')}</div></details>`).join('')}
 writeHTML("#task-list",key,tasks.length?html:empty(q?"没找到这件事":filter==="paused"?"没有暂停的待办":"这里还没有待办",q?"换一个关键词试试。":scope==='mine'?"记下自己的事，需要提醒时再设置。":"交办事项单独放在这里，设置后按计划提醒。",!q&&scope==='mine'));
 $("#bulk-bar").hidden=!selectMode;$("#selected-count").textContent='已选 '+selected.size+' 项';$("#remind-selected").disabled=!tasks.some(t=>selected.has(t.id)&&t.status==='active'&&t.target_type!=='local');$("#delete-selected").disabled=!selected.size;
 $("#select-visible").checked=!!tasks.length&&tasks.every(t=>selected.has(t.id));
 const scopes=document.querySelectorAll('[data-scope]');selectPill(scopes,[...scopes].find(b=>b.dataset.scope===scope));
}
function visibleTasks(){const q=$("#search").value.trim().toLowerCase();return state.tasks.filter(t=>!['done','deleted'].includes(t.status)&&(scope==='all'||t.owner_scope===scope)&&(filter==='all'||filter==='reminders'&&t.target_type!=='local'||t.status===filter)&&[t.title,t.owner_name,t.member,t.group_name,...(t.recipients||[])].some(v=>(v||'').toLowerCase().includes(q)))}
function historyKey(){return JSON.stringify([historyStatus,$('#history-month').value,$('#history-search').value.trim(),state.history_revision])}
async function loadHistory(more=false){
 const key=historyKey(),seq=++historySequence,button=$('#history-more');historyLoading=true;button.disabled=true;
 if(!more){historyData={key,tasks:[],months:[],total:0,has_more:false};writeHTML('#history-list','loading'+key,'<div class="history-loading" aria-label="正在读取历史"><i></i><i></i><i></i></div>');button.hidden=true}
 const query=new URLSearchParams({status:historyStatus,month:$('#history-month').value,search:$('#history-search').value.trim(),offset:String(more?historyData.tasks.length:0)});
 try{const data=await request('/api/history?'+query);if(seq!==historySequence||key!==historyKey())return;historyData={...data,key,tasks:more?[...historyData.tasks,...data.tasks]:data.tasks};const month=$('#history-month'),selectedMonth=month.value;month.innerHTML='<option value="">全部月份</option>'+data.months.map(m=>`<option value="${escapeHTML(m)}">${escapeHTML(m.replace('-',' 年 '))} 月</option>`).join('');if(selectedMonth&&!data.months.includes(selectedMonth))month.insertAdjacentHTML('beforeend',`<option value="${escapeHTML(selectedMonth)}">${escapeHTML(selectedMonth.replace('-',' 年 '))} 月</option>`);month.value=selectedMonth;paintHistory()}
 catch(error){if(seq===historySequence){historyData.key=null;writeHTML('#history-list','error'+seq,empty('暂时没能读取历史','重新打开历史页即可重试。'));toast(error.message,true)}}finally{if(seq===historySequence){historyLoading=false;button.disabled=false}}
}
function renderHistory(){
 if(view!=='history')return;
 if(historyData.key!==historyKey())loadHistory();else if(!historyLoading)paintHistory();
 const historyTabs=document.querySelectorAll('[data-history]');selectPill(historyTabs,[...historyTabs].find(b=>b.dataset.history===historyStatus));
 $('#history-note').textContent=historyStatus==='deleted'?'删除后保留 30 天，到期自动清理；清理前会备份。':'完成记录长期保留，按完成日期整理。';
}
function paintHistory(){
 const today=bjParts(serverNow()).slice(0,10),yesterday=bjParts(new Date(serverNow().getTime()-86400000)).slice(0,10),groups=new Map();
 for(const t of historyData.tasks){const day=(t.history_stamp||'').slice(0,10)||'未记录日期';if(!groups.has(day))groups.set(day,[]);groups.get(day).push(t)}
 const html=[...groups].map(([day,tasks])=>`<section class="history-group" data-date="${escapeHTML(day)}"><h3>${escapeHTML(day===today?'今天':day===yesterday?'昨天':day.replace(/-/g,'.'))}</h3><div class="task-list">${tasks.map(t=>card(t,true)).join('')}</div></section>`).join('');
 writeHTML('#history-list',JSON.stringify([historyData.tasks,[...pending],historyStatus]),html||empty(historyStatus==='deleted'?'回收站是空的':'没有完成记录',historyStatus==='deleted'?'删除的事项可在 30 天内恢复。':'试试其他月份或关键词；完成的事项会自动归入这里。'));
 $('#history-more').hidden=!historyData.has_more;$('#history-more').textContent=`查看更多 · 已显示 ${historyData.tasks.length} / ${historyData.total}`;
}
function renderEvents(){
 const statuses={queued:"已排队",waiting:"等待可发送条件",cancelled:"已取消",preview:"预览 · 未发送",notified:"已提交桌面通知",operated:"微信已执行发送",sending:"正在处理",retry:"等待自动重试",unknown:"状态待核对 · 已暂停"};
 writeHTML("#event-list",JSON.stringify(state.events),state.events.length?state.events.map(e=>`<article class="event-card"><div class="event-head"><span>待办 #${e.task_id} · ${formatTime(e.at)}</span><span class="event-status ${escapeHTML(e.status)}">${escapeHTML(statuses[e.status]||e.status)}</span></div><div class="event-content">${escapeHTML(e.content)}</div><div class="event-detail">${escapeHTML(e.detail)}</div></article>`).join(""):empty("还没有提醒记录","保存计划后，到点自动提醒，并在这里留下记录。"));
}
function setDue(date){
 $("#task-form").elements.first_due.value=bjParts(date).slice(0,16);updateScheduleSummary();
}
function updateScheduleSummary(){
 const f=$("#task-form");$("#schedule-summary").textContent=f.elements.first_due.value?"首次提醒："+formatTime(f.elements.first_due.value+":00+08:00")+" · 北京时间":"请选择提醒时间";
 updateConflict();
}
function updateTargetFields(){
 const f=$("#task-form"),type=f.elements.reminder_enabled.checked?f.elements.target_type.value:"local",local=type==="local",wx=type==="group"||type==="contact";
 reveal($('#reminder-options'),!local,$('#task-dialog').open);f.elements.target_type.disabled=local;
 $("#recipient-fields").hidden=!wx;$("#member-field").hidden=type!=="group";$("#schedule-fields").hidden=local;
 f.elements.group_name.disabled=!wx;f.elements.group_name.required=wx;f.elements.member.disabled=type!=="group";f.elements.member.required=type==="group";
 for(const k of ["first_due","interval_hours","interval_unit","repeat","auto_start"])f.elements[k].disabled=local;
 $("#target-label").textContent=type==="group"?"完整微信群名":"联系人（每行一位）";f.elements.group_name.rows=type==="group"?1:2;f.elements.group_name.maxLength=type==="group"?100:3030;f.elements.group_name.placeholder=type==="group"?"完整、唯一的群名":"每行一个完整、唯一的联系人备注名";
 $("#target-hint").textContent={local:"只保存到清单，不发送任何提醒。",self:"到点弹出 Windows 通知，不需要打开微信。",contact:"自动搜索联系人并私聊，无需置顶。",group:"每行填写一个成员名称。同群到期事项会汇总，发出真正的 @。"}[type];
 const current=editing?findTask(editing):null;
 $("#save-hint").textContent=current?.status==='done'?'事项仍保留在历史，不重新开启提醒。':current?.status==='paused'?'提醒保持暂停；保存后可从清单恢复。':current?.reminder_finished?'这件事已提醒过，修改文字不会再次提醒；修改时间或周期会更新计划。':wx?"微信需登录，电脑桌面需解锁；保存后按计划执行，可随时暂停。":"关闭主窗口后仍可通知你，请让软件在托盘保持运行。";
 $("#repeat-fields").hidden=!f.elements.repeat.checked;
 updateConflict();
}
function updateConflict(){
 const f=$('#task-form'),el=$('#schedule-conflict');if(!state||!f.elements.reminder_enabled.checked||!['group','contact'].includes(f.elements.target_type.value)||!f.elements.first_due.value){el.hidden=true;return}
 const kind=f.elements.target_type.value,minute=f.elements.first_due.value,names=(kind==='group'?f.elements.member.value:f.elements.group_name.value).split(/\r?\n/).map(n=>n.trim()).filter(Boolean),group=f.elements.group_name.value.trim();
 const matches=state.tasks.filter(t=>t.id!==editing&&t.status==='active'&&!t.reminder_finished&&t.target_type===kind&&(kind!=='group'||t.group_name===group)&&bjParts(new Date(t.next_due)).slice(0,16)===minute&&(t.recipients||[]).some(n=>names.includes(n)));
 el.hidden=!matches.length;if(!matches.length)return;
 const content='<p>这个时间已安排提醒，可继续单独保存，到点同会话会汇总发送。</p>'+matches.slice(0,3).map(t=>`<button type="button" class="conflict-item" data-existing="${t.id}"><span>${escapeHTML((t.recipients||[]).filter(n=>names.includes(n)).join('、'))} · ${escapeHTML(t.title)}</span><small>查看已有待办 ↗</small></button>`).join('');
 if(el.innerHTML!==content)el.innerHTML=content;
}
let draftInitial='',firstDueInitial='',closingTask=false;
const draftKey=()=>`qiangxin.drafts.v1:${state?.data_path||location.origin}`;
function draftMap(){try{const data=JSON.parse(localStorage.getItem(draftKey())||'{}');return data&&typeof data==='object'&&!Array.isArray(data)?data:{}}catch{return {}}}
function captureDraft(){const f=$('#task-form');return {editing,revision:editingRevision,dueOriginal:firstDueInitial,ownerEdited,fields:[...f.elements].filter(el=>el.name).map(el=>({name:el.name,value:el.value,checked:el.checked}))}}
function storeDraft(remove=false){
 if(!state)return;try{const drafts=draftMap(),key=String(editing??'new'),draft=captureDraft();
  if(remove||JSON.stringify(draft)===draftInitial)delete drafts[key];else if(draft.fields.some(f=>['title','group_name','member'].includes(f.name)&&f.value.trim()))drafts[key]={...draft,at:Date.now()};
  const keys=Object.keys(drafts).sort((a,b)=>(drafts[b].at||0)-(drafts[a].at||0));for(const key of keys.slice(10))delete drafts[key];localStorage.setItem(draftKey(),JSON.stringify(drafts));
 }catch{toast('草稿暂时无法保存在本机，请先保存待办',true)}
}
function applyDraft(draft){
 if(!draft||!Array.isArray(draft.fields))return false;editing=draft.editing;editingRevision=draft.revision;firstDueInitial=draft.dueOriginal||'';ownerEdited=!!draft.ownerEdited;const f=$('#task-form');
 for(const field of draft.fields){const el=f.elements[field.name];if(!el||typeof field.value!=='string'||field.value.length>10000)continue;el.value=field.value;if(['checkbox','radio'].includes(el.type))el.checked=!!field.checked}
 $('#dialog-title').textContent=editing?'编辑待办':'新建待办';updateOwner();updateTargetFields();updateInterval();updateScheduleSummary();return true;
}
function restoreDraft(){if(!savedDraft)return;const draft=savedDraft;savedDraft=null;openTask(null,{keepDraft:true,skipStored:true});applyDraft(draft);$('#draft-return').hidden=true;$('#draft-status').hidden=true;$('#task-form').elements.title.focus()}
$('#schedule-conflict').onclick=e=>{const b=e.target.closest('[data-existing]');if(!b)return;const task=findTask(b.dataset.existing);if(!task)return;if(!savedDraft){storeDraft();savedDraft=captureDraft()}openTask(task,{keepDraft:true});$('#draft-return').hidden=false};
$('#back-to-draft').onclick=restoreDraft;
function openTask(task=null,options={}){
 hideMenu();hidePreview();
 if(!options.keepDraft)savedDraft=null;$('#draft-return').hidden=!savedDraft;
 editing=task?.id??null;editingRevision=task?.revision??null;const f=$("#task-form");f.reset();$("#dialog-title").textContent=task?"编辑待办":"新建待办";$('#task-error').hidden=true;$('#draft-status').hidden=true;
 if(task)for(const k of ["title","group_name","member","interval_hours"])f.elements[k].value=task[k];
 if(task?.recipients?.length)f.elements[task.target_type==="group"?"member":"group_name"].value=task.recipients.join("\n");
 f.elements.interval_hours.value=task?.interval_value??task?.interval_hours??3;f.elements.interval_unit.value=task?.interval_unit??"hours";
 f.elements.reminder_enabled.checked=Boolean(options.reminder||(task&&task.target_type!=="local"));if(options.title)f.elements.title.value=options.title;
 f.elements.target_type.value=task?.target_type==="local"?"self":task?.target_type??"self";f.elements.repeat.checked=task?Boolean(task.repeat):false;
 f.elements.owner_scope.value=task?.owner_scope??(scope==='delegated'?'delegated':'mine');f.elements.owner_name.value=task?.owner_name??'';ownerEdited=!!task||scope==='delegated';updateOwner();
 f.elements.auto_start.checked=state?.settings.mode==='wechat'&&Boolean(state.settings.enabled);$('#auto-start-field').hidden=!!task&&task.target_type!=='local';
 setDue(task&&task.target_type!=="local"?new Date(task.next_due):serverNow());firstDueInitial=f.elements.first_due.value;updateTargetFields();updateInterval();draftInitial=JSON.stringify(captureDraft());
 const stored=draftMap()[String(editing??'new')];if(!options.skipStored&&!options.title&&stored&&applyDraft(stored)){$('#draft-status').hidden=false;if(task&&task.revision!==stored.revision){editingRevision=task.revision;$('#task-error').textContent='事项已有新变化，草稿仍保留；请核对内容和提醒时间后保存。';$('#task-error').hidden=false}}
 if(!$('#task-dialog').open)$("#task-dialog").showModal();f.elements.title.focus();
}
async function dismissTask(saved=false){if(closingTask||!$('#task-dialog').open)return;closingTask=true;if(!saved)storeDraft();const dialog=$('#task-dialog'),a=animate(dialog,[{opacity:1,scale:'1',translate:'0 0'},{opacity:0,scale:'.987',translate:'0 4px'}],{duration:130});try{if(a)await a.finished}catch{}dialog.close();closingTask=false}
$("#new-task").onclick=()=>openTask();for(const id of ["#close-dialog","#cancel-dialog"])$(id).onclick=()=>dismissTask();
$('#discard-draft').onclick=()=>{storeDraft(true);const t=editing?findTask(editing):null;openTask(t,{keepDraft:true,skipStored:true});$('#draft-status').hidden=true};
$('#task-dialog').addEventListener('cancel',e=>{e.preventDefault();dismissTask()});
$("#task-dialog").addEventListener('click',e=>{if(e.target===$("#task-dialog")){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dismissTask()}});
$("#task-form").elements.target_type.onchange=()=>{const f=$('#task-form');if(!editing){const wx=['group','contact'].includes(f.elements.target_type.value);f.elements.repeat.checked=wx;if(!ownerEdited){f.elements.owner_scope.value=wx?'delegated':'mine';updateOwner()}}updateTargetFields()};
$("#task-form").elements.repeat.onchange=updateTargetFields;
$("#task-form").elements.reminder_enabled.onchange=updateTargetFields;
for(const b of document.querySelectorAll('[data-interval]'))b.onclick=()=>{$("#task-form").elements.interval_hours.value=b.dataset.interval;$("#task-form").elements.interval_unit.value=b.dataset.unit;updateInterval();document.querySelectorAll('[data-interval]').forEach(x=>x.classList.toggle('selected',x===b))};
function updateInterval(){const f=$("#task-form"),hours=f.elements.interval_unit.value==="hours";f.elements.interval_hours.min=hours?.1:1;f.elements.interval_hours.step=hours?.1:1;f.elements.interval_hours.max={hours:720,days:30,weeks:4}[f.elements.interval_unit.value];}
function updateOwner(){$('#owner-field').hidden=$('#task-form').elements.owner_scope.value!=='delegated'}
$('#task-form').elements.owner_scope.onchange=()=>{ownerEdited=true;updateOwner()};
$("#task-form").elements.interval_unit.onchange=updateInterval;
$("#task-form").elements.first_due.oninput=updateScheduleSummary;
 for(const name of ['group_name','member'])$('#task-form').elements[name].oninput=updateConflict;
 $('#task-dialog').addEventListener('close',()=>{savedDraft=null;$('#draft-return').hidden=true;$('#task-error').hidden=true});
 $('#task-form').addEventListener('input',()=>{storeDraft();$('#task-error').hidden=true});$('#task-form').addEventListener('change',()=>storeDraft());
$("#task-form").onsubmit=async e=>{
 e.preventDefault();const f=e.target,button=f.querySelector('[type=submit]');if(button.disabled)return;button.disabled=true;button.textContent="保存中…";
 const data=Object.fromEntries(new FormData(f));data.target_type=f.elements.reminder_enabled.checked?f.elements.target_type.value:"local";delete data.reminder_enabled;if(data.first_due)data.first_due+=":00+08:00";data.interval_value=Number(data.interval_hours||3);delete data.interval_hours;data.recipients=(data.target_type==="group"?f.elements.member.value:data.target_type==="contact"?f.elements.group_name.value:"").split(/\r?\n/).map(x=>x.trim()).filter(Boolean);data.repeat=f.elements.repeat.checked;data.auto_start=data.target_type!=="local"&&f.elements.auto_start.checked&&(!editing||findTask(editing)?.target_type==='local');if(editing){data.action="edit";data.expected_revision=editingRevision}
 if(editing&&findTask(editing)?.target_type!=='local'&&f.elements.first_due.value===firstDueInitial)delete data.first_due;
 try{const value=await request(editing?`/api/tasks/${editing}`:"/api/tasks",data);accept(value);storeDraft(true);if(savedDraft){restoreDraft();toast('已有事项已保存，回到保留的草稿')}else{await dismissTask(true);filter="all";scope=data.owner_scope;$('#status-filter').value=filter;setView("tasks");renderTasks();toast('待办已保存')}}catch(error){storeDraft();$('#task-error').textContent=error.message;$('#task-error').hidden=false;$('#task-error').scrollIntoView({block:'nearest'});toast(error.message,true)}
 finally{button.disabled=false;button.textContent="保存待办"}
};
$("#task-form").addEventListener('keydown',e=>{if(e.ctrlKey&&e.key==='Enter'&&!e.isComposing){e.preventDefault();$('#task-form').requestSubmit()}});
async function taskAction(e){
 const b=e.target.closest('button[data-action]');if(!b||!state)return;const action=b.dataset.action;if(action==='new')return openTask();
 const task=findTask(b.dataset.id);if(!task||pending.has(task.id))return;if(action==='more')return taskMenu(task,b);hideMenu();if(action==='edit')return openTask(task);if(action==='add-reminder')return openTask(task,{reminder:true});if(action==='preview')return showPreview(b,task);
 pending.add(task.id);renderTasks();renderHistory();
 try{
  if(action==='check'){toast('正在检查微信会话…');const result=await request(`/api/tasks/${task.id}`,{action});toast(result.detail);return}
  if(action==='remind')toast(task.target_type==='self'?'正在提交桌面通知…':'正在处理提醒，请稍候…');
  const labels={complete:'已完成，移入历史待办',resume:'已重新开启，恢复提醒计划',pause:'已暂停这件事的提醒',remind:'已加入队列，自动发送',delete:'已移入回收站，未发提醒已取消',undelete:task.target_type==='local'?'待办已恢复':'已恢复；提醒先暂停，确认时间后再开启'};
  const undo=['complete','pause'].includes(action)?()=>mutate(`/api/tasks/${task.id}`,{action:'undo'},'已撤销，保留原来的状态和提醒时间'):action==='delete'?()=>mutate(`/api/tasks/${task.id}`,{action:'undelete'},'已恢复；有提醒的事项先暂停'):null;
  if(await mutate(`/api/tasks/${task.id}`,{action},labels[action],undo)){
   const latest=state.events.find(x=>x.task_id===task.id);if(action==='remind'&&['unknown','retry'].includes(latest?.status))toast(latest.detail,true);
  }
 }catch(error){toast(error.message,true)}finally{pending.delete(task.id);renderTasks();renderHistory()}
}
$("#task-list").onclick=taskAction;$("#history-list").onclick=taskAction;
for(const b of document.querySelectorAll('[data-scope]'))b.onclick=()=>{scope=b.dataset.scope;selected.clear();hidePreview();hideMenu();renderTasks()};$('#status-filter').onchange=e=>{filter=e.target.value;selected.clear();renderTasks()};for(const b of document.querySelectorAll('[data-history]'))b.onclick=()=>{historyStatus=b.dataset.history;$('#history-month').value='';renderHistory()};$("#search").oninput=()=>state&&renderTasks();let historySearchTimer;$("#history-search").oninput=()=>{clearTimeout(historySearchTimer);historySearchTimer=setTimeout(()=>state&&renderHistory(),180)};$('#history-month').onchange=renderHistory;$('#history-more').onclick=()=>!historyLoading&&loadHistory(true);
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>setView(b.dataset.view);
$("#toggle-engine").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const enabled=!state.settings.enabled;await mutate('/api/settings',{enabled},enabled?'自动提醒已开启':'全部提醒已暂停')}finally{b.disabled=false}};
function markSettingsDirty(){const f=$('#settings-form');settingsDirty=['mode','start','end','daily_limit'].some(k=>String(f.elements[k].value)!==String(state.settings[k]))||['weekdays','sound_enabled','widget_enabled'].some(k=>f.elements[k].checked!==Boolean(state.settings[k]))||f.elements.startup_enabled.checked!==Boolean(state.startup_enabled);f.classList.toggle('dirty',settingsDirty);f.querySelector('.settings-footer span').textContent=settingsDirty?'有修改尚未保存':'设置会保留，重启后继续执行。'}
$("#settings-form").oninput=markSettingsDirty;$("#settings-form").onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('[type=submit]');b.disabled=true;try{if(await mutate('/api/settings',{mode:f.elements.mode.value,start:f.elements.start.value,end:f.elements.end.value,daily_limit:Number(f.elements.daily_limit.value),weekdays:f.elements.weekdays.checked,wait_idle:false,compact:true,sound_enabled:f.elements.sound_enabled.checked,widget_enabled:f.elements.widget_enabled.checked,...(f.elements.startup_enabled.checked!==Boolean(state.startup_enabled)?{startup_enabled:f.elements.startup_enabled.checked}:{})},'设置已保存')){settingsDirty=false;render();markSettingsDirty()}}finally{b.disabled=false}};
$('#settings-form').elements.widget_enabled.onchange=async e=>{const desired=e.target.checked;if(!await mutate('/api/settings',{widget_enabled:desired},desired?'桌面小窗已开启':'桌面小窗已关闭'))e.target.checked=!desired;markSettingsDirty()};
$('#reset-widget').onclick=()=>mutate('/api/widget/reset',{},'小窗已显示到屏幕中央');
$('#attention-banner').onclick=()=>{scope='all';filter='paused';$('#status-filter').value=filter;$('#search').value='';setView('tasks');renderTasks()};
$("#test-notification").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{await mutate('/api/notifications/test',{},'已发出测试通知，请查看桌面右下角')}finally{b.disabled=false}};
$("#diagnose").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const d=await request('/api/diagnostic');$("#diagnostic-result").textContent=`Windows：${d.windows?'是':'否'} · Python ${d.python}\n微信：${d.wechat_running?'已运行，仍需登录':'尚未运行'}\n${Object.entries(d.dependencies).map(([k,v])=>k+': '+(v?'就绪':'未安装')).join(' · ')}\n${d.detail}`;$("#diagnostic-result").hidden=false}catch(error){toast(error.message,true)}finally{b.disabled=false}};
$("#task-list").onchange=e=>{const id=Number(e.target.dataset.select);if(!id)return;e.target.checked?selected.add(id):selected.delete(id);renderTasks()};
$("#select-visible").onchange=e=>{for(const t of visibleTasks())e.target.checked?selected.add(t.id):selected.delete(t.id);renderTasks()};
let confirmPending=null;
function confirmAction({title,description,details=[],accept='确认',danger=false}){
 if(confirmPending)return Promise.resolve(false);hideMenu();hidePreview();const d=$('#confirm-dialog');$('#confirm-title').textContent=title;$('#confirm-description').textContent=description;$('#confirm-details').replaceChildren(...details.map(text=>{const row=document.createElement('div');row.textContent=text;return row}));$('#confirm-accept').textContent=accept;$('#confirm-accept').classList.toggle('danger-fill',danger);d.returnValue='';d.showModal();return new Promise(resolve=>confirmPending=resolve);
}
$('#confirm-dialog').addEventListener('close',()=>{const resolve=confirmPending;confirmPending=null;resolve?.($('#confirm-dialog').returnValue==='accept')});
$('#confirm-dialog').addEventListener('click',e=>{const d=e.currentTarget;if(e.target!==d)return;const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close('cancel')});
async function remindMany(button,ids){
 const tasks=state.tasks.filter(t=>t.status==='active'&&t.target_type!=='local'&&(!ids||ids.includes(t.id)));if(!tasks.length){toast('没有可提醒的事项');return}
 const routes=new Set();for(const t of tasks){if(t.target_type==='group')routes.add('群聊 · '+t.group_name);if(t.target_type==='contact')for(const name of t.recipients||[t.group_name])routes.add('私聊 · '+name);if(t.target_type==='self')routes.add('提醒自己 · 桌面通知')}
 const preview=state.settings.mode==='preview';if(!await confirmAction({title:preview?'批量预览提醒？':'现在提醒这些事项？',description:`${tasks.length} 件进行中的待办，${routes.size} 个提醒对象。`+(preview?'只生成预览，不发送消息。':'同会话会汇总发送，不改变后续提醒计划。'),details:[...routes],accept:preview?'生成预览':'开始提醒'}))return;
 button.disabled=true;try{const result=await request('/api/remind-all',{task_ids:tasks.map(t=>t.id)});accept(result);toast(result.queued_added?"已排队 "+result.queued_added+" 条提醒；同群会汇总发送":"这些事项已在排队，或没有可提醒的事项")}catch(error){toast(error.message,true)}finally{button.disabled=false}
}
$("#remind-selected").onclick=e=>remindMany(e.currentTarget,[...selected]);
$('#cancel-select').onclick=()=>{selectMode=false;selected.clear();renderTasks()};
$('#delete-selected').onclick=async()=>{const ids=[...selected];if(!ids.length||!await confirmAction({title:`删除这 ${ids.length} 件待办？`,description:'取消它们尚未发出的提醒，移入回收站。30 天内可恢复。',details:ids.slice(0,5).map(id=>findTask(id)?.title||''),accept:'移入回收站',danger:true}))return;const ok=await mutate('/api/tasks/delete-many',{task_ids:ids},'已删除 '+ids.length+' 件，可在历史的回收站恢复',()=>mutate('/api/tasks/delete-many',{task_ids:ids,restore:true},'已恢复；有提醒的事项先暂停'));if(ok){selected.clear();renderTasks()}};
$("#backup-data").onclick=async e=>{const b=e.currentTarget;b.disabled=true;try{const r=await fetch('/api/backup');if(!r.ok)throw new Error('备份失败');const url=URL.createObjectURL(await r.blob()),a=document.createElement('a');a.href=url;a.download='待办备份-'+bjParts(serverNow()).slice(0,10)+'.db';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);toast('备份已导出')}catch(error){toast(error.message,true)}finally{b.disabled=false}};
$("#restore-data").onclick=()=>$("#restore-file").click();
$("#restore-file").onchange=async e=>{const file=e.target.files[0];if(!file)return;try{if(file.size>20*1024*1024)throw new Error('备份不能超过 20 MB');if(!await confirmAction({title:'恢复这份备份？',description:'会替换当前清单；程序会先备份现有数据。',details:[file.name],accept:'恢复备份'}))return;const bytes=new Uint8Array(await file.arrayBuffer());let text='';for(let i=0;i<bytes.length;i+=32768)text+=String.fromCharCode(...bytes.subarray(i,i+32768));await mutate('/api/data/restore',{content:btoa(text)},'已恢复待办备份');selected.clear();settingsDirty=false;render()}catch(error){toast(error.message,true)}finally{e.target.value=''}};
async function refresh(){if(refreshing||!state&&document.hidden)return;refreshing=true;try{accept(await request('/api/state'))}catch(error){$("#engine-title").textContent='本地服务连接中断';$("#engine-description").textContent='请重新打开软件，或双击 start.bat';$("#engine-dot").classList.remove('running')}finally{refreshing=false}}
window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',()=>!document.hidden&&refresh());refresh();setInterval(refresh,3000);

let composingQuick=false;$('#quick-add input').addEventListener('compositionstart',()=>composingQuick=true);$('#quick-add input').addEventListener('compositionend',()=>composingQuick=false);
$('#quick-add input').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.isComposing||composingQuick||e.keyCode===229))e.preventDefault()});
$("#quick-add").onsubmit=async e=>{e.preventDefault();const f=e.target,b=f.querySelector('[type=submit]'),title=f.elements.title.value.trim();if(!title||b.disabled||composingQuick)return;b.disabled=true;try{if(await mutate('/api/tasks',{title,target_type:'local',owner_scope:'mine',repeat:false},'待办已保存')){f.reset();filter='all';scope='mine';$('#status-filter').value=filter;setView('tasks');renderTasks();f.elements.title.focus()}}finally{b.disabled=false}};
$("#quick-reminder").onclick=()=>openTask(null,{title:$("#quick-add").elements.title.value.trim(),reminder:true});

const reveals=new WeakMap();
function reveal(el,show,animated=true){
 const previous=reveals.get(el);if(previous?.show===show)return;previous?.animation?.cancel();reveals.set(el,{show});
 if(!animated||reducedMotion()){el.hidden=!show;return}const height=show?(el.hidden=false,el.scrollHeight):el.offsetHeight;
 const a=animate(el,show?[{height:'0px',opacity:0,overflow:'hidden'},{height:height+'px',opacity:1,overflow:'hidden'}]:[{height:height+'px',opacity:1,overflow:'hidden'},{height:'0px',opacity:0,overflow:'hidden'}],{duration:210});reveals.set(el,{show,animation:a});a?.finished.then(()=>{if(reveals.get(el)?.animation===a)el.hidden=!show}).catch(()=>{});
}
let menuAnchor=null,menuSequence=0,previewTimer,previewAnchor=null;
function hideMenu(){const m=$('#action-menu'),seq=++menuSequence;if(menuAnchor?.isConnected)menuAnchor.setAttribute('aria-expanded','false');menuAnchor=null;if(m.hidden)return;m.inert=true;const a=animate(m,[{opacity:1,translate:'0 0'},{opacity:0,translate:'0 -2px'}],{duration:90});if(!a)m.hidden=true;else a.finished.then(()=>{if(seq===menuSequence)m.hidden=true}).catch(()=>{})}
function placePopup(el,anchor){const r=anchor.getBoundingClientRect();el.hidden=false;const width=el.offsetWidth,height=el.offsetHeight;el.style.left=Math.max(8,Math.min(r.right-width,innerWidth-width-8))+'px';el.style.top=(r.bottom+height+8<innerHeight?r.bottom+6:Math.max(8,r.top-height-6))+'px'}
function taskMenu(task,anchor){hidePreview();const entries=[['edit','查看 / 编辑']];if(task.status==='active'){entries.push(['add-reminder',task.target_type==='local'?'添加提醒':'修改提醒']);if(task.target_type!=='local')entries.push(['pause','暂停提醒'])}entries.push(['delete','删除待办']);showMenu(entries,anchor,task.id)}
function showMenu(entries,anchor,id=null){hideMenu();++menuSequence;menuAnchor=anchor;anchor.setAttribute('aria-expanded','true');const m=$('#action-menu');m.inert=false;m.getAnimations().forEach(a=>a.cancel());m.innerHTML=entries.map(([action,label])=>`<button role="menuitem" data-action="${action}" ${id===null?'':`data-id="${id}"`} class="${action==='delete'?'danger':''}">${label}</button>`).join('');placePopup(m,anchor);animate(m,[{opacity:0,translate:'0 -3px',scale:'.985'},{opacity:1,translate:'0 0',scale:'1'}],{duration:150});m.querySelector('button')?.focus({preventScroll:true})}
$('#list-more').onclick=e=>showMenu([['select','批量选择'],['remind-all','提醒全部进行中事项']],e.currentTarget);
$('#action-menu').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.action==='select'){hideMenu();selectMode=true;renderTasks()}else if(b.dataset.action==='remind-all'){hideMenu();remindMany($('#list-more'))}else taskAction(e)};
document.addEventListener('pointerdown',e=>{if(!e.target.closest('#action-menu')&&!e.target.closest('[data-action=more],#list-more'))hideMenu()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const anchor=menuAnchor;hideMenu();hidePreview();anchor?.focus()}if(!$('#action-menu').hidden&&['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();const items=[...$('#action-menu').querySelectorAll('button')],i=items.indexOf(document.activeElement);items[(i+(e.key==='ArrowDown'?1:items.length-1))%items.length].focus()}});
document.addEventListener('keydown',e=>{
 if(e.isComposing||!e.ctrlKey||e.altKey||e.metaKey||$('#task-dialog').open||$('#confirm-dialog').open)return;const key=e.key.toLowerCase(),typing=e.target.matches('input,textarea,[contenteditable=true]');
 if(key==='n'){e.preventDefault();setView('tasks');$('#quick-add input').focus()}
 else if(key==='f'){e.preventDefault();const search=view==='history'?$('#history-search'):$('#search');if(view!=='history')setView('tasks');search.focus();search.select()}
 else if(key==='z'&&!typing&&lastUndo){e.preventDefault();$('#toast-undo').click()}
});
for(const [selector,title] of [['#new-task','新建待办'],['#quick-add input','快速记事'],['#search','搜索待办'],['#history-search','搜索历史待办']])$(selector).title=title+(selector.includes('search')?' · Ctrl + F':' · Ctrl + N');
const tabResize=new ResizeObserver(entries=>entries.forEach(({target})=>placePill(target)));document.querySelectorAll('.tabs').forEach(el=>tabResize.observe(el));
function hidePreview(){clearTimeout(previewTimer);previewTimer=null;$('#task-preview').hidden=true}
function showPreview(anchor,task){previewAnchor=anchor;const p=$('#task-preview');$('#preview-title').textContent=task.title;$('#preview-meta').textContent=(task.owner_name||(task.owner_scope==='mine'?'我的待办':(task.recipients||[]).join('、')||'交办事项'))+(task.target_type==='local'?' · 仅记录':' · '+(task.status==='paused'?'提醒已暂停':'下次提醒 '+formatTime(task.next_due)));placePopup(p,anchor)}
function schedulePreview(b){if(!state)return;previewAnchor=b;const t=findTask(b.dataset.id);previewTimer=setTimeout(()=>{previewTimer=null;if(b.isConnected&&b.matches(':hover')&&t)showPreview(b,t)},400)}
for(const list of ['#task-list','#history-list']){const el=$(list);el.addEventListener('pointerover',e=>{const b=e.target.closest('.task-title');if(!b||b.contains(e.relatedTarget)||!state)return;hidePreview();schedulePreview(b)});el.addEventListener('pointermove',e=>{const b=e.target.closest('.task-title');if(b&&!previewTimer&&$('#task-preview').hidden)schedulePreview(b)});el.addEventListener('pointerout',e=>{if(e.target.closest('.task-title')){hidePreview();previewAnchor=null}});el.addEventListener('focusin',e=>{const b=e.target.closest('.task-title');if(b&&state){const t=findTask(b.dataset.id);if(t)showPreview(b,t)}});el.addEventListener('focusout',()=>{hidePreview();previewAnchor=null})}
window.addEventListener('scroll',()=>{const b=previewAnchor;hidePreview();if(menuAnchor?.isConnected&&!$('#action-menu').inert)placePopup($('#action-menu'),menuAnchor);if(b?.isConnected&&b.matches(':hover'))schedulePreview(b)},true);
window.addEventListener('wheel',e=>{if(!e.target.closest('#action-menu'))hideMenu()},{passive:true});
