// Round-thirteen synthetic browser regressions, with actual assets and pointer hit tests.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {mount,run,span}=require('./run-console-fixture.cjs');
const fs=require('node:fs/promises');
const path=require('node:path');
const hit=loc=>loc.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));});
async function fixture(browser,width=1280,kind='plan'){
 const page=await browser.newPage({viewport:{width,height:844}});page.setDefaultTimeout(4000);
 const state={posts:[],delay:null,fail:false,childFailure:kind==='recovery',accepted:false,plan:kind==='plan',title:'Completed synthetic review with a long descriptive execution title'};
 const steps=[{role:'Review',backend:'codex',model:'fixture',effort:'low',task:'Review synthetic facts',reason:'Check evidence'}];
 const pending={job_id:'source-job',conversation_id:'source',gate_id:'plan',kind:'maestro_plan',plan:{steps}};
 const turn=id=>({id:id+'-job',project:'p',state:state.plan?'running':id==='source'?'failed':'completed',request:{prompt:'Review fixture',backend:'codex',model:'fixture',effort:'low',execution_mode:'native'},result:{answer:'Synthetic result'},workflow_checkpoint:kind==='recovery',workflow_completed_steps:id==='source'?1:2,gates:state.plan?[{...pending,state:'pending'}]:[]});
 const items=['checker','reviewer'].map(name=>({id:'project/p/'+name,resource_id:'project/p/'+name,revision:'s1',kind:'skill',name,description:'Synthetic '+name,scope:'project',origin:'agents',group:'Skills',selectable:true}));
 await mount(page,async(url,request)=>{
  if(request.method()==='POST'){
   state.posts.push({path:url.pathname,data:request.postDataJSON(),key:request.headers()['idempotency-key']});
   if(url.pathname.endsWith('/resume')){state.accepted=true;return{status:202,json:{job_id:'child-job',conversation_id:'child'}};}
   if(state.delay)await state.delay;
   if(state.fail)return{status:503,json:{code:'synthetic_unavailable'}};
   state.plan=false;return{json:{resolved:true}};
  }
  if(url.pathname==='/v1/projects')return{json:{projects:['p'],details:{p:{label:'Synthetic project'}}}};
  if(url.pathname==='/v1/models')return{json:{models:[{id:'fixture',backend:'codex',efforts:['low']}],providers:{codex:true},maestro:true}};
  if(url.pathname==='/v1/resources')return{json:{items,warnings:[]}};
  if(url.pathname==='/v1/conversations')return{json:{conversations:['source',...(state.accepted?['child']:[])].map(id=>({id,title:id==='source'?'Synthetic source':'Resumed synthetic run',state:turn(id).state,project:'p',last_job_id:id+'-job',backend:'codex',model:'fixture'}))}};
  if(url.pathname==='/v1/conversations/child'&&state.childFailure)return{status:503,json:{code:'synthetic_unavailable'}};
  if(url.pathname.startsWith('/v1/conversations/')){const id=url.pathname.split('/').pop();return{json:{title:id==='source'?'Synthetic source':'Resumed synthetic run',turns:[turn(id)]}};}
  if(url.pathname==='/v1/jobs/source-job')return{json:turn('source')};
  if(url.pathname==='/v1/jobs/child-job')return{json:turn('child')};
  if(url.pathname==='/v1/activity')return{json:{counts:{running:state.plan?1:0,needs_you:state.plan?1:0},jobs:[{...run,...turn('source'),job_id:'source-job',conversation_id:'source',project_id:'p',backend:'codex',model:'fixture',title:state.title,work_item:null}],needs_you:state.plan?[pending]:[],providers:[]}};
  if(url.pathname.endsWith('/spans'))return{json:{spans:[{...span,span_id:'review',name:'Review synthetic facts',attrs:{backend:'codex',model:'fixture'}}]}};
  if(url.pathname.endsWith('/events'))return{body:'',contentType:'text/event-stream'};
 });
 async function open(){if(width<=620)await page.locator('#menu').click();await page.locator('#history .conversation-row > button').filter({hasText:'Synthetic source'}).click();await page.waitForFunction(()=>!loading&&document.querySelector('#conversation-title').textContent==='Synthetic source');}
 return{page,state,open};
}
(async()=>{
 const browser=await chromium.launch();const failures=[];
 async function check(id,fn){if(process.env.ONLY&&!id.startsWith(process.env.ONLY))return;try{await fn();console.log('PASS '+id);}catch(e){failures.push(id+': '+e.stack);console.error('FAIL '+id+': '+e.message);}}
 try{
 await check('Isolation error guidance',async()=>{
  const f=await fixture(browser),p=f.page;
  for(const code of ['scoped_private_file_linked','scoped_private_files_unavailable','project_root_unavailable']){
   const message=await p.evaluate(code=>userErrors[code],code);assert.equal(typeof message,'string');assert.match(message,/owner|folder/i);assert(!message.includes(code));
  }await p.close();
 });
 await check('A1-F1 status disclosure',async()=>{
  const f=await fixture(browser,400),p=f.page;await f.open();
  for(const theme of ['porcelain','amethyst','petroleum'])for(const pending of [true,false]){
   f.state.plan=pending;await p.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.dispatchEvent(new Event('tail:history'));},theme);
   await p.waitForFunction(pending=>document.querySelector('#run-status-toggle').textContent.includes(pending?'awaiting approval':'Completed synthetic'),pending);
   const label=p.locator('#run-status-toggle');assert(await label.evaluate(n=>n.scrollWidth>n.clientWidth));assert(await hit(label));
   assert((await label.getAttribute('title')).includes(await label.innerText()));
   await p.keyboard.press('Control+j');assert((await label.getAttribute('title')).includes(await label.innerText()));await p.keyboard.press('Control+j');
  }await p.close();
 });
 await check('A2-F1 refine after arrows',async()=>{
  for(const width of [1280,400]){
   const f=await fixture(browser,width,'empty'),p=f.page;const input=p.locator('#prompt');await input.fill('/c');await p.locator('#resource-menu [role=option]').first().waitFor();
   await p.keyboard.press('ArrowDown');await p.keyboard.press('Backspace');await p.keyboard.type('review');
   assert.equal(await input.inputValue(),'/review');await p.waitForFunction(()=>document.querySelectorAll('#resource-menu [role=option]').length===1);
   const option=p.locator('#resource-menu [role=option]');assert.match(await option.innerText(),/reviewer/);await p.keyboard.press('ArrowUp');assert(await hit(option));await p.keyboard.press('Tab');assert.equal(await input.inputValue(),'/reviewer ');await p.close();
  }
 });
 await check('A4-F2 tabbed fence selection',async()=>{
  const f=await fixture(browser,1280,'empty'),p=f.page;
  for(const marker of ['-','1.'])for(const fence of ['```','~~~']){
   const text=`> ${marker}\t${fence}text\n>    /reviewer example\n>    ${fence}\n/rev`;
   await p.locator('#prompt').fill(text);await p.locator('#resource-menu [role=option]').first().waitFor();await p.keyboard.press('ArrowDown');await p.keyboard.press('Enter');
   const selected=await p.evaluate(()=>selectedOccurrences().map(x=>({start:x.start,token:x.ref.token})));assert.equal(selected.at(-1).start,text.lastIndexOf('/rev'));assert((await p.locator('#prompt').inputValue()).includes('/reviewer example'));
  }await p.close();
 });
 await check('A5-F1 accepted resume navigation recovery',async()=>{
  const f=await fixture(browser,1280,'recovery'),p=f.page;await f.open();await p.locator('#prompt').fill('Preserved synthetic draft');
  await p.getByRole('button',{name:'Resume workflow',exact:true}).click();
  const retry=p.getByRole('button',{name:'Open resumed run',exact:true});await retry.waitFor();await p.waitForFunction(()=>!document.querySelector('.workflow-recovery button').disabled);assert(await hit(retry));
  assert.match(await p.locator('.workflow-recovery [role=status]').innerText(),/resumed/i);assert.equal(await p.locator('#prompt').inputValue(),'Preserved synthetic draft');
  f.state.childFailure=false;await retry.click();await p.waitForFunction(()=>document.querySelector('#conversation-title').textContent==='Resumed synthetic run');assert.equal(f.state.posts.filter(x=>x.path.endsWith('/resume')).length,1);
  if(process.env.EVAL_OUTPUT){await fs.mkdir(process.env.EVAL_OUTPUT,{recursive:true});await p.screenshot({path:path.join(process.env.EVAL_OUTPUT,'resume-child.png')});}await p.close();
 });
 await check('A5-F2 cross-surface pending and retry',async()=>{
  for(const from of ['console','chat']){
   const f=await fixture(browser),p=f.page;await f.open();await p.evaluate(()=>runConsole.openRun('source-job'));await p.locator('#run-plan-approve-plan').waitFor();
   let release;f.state.delay=new Promise(r=>release=r);f.state.fail=true;
   if(from==='console')await p.locator('#run-plan-approve-plan').click();else{await p.keyboard.press('Control+j');await p.locator('.maestro-plan-actions button').first().click();}
   await p.waitForFunction(()=>document.querySelector('.maestro-plan-actions button').disabled);
   if(from==='console')await p.keyboard.press('Control+j');else await p.keyboard.press('Control+j');
   assert.equal(await p.locator('#run-plan-approve-plan').isDisabled(),true);assert.equal(await p.locator('#run-plan-discard-plan').isDisabled(),true);assert.equal(f.state.posts.length,1);
   release();await p.waitForFunction(()=>!document.querySelector('.maestro-plan-actions button').disabled&&!document.querySelector('#run-plan-approve-plan').disabled);
   f.state.fail=false;f.state.delay=null;if(await p.locator('#run-console').isVisible())await p.keyboard.press('Control+j');await p.locator('.maestro-plan-actions button').first().click();await p.waitForFunction(()=>document.querySelector('.maestro-plan-card .state-pill').textContent.includes('Approved'));assert.equal(f.state.posts.length,2);await p.close();
  }
 });
 }finally{await browser.close();}
 if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
})();
