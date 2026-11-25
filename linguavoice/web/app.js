const $ = id => document.getElementById(id);
const supplied = new URLSearchParams(location.hash.slice(1)).get('token');
if(supplied){ sessionStorage.setItem('linguavoice-token',supplied); history.replaceState(null,'',location.pathname); }
const token = sessionStorage.getItem('linguavoice-token') || '';
let state, initialized=false, seen=new Set(), pending=[], armed=false, playingId=null, objectURL=null, commandRevision=0, busy=false, pollBusy=false, playState='idle';
const audio=$('audio');
const manualJobs=new Set();
let installId=sessionStorage.getItem('linguavoice-install')||'', installing=!!installId, installPolling=false, installedVoice='', installedReady=false;
const notice=(text,error=false)=>{ $('notice').textContent=text; $('notice').className=error?'error':''; };
async function api(path,method='GET',body){
  const response=await fetch(path,{method,headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});
  const result=await response.json(); if(!response.ok){const error=Error(result.error||'请求失败');error.status=response.status;throw error;} return result;
}
async function heartbeat(status){
  playState=status;
  $('playbackState').textContent=({armed:'已启用',playing:'播放中',paused:'已暂停',blocked:'需要手动点击播放',ended:'播放完成',idle:'待播放',error:'播放失败'})[status]||status;
  await api('/api/playback','POST',{state:status,job_id:playingId});
}
async function play(job){
  busy=true;
  try{
    const response=await fetch(job.audio_path,{headers:{Authorization:'Bearer '+token}});
    if(!response.ok) throw Error('音频尚未准备好');
    if(objectURL) URL.revokeObjectURL(objectURL);
    objectURL=URL.createObjectURL(await response.blob()); audio.src=objectURL;
    audio.playbackRate=Number($('playbackRate').value); playingId=job.id;
    $('nowPlaying').textContent=job.voice_name+' · '+job.speed.toFixed(2)+'×';
    try {await audio.play(); await heartbeat('playing');notice('正在播放'+job.voice_name+'。');}
    catch {await heartbeat('blocked'); notice('浏览器阻止了自动播放，请点击音频播放器上的播放按钮。');}
  }catch(error){notice(error.message,true);busy=false;}
}
function next(){if(!busy&&armed&&pending.length){const job=pending.shift();play(job);}}
audio.addEventListener('ended',()=>{busy=false;heartbeat('ended').catch(()=>{});notice('播放完成。可以继续试听，或回到 ChatGPT 聊天。');next();});
audio.addEventListener('pause',()=>{if(!audio.ended)heartbeat('paused').catch(()=>{});});
audio.addEventListener('play',()=>{busy=true;heartbeat('playing').catch(()=>{});});
audio.addEventListener('error',()=>{busy=false;heartbeat('error').catch(()=>{});});
function renderJobs(jobs){
  const container=$('jobs');container.replaceChildren();
  if(!jobs.length){const p=document.createElement('p');p.className='empty';p.textContent='准备好后，AI 选择的语句会出现在这里。';container.append(p);}
  for(const job of [...jobs].reverse()){
    const row=document.createElement('div');row.className='job';
    const meta=document.createElement('div');meta.className='meta';
    const status={queued:'排队中',generating:'正在合成',ready:'可播放',error:'失败',cancelled:'已取消'}[job.status];
    meta.textContent=job.voice_name+' · '+job.speed.toFixed(2)+'× · '+status;
    const text=document.createElement('p');text.textContent=job.hide_text?'听力原文已折叠':job.text;row.append(meta,text);
    if(job.error){const p=document.createElement('p');p.textContent=job.error;row.append(p);}
    if(job.status==='ready'){
      const button=document.createElement('button');button.textContent='播放';button.onclick=()=>{armed=true;pending=pending.filter(x=>x.id!==job.id);play(job);};row.append(button);
      if(job.hide_text){const reveal=document.createElement('button');reveal.textContent='显示原文';reveal.onclick=async()=>{try{text.textContent=(await api('/api/reveal','POST',{job_id:job.id})).text;}catch(e){notice(e.message,true);}};row.append(reveal);}
    }container.append(row);
  }
}
function renderVoices(){
  const selection=$('voice').value||state.settings.default_voice;
  $('voice').replaceChildren();
  for(const v of state.voices){const o=document.createElement('option');o.value=v.id;o.textContent=v.name;$('voice').append(o);}
  $('voice').value=state.voices.some(v=>v.id===selection)?selection:state.settings.default_voice;
  renderVoiceInfo();
}
async function renderVoiceInfo(){
  const v=state?.voices.find(v=>v.id===$('voice').value);
  $('voiceInfo').textContent=v?'所有对话统一使用：'+v.name+'。'+(v.provider==='gpt-sovits'?'统一使用 J 声音处理参数。':'使用此引擎的声音参数。'):'请先导入一个声音。';
  $('technicalInfo').textContent=v?`模型版本：${v.version} · ${v.reference_count} 段参考录音`:'';
  $('voiceRights').textContent=v?((v.provenance?.author?'作者：'+v.provenance.author+'。':'')+v.license):'';
  const source=v?.provenance?.source_url||'';
  $('voiceSource').hidden=!/^https:\/\//.test(source);if(source)$('voiceSource').href=source;
  if(!v)return;
  for(const option of $('language').options){option.disabled=!(v.languages||['zh','en','ja','ko','yue']).includes(option.value);}
  if($('language').selectedOptions[0]?.disabled)$('language').value='zh';
  $('referenceNote').textContent=v.reference_locked?'已确认的参考录音已锁定。': '参考录音需3～10秒，并与原文对应。';
  try{const refs=await api('/api/references/'+v.id);$('referenceList').replaceChildren();
    for(const r of refs.references){const o=document.createElement('option');o.value=r.index;o.textContent=r.filename+' · '+r.text.slice(0,55);$('referenceList').append(o);}
    $('referenceList').value=String(refs.selected_index??0);
    $('saveReference').disabled=refs.locked;
  }catch(e){notice(e.message,true);}
}
async function refresh(){
  if(pollBusy)return;pollBusy=true;
  try{
    const previous=state;state=await api('/api/state');$('lamp').className='online';
    if(!previous){for(const j of state.jobs){if(j.status==='ready')seen.add(j.id);}commandRevision=state.command.revision;}
    $('engineStatus').textContent=state.engine_state==='generating'?'显卡正在合成':'本机服务已连接';
    if(!initialized){renderVoices();$('speed').value=state.settings.speed;$('speedValue').textContent=Number(state.settings.speed).toFixed(2)+'×';$('autoplay').checked=state.settings.autoplay;$('speechEnabled').checked=state.settings.speech_enabled;$('desktopPlayback').checked=state.settings.desktop_playback!==false;initialized=true;}
    else if(JSON.stringify(previous.voices)!==JSON.stringify(state.voices))renderVoices();
    $('startup').checked=!!state.background?.startup_enabled;
    $('connectionHelp').textContent=state.background?.message||'首次使用需要连接自己的 ChatGPT 账号。';
    const desktop=state.playback?.target==='desktop';
    $('armPlayer').textContent=desktop?'电脑后台播放已就绪':'开启网页聊天播放';
    $('armPlayer').disabled=desktop;
    $('playerHelp').textContent=desktop?'聊天语音由电脑直接播放，可以关闭这个网页。':'网页播放模式需要保持此页打开，并点击上方按钮。';
    if(desktop&&!busy){$('playbackState').textContent=({playing:'电脑正在播放',paused:'电脑已暂停',ended:'电脑播放完成',error:'电脑播放失败',armed:'后台播放器就绪',stopped:'已停止'})[state.playback.state]||'后台播放器就绪';if(state.playback.error)notice(state.playback.error,true);}
    $('bindings').textContent=Object.entries(state.settings.role_bindings).map(([r,v])=>r+' → '+(state.voices.find(x=>x.id===v)?.name||v)).join('；')||'还没有角色绑定';
    const auditionWaiting=state.jobs.some(j=>manualJobs.has(j.id)&&['queued','generating'].includes(j.status));
    $('synthesize').disabled=auditionWaiting;$('synthesize').textContent=auditionWaiting?'正在准备声音…':'试听这句话';
    $('tryInstalled').disabled=auditionWaiting||!installedReady;
    for(const j of state.jobs){if(manualJobs.has(j.id)&&j.status==='error'){manualJobs.delete(j.id);notice('试听没有完成：'+j.error,true);}}
    const signature=JSON.stringify(state.jobs);
    if(signature!==refresh.lastJobs){renderJobs(state.jobs);refresh.lastJobs=signature;}
    if(state.command.revision>commandRevision){commandRevision=state.command.revision;
      if(state.command.action==='pause')audio.pause();
      if(state.command.action==='resume'&&audio.src)audio.play().catch(()=>heartbeat('blocked'));
      if(state.command.action==='stop'){pending=[];for(const j of state.jobs)seen.add(j.id);audio.pause();audio.currentTime=0;busy=false;}
    }
    for(const j of state.jobs){if(j.status==='ready'&&!seen.has(j.id)){seen.add(j.id);if(j.playback_target!=='desktop'&&(state.settings.autoplay||manualJobs.has(j.id)))pending.push(j);}}
    if(!state.settings.autoplay)pending=pending.filter(j=>manualJobs.has(j.id));
    next();
  }catch(e){$('lamp').className='';$('engineStatus').textContent='本机服务未连接';notice(e.message,true);}finally{pollBusy=false;}
}
$('speed').oninput=()=>{$('speedValue').textContent=Number($('speed').value).toFixed(2)+'×';};
$('voice').onchange=()=>{renderVoiceInfo();saveSettings();};
$('speed').onchange=saveSettings;
$('autoplay').onchange=saveSettings;
$('speechEnabled').onchange=saveSettings;
$('desktopPlayback').onchange=saveSettings;
$('startup').onchange=async()=>{try{await api('/api/startup','POST',{enabled:$('startup').checked});notice('自动准备设置已保存。');await refresh();}catch(e){notice(e.message,true);}};
let saving=Promise.resolve();
function saveSettings(){
  const chosen={speed:Number($('speed').value),default_voice:$('voice').value,autoplay:$('autoplay').checked,speech_enabled:$('speechEnabled').checked,desktop_playback:$('desktopPlayback').checked};
  saving=saving.then(async()=>{await api('/api/settings','POST',chosen);notice('设置已自动保存。');await refresh();}).catch(e=>notice('设置未保存：'+e.message,true));
  return saving;
}
$('bindRole').onclick=async()=>{try{const role=$('roleName').value.trim();if(!role)throw Error('请输入角色名称');await api('/api/settings','POST',{role_bindings:{...state.settings.role_bindings,[role]:$('voice').value}});notice('角色已绑定。');await refresh();}catch(e){notice(e.message,true);}};
$('saveReference').onclick=async()=>{try{await api('/api/reference','POST',{voice_id:$('voice').value,index:Number($('referenceList').value)});notice('参考录音已保存。');}catch(e){notice(e.message,true);}};
async function requestSpeech(voiceId){
  const b=$('synthesize');b.disabled=true;armed=true;
  try{
    await saving;
    const job=await api('/api/speak','POST',{text:$('text').value,voice_id:voiceId||$('voice').value,language:$('language').value,speed:Number($('speed').value),hide_text:$('hideText').checked,source:'console'});
    manualJobs.add(job.id);notice('正在准备声音，完成后会自动播放。首次使用会稍久一些。');await refresh();
  }catch(e){b.disabled=false;notice(e.message,true);}
}
$('synthesize').onclick=()=>requestSpeech();
$('armPlayer').onclick=async()=>{armed=true;try{await heartbeat('armed');$('armPlayer').textContent='聊天播放已开启';$('playerHelp').textContent='保持页面打开。回到 ChatGPT，即可接收 AI 选择说出的台词。';notice('播放器已开启。'+($('autoplay').checked?'新语音会自动播放。':'如需自动播放，请打开上方开关。'));next();}catch(e){notice(e.message,true);}};
$('replay').onclick=async()=>{try{if(state.playback?.target==='desktop'&&state.playback.job_id&&!audio.src){await api('/api/replay','POST',{job_id:state.playback.job_id});}else if(audio.src){audio.currentTime=0;audio.play().catch(()=>notice('请点击音频播放器的播放按钮。'));}else{notice('还没有可重播的语音。');}}catch(e){notice(e.message,true);}};
$('pausePlayback').onclick=async()=>{try{await api('/api/control','POST',{action:'pause'});audio.pause();}catch(e){notice(e.message,true);}};
$('resumePlayback').onclick=async()=>{try{await api('/api/control','POST',{action:'resume'});}catch(e){notice(e.message,true);}};
$('stopPlayback').onclick=async()=>{pending=[];audio.pause();audio.currentTime=0;busy=false;try{await api('/api/control','POST',{action:'stop'});}catch(e){notice(e.message,true);}};
$('playbackRate').onchange=()=>audio.playbackRate=Number($('playbackRate').value);

$('exportSettings').onclick=async()=>{try{const data=await api('/api/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='linguavoice-settings.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}catch(e){notice(e.message,true);}};
$('importSettings').onclick=()=>$('settingsFile').click();
$('settingsFile').onchange=async()=>{try{const file=$('settingsFile').files[0];if(!file)return;await api('/api/import-settings','POST',JSON.parse(await file.text()));initialized=false;await refresh();notice('设置已导入。');}catch(e){notice(e.message,true);}};
refresh();setInterval(refresh,300);setInterval(()=>{if(armed)heartbeat(playState).catch(()=>{});},5000);

function installProgress(message,percent){
  $('installProgress').classList.remove('hidden');
  $('importStatus').textContent=message;
  $('installBar').value=percent;
  $('installPercent').textContent=Math.round(percent)+'%';
}
function installationBusy(value){
  installing=value;$('dropZone').setAttribute('aria-disabled',String(value));$('voiceFile').disabled=value;
}
async function pollInstall(){
  if(!installId||installPolling)return;installPolling=true;
  try{
    const job=await api('/api/installs/'+encodeURIComponent(installId));
    installProgress(job.message,25+.75*job.progress);
    if(job.status!=='installing'){
      installId='';sessionStorage.removeItem('linguavoice-install');installationBusy(false);
      if(job.result){
        installedVoice=job.result.id;installedReady=job.status==='ready';$('installedName').value=job.result.name;
        $('installResult').classList.remove('hidden');$('tryInstalled').disabled=job.status!=='ready';
        await refresh();
      }
      if(job.status==='error'||job.status==='needs_setup'){$('installPercent').textContent=job.status==='error'?'未完成':'已保存';notice(job.message,true);}
      else notice('角色已添加。可以试听，或在左边选择它。');
    }
  }catch(e){if(e.status===400||e.status===404){installId='';sessionStorage.removeItem('linguavoice-install');installationBusy(false);installProgress('安装记录已失效，请重新拖入 ZIP；已装好的角色会自动识别。',0);}else{notice('暂时联系不上安装服务，请保持页面打开；恢复连接后会继续显示进度。',true);}}
  finally{installPolling=false;}
}
function installFile(file){
  if(installing){notice('正在安装一个角色，完成后再添加下一个。',true);return;}
  if(!file)return;
  if(!/\.zip$/i.test(file.name)){notice('请添加 ZIP 压缩包，不用先解压。',true);return;}
  if(!file.size||file.size>1024**3){notice('请选择小于 1 GB 的角色 ZIP。较大的合集需要单个角色包。',true);return;}
  installationBusy(true);installedVoice='';installedReady=false;$('installResult').classList.add('hidden');
  $('installFilename').textContent=file.name+' · '+(file.size/1024**2).toFixed(1)+' MB';
  installProgress('正在接收角色包',0);notice('');
  const xhr=new XMLHttpRequest();xhr.open('POST','/api/install');
  xhr.setRequestHeader('Authorization','Bearer '+token);xhr.setRequestHeader('X-Model-Trust','yes');xhr.timeout=180000;
  xhr.upload.onprogress=e=>{if(e.lengthComputable)installProgress(e.loaded===e.total?'正在检查角色包':'正在接收角色包',25*e.loaded/e.total);};
  const fail=message=>{installationBusy(false);$('importStatus').textContent=message;notice(message,true);};
  xhr.onload=()=>{try{const result=JSON.parse(xhr.responseText);if(xhr.status!==202)throw Error(result.error||'安装没有开始，请重试');installId=result.id;sessionStorage.setItem('linguavoice-install',installId);pollInstall();}catch(e){fail(e.message);}};
  xhr.onerror=()=>fail('连接中断了，请确认控制台还在运行，再重新拖入。');
  xhr.ontimeout=()=>fail('传输时间太长，请重新拖入角色包。');xhr.send(file);
}
const dropZone=$('dropZone');
dropZone.onclick=()=>{if(!installing)$('voiceFile').click();};
dropZone.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();dropZone.click();}};
$('voiceFile').onchange=()=>{installFile($('voiceFile').files[0]);$('voiceFile').value='';};
dropZone.ondragover=e=>{e.preventDefault();if(!installing)dropZone.classList.add('dragging');};
dropZone.ondragleave=e=>{if(!dropZone.contains(e.relatedTarget))dropZone.classList.remove('dragging');};
dropZone.ondrop=e=>{e.preventDefault();dropZone.classList.remove('dragging');if(e.dataTransfer.files.length!==1){notice('一次只添加一个角色 ZIP。',true);return;}installFile(e.dataTransfer.files[0]);};
document.addEventListener('dragover',e=>e.preventDefault());
document.addEventListener('drop',e=>{e.preventDefault();if(!dropZone.contains(e.target))notice('请把 ZIP 拖到“添加新角色”的虚线框里。');});
$('tryInstalled').onclick=()=>{if(installedVoice)requestSpeech(installedVoice);};
$('addAnother').onclick=()=>dropZone.click();
$('renameInstalled').onclick=async()=>{try{await api('/api/rename-voice','POST',{voice_id:installedVoice,name:$('installedName').value});await refresh();notice('角色名字已保存。');}catch(e){notice(e.message,true);}};
installationBusy(installing);if(installId)pollInstall();setInterval(pollInstall,750);

async function renderSupport(){
  try{
    const info=await api('/api/support');const box=$('supportContent');box.replaceChildren();
    const heading=text=>{const h=document.createElement('h3');h.textContent=text;box.append(h);};
    const table=(heads,rows)=>{const t=document.createElement('table');t.className='support-table';const top=document.createElement('tr');for(const x of heads){const c=document.createElement('th');c.textContent=x;top.append(c);}t.append(top);for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=value;tr.append(td);}t.append(tr);}box.append(t);};
    heading('模型与引擎');table(['模型 / 版本','当前状态','说明'],info.versions.map(v=>[v.provider+' · '+v.version,v.status,v.note]));
    heading('语言');table(['语言','验证情况'],info.languages.map(x=>[x.name,x.status]));
    heading('可以导入什么');table(['文件','支持情况','说明'],info.imports.map(x=>[x.format,x.status,x.note]));
    heading('本机已有声音');if(info.voices.length){table(['名字','引擎','版本'],info.voices.map(v=>[v.name,v.provider,v.version]));}else{const p=document.createElement('p');p.textContent='还没有安装声音，可以先添加完整角色 ZIP。';box.append(p);}
    for(const [title,list] of [['播放与教学功能',info.features],['使用条件',info.limits]]){heading(title);const ul=document.createElement('ul');for(const text of list){const li=document.createElement('li');li.textContent=text;ul.append(li);}box.append(ul);}
  }catch(e){$('supportContent').textContent='暂时无法读取，请稍后重新展开。';}
}
$('supportSection').addEventListener('toggle',()=>{if($('supportSection').open)renderSupport();});

if($('supportSection').open)renderSupport();
