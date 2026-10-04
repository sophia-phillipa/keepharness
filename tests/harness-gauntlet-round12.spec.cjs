// Synthetic round-twelve regressions against the actual workspace assets.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {mount,run,span}=require('./run-console-fixture.cjs');
const fs=require('node:fs/promises');
const path=require('node:path');
const settle=p=>p.evaluate(()=>Promise.all(document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));
async function capture(page,name){if(process.env.EVAL_OUTPUT){await fs.mkdir(process.env.EVAL_OUTPUT,{recursive:true});await page.screenshot({path:path.join(process.env.EVAL_OUTPUT,name+'.png')});}}
const hit=locator=>locator.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));});
async function fixture(browser,width=1280,height=720,plan=false){
 const page=await browser.newPage({viewport:{width,height}});page.setDefaultTimeout(3000);
 const state={completed:!plan,plan,posts:[],delay:null,fail:false,version:0};
 const steps=['Research','Review'].map(role=>({role,backend:'codex',model:'fixture',effort:'low',task:role+' synthetic text',reason:'Check facts'}));
 const pending={job_id:'a-job',conversation_id:'a',gate_id:'plan',kind:'maestro_plan',plan:{steps}};
 const turn=()=>({id:'a-job',project:'p',state:state.completed?'completed':'running',request:{prompt:'Review fixture',backend:'codex',model:'fixture',effort:'low',execution_mode:'native'},result:null,gates:state.plan?[{...pending,state:'pending'}]:[]});
 await mount(page,async(url,request)=>{
  if(request.method()==='POST'){state.posts.push({path:url.pathname,data:request.postDataJSON()});if(state.delay)await state.delay;return{json:{resolved:true}};}
  if(url.pathname==='/v1/projects')return{json:{projects:['p'],details:{p:{label:'Synthetic project'}}}};
  if(url.pathname==='/v1/models')return{json:{uploads_enabled:true,models:[{id:'fixture',backend:'codex',uploads:true,efforts:['low','high']},{id:'other',backend:'codex',efforts:['medium','high']}],providers:{codex:true},maestro:true}};
  if(url.pathname==='/v1/resources')return{json:{items:[],warnings:[]}};
  if(url.pathname==='/v1/conversations')return{json:{conversations:[{id:'a',title:'Synthetic report',state:'running',project:'p',last_job_id:'a-job',backend:'codex',model:'fixture'}]}};
  if(url.pathname==='/v1/conversations/a')return{json:{title:'Synthetic report',turns:[turn()]}};
  if(url.pathname==='/v1/jobs/a-job')return{json:turn()};
  if(url.pathname==='/v1/activity')return{json:{counts:{running:1},jobs:[{...run,job_id:'a-job',conversation_id:'a',project_id:'p',backend:'codex',model:'fixture'}],needs_you:state.plan?[pending]:[],providers:[]}};
  if(url.pathname.endsWith('/spans'))return{json:{spans:Array.from({length:4},(_,i)=>({...span,span_id:'step-'+i,name:'Implementation and integration engineer',start_ts:Date.now()/1000-123,end_ts:state.completed?Date.now()/1000:null,attrs:{backend:'codex',model:'gpt-6-astra',effort:'high',enforcement:'mediated',version:state.version}}))}};
  if(url.pathname.endsWith('/events'))return{body:'',contentType:'text/event-stream'};
  if(url.pathname==='/v1/project-files'){
   const folder=url.searchParams.get('path')||'';
   if(folder && state.fileDelay)await state.fileDelay;
   if(folder && state.fail)return{status:503,json:{code:'synthetic_unavailable'}};
   const entries=folder==='alpha'?[{name:'nested',path:'alpha/nested',type:'directory'},{name:'child.txt',path:'alpha/child.txt',type:'file'}]:folder==='alpha/nested'?[{name:'deep.txt',path:'alpha/nested/deep.txt',type:'file'}]:folder?[]:[{name:'alpha',path:'alpha',type:'directory'},{name:'beta',path:'beta',type:'directory'},{name:'synthetic.txt',path:'synthetic.txt',type:'file'}];
   return{json:{state:'ready',root_id:'home',path:folder,roots:[{id:'home',label:'Local Folders'}],entries}};
  }
 });
 async function open(){if(width<=620)await page.locator('#menu').click();await page.locator('#sidebar .conversation-row > button').filter({hasText:'Synthetic report'}).click();await page.waitForFunction(()=>!loading && document.querySelector('#conversation-title').textContent==='Synthetic report');}
 async function consoleOpen(){await open();await page.evaluate(()=>runConsole.openRun('a-job'));await page.locator('#run-console').waitFor();await page.locator('.run-span-row').first().waitFor();await settle(page);}
 return{page,state,open,consoleOpen};
}
async function publication(page){await page.evaluate(()=>showGate({gate_id:'publish',kind:'publish',publish:true,effect_id:'effect',operation:'jira.create_issue',destination:'SYNTHETIC',artifact_preview:Array(40).fill('Synthetic evidence').join('\n'),options:[{id:'approve',label:'Approve'},{id:'deny',label:'Deny'}]}));}
(async()=>{
 const browser=await chromium.launch();const failures=[];
 async function check(id,fn){if(process.env.ONLY&&!process.env.ONLY.split(',').some(x=>id.startsWith(x)))return;try{await fn();console.log('PASS '+id);}catch(e){failures.push(id+': '+e.stack);console.error('FAIL '+id+': '+e.message);}}
 try{
 await check('A2-F1 Pointer to keyboard tree navigation',async()=>{
  for(const width of [400,1440]){
   const f=await fixture(browser,width,844),p=f.page;
   if(await p.locator('#activity-panel').isHidden())await p.locator('#panel-toggle').click();
   await p.getByRole('button',{name:'Expand alpha',exact:true}).click();
   await p.locator('#files-tree [data-path="alpha/nested"]').first().waitFor();
   await p.keyboard.press('ArrowDown');assert.equal(await p.locator(':focus').getAttribute('data-path'),'alpha/nested');
   await p.keyboard.press('ArrowRight');await p.locator('#files-tree [data-path="alpha/nested/deep.txt"]').waitFor();
   await p.keyboard.press('End');assert.equal(await p.locator(':focus').getAttribute('data-path'),'synthetic.txt');
   await p.keyboard.press('Home');assert.equal(await p.locator(':focus').getAttribute('data-path'),'alpha');
   assert(await hit(p.locator(':focus')));assert.equal(await p.locator('#files-tree [role=treeitem][tabindex="0"]').count(),1);
   await capture(p,'tree-navigation-'+width);await p.close();
  }
 });
 await check('A2-F2 Files exposes multiple selection',async()=>{
  for(const width of [400,1440]){
   const f=await fixture(browser,width,844),p=f.page;
   if(await p.locator('#activity-panel').isHidden())await p.locator('#panel-toggle').click();
   const tree=p.locator('#files-tree');await tree.locator('[role=treeitem]').first().focus();
   await p.keyboard.press('Space');await p.keyboard.press('ArrowDown');await p.keyboard.press('Space');
   assert.equal(await tree.locator('[aria-selected=true]').count(),2);
   const cdp=await p.context().newCDPSession(p);const ax=await cdp.send('Accessibility.getFullAXTree');
   const node=ax.nodes.find(n=>n.role?.value==='tree'&&n.name?.value==='Authorized files');
   assert.equal(node.properties.find(p=>p.name==='multiselectable').value.value,true);
   await p.keyboard.press('Space');assert.equal(await tree.locator('[aria-selected=true]').count(),1);
   assert(await hit(p.locator(':focus')));await capture(p,'tree-selection-'+width);await p.close();
  }
 });
 await check('A5-F1 Invalid task stays local and focuses its field',async()=>{
  const f=await fixture(browser,1440,900,true),p=f.page;await f.consoleOpen();await p.locator('#run-plan-edit').click();
  const task=p.getByLabel('Task for step 1',{exact:true});await task.fill('   ');
  await p.getByRole('button',{name:'Run with edits (1)',exact:true}).click();
  assert.equal(f.state.posts.length,0);assert(await task.evaluate(n=>n===document.activeElement));
  assert.equal(await task.getAttribute('aria-invalid'),'true');
  assert.match(await p.locator('.run-plan-approval [role=status]').innerText(),/Enter.*task.*step 1/i);
  assert(await hit(task));await capture(p,'invalid-task');
  await task.fill('Restored task');await p.getByRole('button',{name:'Run with edits (1)',exact:true}).click();
  await p.waitForTimeout(100);assert.equal(f.state.posts.length,1);assert.equal(f.state.posts[0].data.plan.steps[0].task,'Restored task');await p.close();
 });
 await check('A5-F1 Storage warnings survive field validation',async()=>{
  const f=await fixture(browser,1440,900,true),p=f.page;await f.consoleOpen();await p.locator('#run-plan-edit').click();
  await p.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(key,value){if(key.startsWith('plan-draft:'))throw Error('synthetic storage unavailable');return original.call(this,key,value);};});
  await p.getByLabel('Task for step 1',{exact:true}).fill('');
  const feedback=await p.locator('.run-plan-approval [role=status]').innerText();assert.match(feedback,/not saved/);assert.match(feedback,/Enter a task for step 1/);assert.equal(f.state.posts.length,0);
  await p.getByRole('button',{name:'Hide editor',exact:true}).click();await p.getByRole('button',{name:'Run with edits (1)',exact:true}).click();
  const restored=await p.locator('.run-plan-approval [role=status]').innerText();assert.match(restored,/not saved/);assert.match(restored,/Enter a task for step 1/);assert.equal(f.state.posts.length,0);assert(await p.getByLabel('Task for step 1',{exact:true}).evaluate(n=>n===document.activeElement));await p.close();
 });
 await check('A5-F1 Unavailable model and effort stay local',async()=>{
  for(const field of ['model','effort']){
   const f=await fixture(browser,1440,900,true),p=f.page;
   await p.evaluate(field=>sessionStorage.setItem('plan-draft:plan',JSON.stringify({steps:[{role:'Research',backend:'codex',model:field==='model'?'missing':'fixture',effort:field==='effort'?'missing':'low',task:'Synthetic task',reason:'Check facts'}]})),field);
   await f.consoleOpen();await p.locator('#run-plan-edit').click();await p.locator('.run-plan-actions button').filter({hasText:'Run with edits'}).click();
   assert.equal(f.state.posts.length,0);assert.match(await p.locator('.run-plan-approval [role=status]').innerText(),/available.*step 1/i);
   const input=p.getByLabel((field==='model'?'Model':'Effort')+' for step 1',{exact:true});assert(await input.evaluate(n=>n===document.activeElement));
   await input.selectOption(field==='model'?'codex/fixture':'low');await p.locator('.run-plan-actions button').filter({hasText:'Run with edits'}).click();await p.waitForTimeout(100);assert.equal(f.state.posts.length,1);await p.close();
  }
 });
 await check('A1-F1 Complete default cards across publication and completed states',async()=>{
  for(const [width,height] of [[1440,900],[1280,720],[1024,768],[400,812]])for(const theme of ['porcelain','amethyst','petroleum'])for(const published of [false,true]){
   const f=await fixture(browser,width,height),p=f.page;f.state.completed=!published;await f.open();if(published)await publication(p);
   await p.evaluate(theme=>{HarnessTheme.apply(theme);runConsole.openRun('a-job');},theme);await p.locator('.run-span-row').first().waitFor();
   for(const tab of ['Pipeline','Timeline']){
    await p.getByRole('tab',{name:tab,exact:true}).click();await settle(p);await p.waitForTimeout(150);
    const row=p.locator('.run-span-row').first(),b=await p.locator('.run-console-body').boundingBox(),r=await row.boundingBox();
    assert(r.y>=b.y&&r.y+r.height<=b.y+b.height,JSON.stringify({width,height,theme,published,tab,r,b}));
    assert(await row.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.bottom-2));}));
    assert(await hit(row));await capture(p,`card-${width}-${theme}-${published}-${tab}`);
   }
   if(published){
    if(width===400)await p.keyboard.press('Escape');
    const evidence=p.locator('.publish-evidence');await evidence.evaluate(n=>n.scrollTop=n.scrollHeight);
    for(const name of ['Approve','Deny']){const choice=p.locator('.publish-gate-card').getByRole('button',{name,exact:true});await choice.scrollIntoViewIfNeeded();assert(await hit(choice));}
   }
   await p.close();
  }
 });
 }finally{await browser.close();}
 if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
})();
