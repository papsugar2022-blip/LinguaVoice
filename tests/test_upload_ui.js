// Exercise actual drop/file handlers without a browser or synthetic speech.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function setup(){
  const elements=new Map(), requests=[];
  function el(id){
    if(!elements.has(id))elements.set(id,{id,value:'',checked:false,disabled:false,textContent:'',files:[],
      classList:{add(){},remove(){}},setAttribute(){},addEventListener(){},contains(target){return target===this;},click(){},replaceChildren(){},append(){}});
    return elements.get(id);
  }
  const c={document:{getElementById:el,addEventListener(){}},location:{hash:'',pathname:'/'},
    sessionStorage:{getItem(){return '';},setItem(){},removeItem(){}},history:{replaceState(){}},
    URLSearchParams,fetch:()=>new Promise(()=>{}),setInterval(){},setTimeout(){},
    XMLHttpRequest:class{constructor(){this.upload={};this.headers={};requests.push(this);}open(method,path){this.method=method;this.path=path;}setRequestHeader(k,v){this.headers[k]=v;}send(file){this.file=file;}}};
  vm.createContext(c);vm.runInContext(fs.readFileSync('linguavoice/web/app.js','utf8'),c);
  return {el,requests};
}
function drop(app,files){let prevented=false;app.el('dropZone').ondrop({preventDefault(){prevented=true;},dataTransfer:{files}});assert.equal(prevented,true);}
let app=setup();drop(app,[{name:'voice.zip',size:1234}]);
assert.equal(app.requests.length,1);assert.equal(app.requests[0].path,'/api/install');assert.equal(app.requests[0].method,'POST');assert.equal(app.el('voiceFile').disabled,true);
drop(app,[{name:'second.zip',size:1234}]);assert.equal(app.requests.length,1);
app=setup();drop(app,[{name:'recording.wav',size:1234}]);assert.equal(app.requests.length,0);assert.match(app.el('notice').textContent,/ZIP/);
app=setup();drop(app,[{name:'a.zip',size:1234},{name:'b.zip',size:1234}]);assert.equal(app.requests.length,0);assert.match(app.el('notice').textContent,/一次/);
app=setup();app.el('voiceFile').files=[{name:'VOICE.ZIP',size:1234}];app.el('voiceFile').onchange();assert.equal(app.requests.length,1);assert.equal(app.el('voiceFile').value,'');
app.requests[0].onerror();assert.equal(app.el('voiceFile').disabled,false);assert.match(app.el('notice').textContent,/连接中断/);
console.log('Drop, file selection, duplicate in-flight upload, invalid files, and network recovery passed.');
