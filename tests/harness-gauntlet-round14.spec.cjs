// Synthetic round-fourteen UI contracts exercised with real rendered assets.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const {mount,run,span}=require('./run-console-fixture.cjs');
const themes=['porcelain','amethyst','petroleum','violet-bordeaux','mineral-rose','arizona'];
const providers=['codex','claude','gemini','deepseek','local','maestro'];
const icons=['brand-openai','brand-claude','brand-gemini','stack-2','stack-2','keepharness'];
const hit=loc=>loc.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));});
async function fixture(browser,width=1440,height=900,empty=false){
 const page=await browser.newPage({viewport:{width,height}});page.setDefaultTimeout(4000);
 const state={empty,count:602,failSend:false,posts:[],resources:[],conversations:[]};
 await mount(page,async(url,request)=>{
  if(request.method()==='POST')state.posts.push(request.postDataJSON());
  if(url.pathname==='/v1/resources')return{json:{items:state.resources,warnings:[]}};
  if(url.pathname==='/v1/conversations')return{json:{conversations:state.conversations}};
  if(request.method()==='POST'&&state.failSend)return{status:503,json:{code:'synthetic_offline'}};
  if(url.pathname==='/v1/models')return{json:{models:state.empty?[]:[{id:'fixture',backend:'local',efforts:['configured']}],providers:{local:true},admin_url:'http://127.0.0.1:9999'}};
  if(url.pathname==='/v1/activity')return{json:{counts:{running:6},jobs:providers.map(backend=>({...run,job_id:backend,title:backend,backend,model:'fixture-'+backend})),providers:providers.map(backend=>({backend,model:'fixture-'+backend,state:'busy',running:1,queued:0})),needs_you:[]}};
  if(url.pathname.endsWith('/spans'))return{json:{spans:[span]}};
  if(url.pathname.endsWith('/events')){const after=Number(url.searchParams.get('after')||0),before=Number(url.searchParams.get('before')||state.count+1),newest=url.searchParams.get('order')==='newest';let events=Array.from({length:state.count},(_,i)=>({id:i+1,timestamp:1700000000+i,type:'started',data:{message:'Synthetic event '+(i+1)}})).filter(e=>e.id>after&&e.id<before);if(newest)events.reverse();const more=events.length>200;events=events.slice(0,200);return{json:{events,next_after:Math.max(after,...events.map(e=>e.id)),next_before:Math.min(before,...events.map(e=>e.id)),has_more:more}};}

  if(url.pathname==='/v1/project-files')return{json:{can_authorize:false,roots:[{id:'root',path:'/synthetic/root'}],entries:[{name:'Quarterly-release-evidence-and-accessibility-review-for-all-supported-viewports-and-palettes',type:'directory',path:'folder'},{name:'README.md',type:'file',path:'README.md'}]}};
 });
 return{page,state};
}
(async()=>{
 const browser=await chromium.launch();const failures=[];
 async function check(id,fn){if(process.env.ONLY&&!id.startsWith(process.env.ONLY))return;try{await fn();console.log('PASS '+id);}catch(e){failures.push(id+': '+e.stack);console.error('FAIL '+id+': '+e.message);}}
 try{
 await check('OWNER-F1 sprite icons',async()=>{
  const {page:p}=await fixture(browser);
  for(const theme of themes){await p.evaluate(t=>HarnessTheme.apply(t),theme);
   for(const selector of ['#rail-space','#rail-scheduled','#rail-runs','#rail-agents','#attention-bell','#settings','#menu','#panel-toggle','#search-conversations']){const n=p.locator(selector+' svg use');assert.equal(await n.count(),1,selector);assert(await n.evaluate(n=>{const r=n.getBoundingClientRect();return r.width>0&&r.height>0;}));assert(await hit(p.locator(selector)));}
   await p.keyboard.press('Control+j');for(const n of await p.locator('.run-console-tabs [role=tab]').all()){assert.equal(await n.locator('svg use').count(),1);assert(await n.locator('svg use').evaluate(n=>{const r=n.getBoundingClientRect();return r.width>0&&r.height>0;}));assert(await hit(n));}await p.keyboard.press('Control+j');
   if(await p.locator('#panel-toggle').getAttribute('aria-expanded')!=='true')await p.locator('#panel-toggle').click();for(const n of await p.locator('.workspace-section > summary').all()){assert.equal(await n.locator('svg use').count(),1);assert(await n.locator('svg use').evaluate(n=>{const r=n.getBoundingClientRect();return r.width>0&&r.height>0;}));assert(await hit(n));}if(await p.locator('#panel-toggle').getAttribute('aria-expanded')!=='true')await p.locator('#panel-toggle').click();
  }await p.close();
 });
 await check('OWNER-F2 compact blocked composer',async()=>{
  for(const [width,height] of [[1440,900],[1280,720],[1024,768],[400,812]])for(const theme of ['porcelain','amethyst']){
   const {page:p,state}=await fixture(browser,width,height);await p.evaluate(t=>HarnessTheme.apply(t),theme);await p.locator('#prompt').fill('Preserved synthetic draft');state.empty=true;await p.evaluate(()=>{policyProject=null;return refreshProjectPermissions();});await p.evaluate(()=>modelAvailability());
   const notice=p.locator('#model-availability');assert(await notice.evaluate(n=>!!n.closest('.composer-area')));assert(await p.locator('#prompt').isDisabled());assert(await p.locator('#send').isDisabled());assert((await notice.boundingBox()).height<=86);assert(await hit(p.locator('#models-retry')));assert(await hit(p.locator('#admin-link')));
   state.empty=false;await p.locator('#models-retry').click();await p.waitForFunction(()=>!document.querySelector('#prompt').disabled);assert.equal(await p.locator('#prompt').inputValue(),'Preserved synthetic draft');await p.close();
  }
 });
 await check('OWNER-F3 log chronology',async()=>{
  const {page:p,state}=await fixture(browser);await p.evaluate(()=>runConsole.openRun('codex'));await p.getByRole('tab',{name:'Logs',exact:true}).click();await p.waitForFunction(()=>document.querySelectorAll('.run-log-row').length===200);
  assert.equal(await p.locator('.run-log-row').first().locator('td').nth(1).innerText(),'602');
  const time=p.locator('.run-log-row time').first();assert.equal(await time.getAttribute('title'),new Date(1700000601*1000).toISOString());assert.equal(await time.innerText(),await p.evaluate(()=>new Date(1700000601*1000).toLocaleString(undefined,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'})));
  const sort=p.getByLabel('Log order');await sort.selectOption('oldest');assert.equal(await p.locator('.run-log-row td').nth(1).innerText(),'403');await sort.selectOption('newest');
  for(const count of [400,600,602]){await p.getByRole('button',{name:'Load more events',exact:true}).click();await p.waitForFunction(n=>document.querySelectorAll('.run-log-row').length===n,count);assert.equal(await p.locator('.run-log-row td').nth(1).innerText(),'602');}
  await p.locator('#console-log-search').focus();await p.waitForTimeout(50);await p.locator('#run-console-panel').evaluate(n=>n.scrollTop=90);const before=await p.locator('#run-console-panel').evaluate(n=>n.scrollTop);state.count=603;await p.evaluate(()=>runConsole.observe({type:'started',job_id:'codex'}));await p.waitForFunction(()=>document.querySelectorAll('.run-log-row').length===603);
  assert.equal(await p.locator('.run-log-row td').nth(1).innerText(),'603');assert(await p.locator('#console-log-search').evaluate(n=>n===document.activeElement));assert.equal(await p.locator('#run-console-panel').evaluate(n=>n.scrollTop),before);await p.close();
 });
 await check('OWNER-F4 provider identities',async()=>{
  const {page:p}=await fixture(browser);await p.keyboard.press('Control+j');await p.getByRole('tab',{name:'Runs',exact:true}).click();
  for(let i=0;i<providers.length;i++){const row=p.locator('.run-table tbody tr').filter({hasText:'fixture-'+providers[i]});assert.equal(await row.locator('svg use').getAttribute('href'),'/assets/icons.svg#'+icons[i]);assert(await row.locator('svg').evaluate(n=>n.getBoundingClientRect().width>=14));}
  await p.getByRole('tab',{name:'Agents',exact:true}).click();for(let i=0;i<providers.length;i++)assert.equal(await p.locator('.run-agent-row').filter({hasText:'fixture-'+providers[i]}).locator('svg use').getAttribute('href'),'/assets/icons.svg#'+icons[i]);
  await p.keyboard.press('Control+j');if(await p.locator('#panel-toggle').getAttribute('aria-expanded')!=='true')await p.locator('#panel-toggle').click();for(let i=0;i<providers.length;i++)assert.equal(await p.locator('#workspace-background-tasks button').filter({hasText:'fixture-'+providers[i]}).locator('svg use').getAttribute('href'),'/assets/icons.svg#'+icons[i]);await p.close();
 });
 await check('A1-F5 F6 file disclosure and icons',async()=>{
  for(const width of [1440,400])for(const theme of ['porcelain','amethyst','petroleum']){const {page:p}=await fixture(browser,width,812);await p.evaluate(t=>HarnessTheme.apply(t),theme);if(await p.locator('#panel-toggle').getAttribute('aria-expanded')!=='true')await p.locator('#panel-toggle').click();const row=p.locator('.authorized-root-card li').first();await row.waitFor();const label=row.locator('span').first();assert.match(await label.getAttribute('title'),/Quarterly-release/);assert(await label.evaluate(n=>n.scrollWidth>n.clientWidth));assert.equal(await row.locator('svg use').count(),1);assert(await row.locator('svg').evaluate(n=>n.getBoundingClientRect().width>0));assert(await hit(row));assert.equal(await p.locator('.authorized-root-card li').nth(1).locator('svg use').count(),1);await p.close();}
 });
 await check('A1-F7 connection contrast',async()=>{
  const {page:p}=await fixture(browser);await p.locator('#settings').click();await p.locator('#setup').click();
  for(const theme of themes){await p.evaluate(t=>HarnessTheme.apply(t),theme);await p.waitForTimeout(300);const ratio=await p.locator('#setup-code').evaluate(n=>{const c=getComputedStyle(n);const lum=s=>{const v=s.match(/[\d.]+/g).slice(0,3).map(x=>{x=Number(x)/255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4;});return v[0]*.2126+v[1]*.7152+v[2]*.0722;};const a=lum(c.color),b=lum(c.backgroundColor);return(Math.max(a,b)+.05)/(Math.min(a,b)+.05);});assert(ratio>=4.5,theme+' contrast '+ratio);assert(await hit(p.locator('#setup-code')));}await p.close();
 });
 await check('A2-F1 short tour actions',async()=>{
  const {page:p}=await fixture(browser,400,400);await p.locator('#settings').click();await p.locator('#settings-tour').click();
  for(let i=0;i<16&&await p.locator('#tour-next').count();i++){await p.waitForTimeout(350);assert(await hit(p.locator('#tour-next')),await p.locator('#tour-title').innerText());await p.keyboard.press('Enter');}await p.close();
 });
 await check('A2-F2 failed send restores keyboard focus',async()=>{
  for(const via of ['button','composer']){const {page:p,state}=await fixture(browser,400,812);await p.locator('#prompt').fill('Exact synthetic offline draft');state.failSend=true;
   await p.locator(via==='button'?'#send':'#prompt').focus();await p.keyboard.press('Enter');await p.waitForFunction(()=>document.querySelector('#status').textContent.includes("Couldn't run"));
   assert.equal(await p.locator('#prompt').inputValue(),'Exact synthetic offline draft');assert(['prompt','send'].includes(await p.evaluate(()=>document.activeElement.id)));assert(await hit(p.locator('#'+await p.evaluate(()=>document.activeElement.id))));await p.close();
  }
 });
 await check('A2-F3 console initial-focus fallback',async()=>{
  const {page:p}=await fixture(browser);await p.evaluate(()=>document.activeElement.blur());await p.keyboard.press('Control+j');await p.keyboard.press('Shift+Tab');await p.keyboard.press('ArrowUp');await p.keyboard.press('Escape');assert.equal(await p.evaluate(()=>document.activeElement.id),'run-status-toggle');assert(await hit(p.locator('#run-status-toggle')));
  await p.locator('#prompt').focus();await p.keyboard.press('Control+j');await p.keyboard.press('Control+j');assert.equal(await p.evaluate(()=>document.activeElement.id),'prompt');await p.close();
 });
 await check('A2-F4 automatic tour dismissal fallback',async()=>{
  for(const exit of ['escape','skip','finish']){const {page:p}=await fixture(browser);await p.evaluate(()=>{localStorage.removeItem('keepharness-tour-seen');document.activeElement.blur();});await p.addInitScript(()=>localStorage.removeItem('keepharness-tour-seen'));await p.reload();await p.locator('#tour-next').waitFor();
   if(exit==='escape')await p.keyboard.press('Escape');else if(exit==='skip')await p.locator('#tour-skip').click();else{for(let i=0;i<20&&await p.locator('#tour-next').count();i++)await p.locator('#tour-next').click();}
   assert.equal(await p.evaluate(()=>document.activeElement.id),'prompt');assert(await hit(p.locator('#prompt')));await p.close();
  }
 });
 await check('OWNER-F2 provider conditions stay at composer',async()=>{
  for(const [width,height] of [[1440,900],[1280,720],[1024,768],[400,812]])for(const theme of ['porcelain','amethyst'])for(const condition of ['backend_unavailable','provider_authentication_required','provider_quota_exhausted']){const {page:p}=await fixture(browser,width,height);await p.evaluate(t=>HarnessTheme.apply(t),theme);await p.locator('#prompt').fill('Preserved blocked draft');await p.evaluate(async condition=>{active=assistant('failed-fixture','fixture',true);job='failed-fixture';await result(job,controller,{id:job,state:'failed',request:{backend:'local',model:'fixture'},result:{condition,backend:'local'}});},condition);
   assert(await p.locator('#prompt').isDisabled());assert(await p.locator('#model-availability').isVisible());assert((await p.locator('#model-availability').boundingBox()).height<=86);assert.equal(await p.locator('#prompt').inputValue(),'Preserved blocked draft');assert.equal(await p.locator('#messages').getByText(/quota is temporarily exhausted|access needs to be renewed/).count(),0);await p.locator('#models-retry').click();await p.waitForFunction(()=>!document.querySelector('#prompt').disabled);await p.close();
  }
 });
 await check('A5-F1 permission failure survives history result',async()=>{
  for(const terminalState of ['completed','failed','cancelled','interrupted']){
  const p=await browser.newPage();let fail=true;await mount(p,async url=>{
   if(url.pathname==='/v1/projects')return{json:{projects:['sem-projeto','other']}};
   if(url.pathname==='/v1/models'){if(fail&&url.searchParams.get('project_id')==='other')return{status:503,json:{code:'synthetic_unavailable'}};return{json:{models:[{id:'fixture',backend:'local',efforts:['configured']}]}};}
   if(url.pathname==='/v1/conversations')return{json:{conversations:[{id:'done',title:'Synthetic completed report',project:'other',state:terminalState,last_job_id:'done',execution:{backend:'local',model:'fixture'}}]}};
   const turn={id:'done',project:'other',state:terminalState,request:{backend:'local',model:'fixture',prompt:'Synthetic prior request'},result:{answer:'Synthetic result'}};
   if(url.pathname==='/v1/conversations/done')return{json:{title:'Synthetic completed report',turns:[turn]}};
   if(url.pathname==='/v1/jobs/done')return{json:turn};
  });p.setDefaultTimeout(4000);await p.locator('#sidebar .conversation-row > button').filter({hasText:'Synthetic completed report'}).click();await p.waitForFunction(()=>!loading&&document.querySelector('#conversation-title').textContent==='Synthetic completed report');await p.locator('#prompt').fill('Continue this report');assert(await p.locator('#send').isDisabled());assert.match(await p.locator('#status').innerText(),/permissions.*retry/i);
  fail=false;await p.evaluate(()=>refreshProjectPermissions());assert.equal(await p.locator('#prompt').inputValue(),'Continue this report');assert.equal(await p.locator('#send').isDisabled(),false);await p.close();
  }
 });
 await check('A4-F3 same-name slash chip identities',async()=>{
  const {page:p,state}=await fixture(browser);state.resources=['agent','skill'].map(kind=>({id:'project/review-'+kind,resource_id:'project/review-'+kind,revision:'s1',kind,name:'review',description:'Synthetic '+kind,scope:'project',origin:'agents',selectable:true}));state.failSend=true;
  await p.locator('#prompt').fill('/review');await p.locator('[data-resource-kind=agent][role=option]').click();await p.locator('#prompt').press('End');await p.keyboard.type(' agent task');await p.keyboard.press('Shift+Enter');await p.keyboard.type('/review');await p.locator('[data-resource-kind=skill][role=option]').click();await p.locator('#prompt').press('End');await p.keyboard.type(' skill task');await p.locator('#send').click();await p.waitForFunction(()=>document.querySelector('#status').textContent.includes("Couldn't run"));assert.deepEqual(state.posts.at(-1).resource_selections.map(x=>x.id),['project/review-agent','project/review-skill']);assert.match(state.posts.at(-1).prompt,/agent task.*\n.*skill task/s);await p.close();
 });
 await check('OWNER-F4 sidebar identity',async()=>{
  const {page:p,state}=await fixture(browser);state.conversations=providers.map(backend=>({id:backend,title:backend+' conversation',project:'sem-projeto',state:'completed',execution:{backend,model:'fixture-'+backend}}));await p.evaluate(()=>history());
  for(let i=0;i<providers.length;i++){const row=p.locator('#sidebar .conversation-row').filter({hasText:providers[i]+' conversation'});assert.equal(await row.locator('.conversation-model-icon use').getAttribute('href'),'/assets/icons.svg#'+icons[i]);assert(await row.locator('.conversation-model-icon svg').evaluate(n=>n.getBoundingClientRect().width>0));}await p.close();
 });
 await check('OWNER-F5 log columns remain readable',async()=>{
  for(const [width,height] of [[1440,900],[1280,720],[1024,768],[400,812]])for(const theme of ['porcelain','amethyst']){
   const {page:p}=await fixture(browser,width,height);await p.evaluate(t=>HarnessTheme.apply(t),theme);await p.evaluate(()=>runConsole.openRun('codex'));await p.getByRole('tab',{name:'Logs',exact:true}).click();await p.locator('.run-log-row').first().waitFor();
   const geometry=await p.locator('.run-log-list').evaluate(table=>{const seq=table.querySelectorAll('th')[1],range=document.createRange();range.selectNodeContents(seq);const time=table.querySelector('time'),cell=time.closest('td');return{sequenceWidth:seq.getBoundingClientRect().width,sequenceLines:range.getClientRects().length,timeEnd:time.getBoundingClientRect().right,cellEnd:cell.getBoundingClientRect().right,overflow:document.documentElement.scrollWidth>innerWidth};});assert(geometry.sequenceWidth>=64);assert.equal(geometry.sequenceLines,1);assert(geometry.timeEnd<=geometry.cellEnd);assert.equal(geometry.overflow,false);await p.close();
  }
 });
 await check('OWNER-F3 log table retains keyboard scroll',async()=>{
  const {page:p,state}=await fixture(browser,400,812);await p.evaluate(()=>runConsole.openRun('codex'));await p.getByRole('tab',{name:'Logs',exact:true}).click();await p.locator('.run-log-row').first().waitFor();const viewport=p.getByRole('region',{name:'Log table',exact:true});const top=await p.locator('#run-console-panel').evaluate(n=>n.scrollTop);await viewport.focus();await p.waitForTimeout(50);assert.equal(await p.locator('#run-console-panel').evaluate(n=>n.scrollTop),top);await viewport.evaluate(n=>{n.scrollLeft=150;n.scrollTop=90;});assert.equal(await viewport.evaluate(n=>n.scrollLeft),150);state.count=603;await p.evaluate(()=>runConsole.observe({type:'started',job_id:'codex'}));await p.waitForFunction(()=>document.querySelectorAll('.run-log-row').length===201);assert(await viewport.evaluate(n=>n===document.activeElement));assert.equal(await viewport.evaluate(n=>n.scrollLeft),150);assert.equal(await viewport.evaluate(n=>n.scrollTop),90);await p.close();
 });
 }finally{await browser.close();}if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
})();
