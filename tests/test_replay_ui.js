const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const script=fs.readFileSync('linguavoice/web/replay.html','utf8').split('<script>')[1].split('</script>')[0];
async function main(){
 const elements=new Map(),sent=[],listeners=new Map(),timers=new Map();let timerSequence=0;const parent={postMessage:m=>sent.push(m)};
 const el=id=>{if(!elements.has(id))elements.set(id,{disabled:true,textContent:''});return elements.get(id);};
 const c={document:{getElementById:el},window:{parent,addEventListener:(name,fn)=>listeners.set(name,fn)},setTimeout:(fn,ms)=>{timers.set(++timerSequence,{fn,ms});return timerSequence;},clearTimeout:id=>timers.delete(id)};
 vm.createContext(c);vm.runInContext(script,c);
 const send=m=>listeners.get('message')({source:parent,data:{jsonrpc:'2.0',...m}});
 send({id:sent[0].id,result:{}});await Promise.resolve();
 const id='a'.repeat(32);
 send({method:'ui/notifications/tool-result',params:{structuredContent:{id,status:'ready',voice_name:'Test'}}});
 assert.equal(el('replay').disabled,false);
 send({method:'ui/notifications/tool-result',params:{structuredContent:{id:'b'.repeat(32),status:'ready'}}});
 const clicked=el('replay').onclick();el('replay').onclick();
 const calls=sent.filter(m=>m.method==='tools/call');assert.equal(calls.length,1);
 assert.equal(calls[0].params.name,'linguavoice_replay');assert.equal(calls[0].params.arguments.job_id,id);
 [...timers.values()].find(t=>t.ms===15000).fn();
 assert.match(el('status').textContent,/等待确认/);assert.equal(el('replay').disabled,true);
 assert.ok([...timers.values()].some(t=>t.ms===120000));
 send({id:calls[0].id,result:{structuredContent:{queued:true,job_id:id}}});await clicked;
 assert.match(el('status').textContent,/已请求/);assert.equal(el('replay').disabled,false);
 const failed=el('replay').onclick();const call=sent.at(-1);
 send({id:call.id,result:{isError:true,content:[{type:'text',text:'这段语音已过期'}]}});await failed;
 assert.equal(el('status').textContent,'这段语音已过期');assert.equal(el('replay').disabled,false);
 // Status polling reads actual backend state; controls remain bound to this card.
 const poll=[...timers.values()].find(t=>t.ms===100);assert.ok(poll);
 const polling=poll.fn();const statusCall=sent.at(-1);
 assert.equal(statusCall.params.name,'linguavoice_card_status');
 send({id:statusCall.id,result:{structuredContent:{id},_meta:{reading:{id,state:'playing',active:true,show_indicator:true,current_text:'Example sentence.',segment_index:2,segment_count:3}}}});
 await polling;
 assert.equal(el('readingText').textContent,'Example sentence.');assert.equal(el('pause').disabled,false);
 const pausing=el('pause').onclick();const pauseCall=sent.at(-1);
 assert.equal(pauseCall.params.name,'linguavoice_card_control');assert.equal(pauseCall.params.arguments.job_id,id);assert.equal(pauseCall.params.arguments.action,'pause');
 send({id:pauseCall.id,result:{structuredContent:{requested:'pause'}}});await pausing;
 const poll2=[...timers.values()].find(t=>t.ms===100).fn();const second=sent.at(-1);
 send({id:second.id,result:{_meta:{reading:{id,state:'paused',active:true,show_indicator:false,current_text:'must not display'}}}});await poll2;
 assert.equal(el('reading').hidden,true);assert.equal(el('readingText').textContent,'');assert.equal(el('resume').disabled,false);
 // The hosted ChatGPT compatibility resource uses its own documented bridge.
 const legacyCalls=[],legacyElements=new Map();
 const legacyEl=id=>{if(!legacyElements.has(id))legacyElements.set(id,{disabled:true,textContent:''});return legacyElements.get(id);};
 const lc={document:{getElementById:legacyEl},window:{parent,addEventListener(){},openai:{
  toolOutput:{id,status:'ready',voice_name:'Test'},
  callTool:async(name,args)=>{legacyCalls.push({name,args});return {structuredContent:{queued:true}};}
 }},setTimeout:()=>1,clearTimeout(){}};
 vm.createContext(lc);vm.runInContext(script.replace('const legacy=false;','const legacy=true;'),lc);
 assert.equal(legacyEl('replay').disabled,false);await legacyEl('replay').onclick();
 assert.equal(legacyCalls.length,1);assert.equal(legacyCalls[0].name,'linguavoice_replay');
 assert.equal(legacyCalls[0].args.job_id,id);assert.match(legacyEl('status').textContent,/已请求/);
 lc.window.openai.callTool=async()=>({structuredContent:{id}});
 lc.window.openai.toolResponseMetadata={mcp_tool_result:{_meta:{reading:{id,state:'playing',active:true,show_indicator:true,current_text:'Wrapped metadata.'}}}};
 await vm.runInContext('pollPosition()',lc);
 assert.equal(legacyEl('readingText').textContent,'Wrapped metadata.');
 assert.equal(legacyEl('pause').disabled,false);
 console.log('PASS: bound recording, duplicate-click guard, result handling and errors. No audio synthesized.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
