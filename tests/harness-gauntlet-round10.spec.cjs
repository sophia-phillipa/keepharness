// Round-ten regressions with synthetic HTTP fixtures and real Chromium layout/hit testing.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { mount, run, span } = require('./run-console-fixture.cjs');
const resource = { id:'project/p/reviewer', resource_id:'project/p/reviewer', revision:'1', name:'reviewer', kind:'agent', scope:'project', origin:'codex', selectable:true,
  description:'Review implementation changes for correctness, keyboard accessibility, mobile layout and recovery from interrupted network connections. Return concrete findings with evidence and reproduction steps. Check every available action with a keyboard and verify that focus remains visible when the viewport is resized or the connection is restored.',
  source:'/synthetic/project/.codex/agents/reviewer.toml', preflight_hint:'Ready to delegate using the selected model and configured permissions.' };
async function fixture(page, initial = {}) {
  page.setDefaultTimeout(6000);
  const state = { quotas:false, queued:false, ...initial };
  const job = () => ({id:'run-a', project:'sem-projeto', state:state.queued?'queued':'running', request:{prompt:'Synthetic review',backend:'local',model:'fixture',execution_mode:'native'},result:null,gates:[]});
  await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>{window.round10Statuses=[];new MutationObserver(records=>{for(const record of records)for(const node of record.addedNodes)window.round10Statuses.push(node.textContent);}).observe(document.querySelector('#status'),{childList:true});}));
  await mount(page, async (url, request) => {
    if(url.pathname==='/v1/resources') return {json:{items:[resource],warnings:[]}};
    if(url.pathname==='/v1/conversations') return {json:{conversations:[{id:'conversation-a',last_job_id:'run-a',project:'sem-projeto',title:'Synthetic review',state:state.queued?'queued':'running'}]}};
    if(url.pathname==='/v1/conversations/conversation-a') return {json:{title:'Synthetic review',turns:[job()]}};
    if(url.pathname==='/v1/jobs/run-a') return {json:job()};
    if(url.pathname==='/v1/activity') return {json:{counts:{running:1},jobs:[{...run,state:state.queued?'queued':'running',wait_reason:state.queued?'provider_capacity':null}],needs_you:[],providers:state.quotas?[{backend:'codex',quota:{available:true,rateLimits:{primary:{usedPercent:25,windowDurationMins:300}}}}]:[]}};
    if(url.pathname.endsWith('/spans')) return {json:{spans:[span]}};
    if(url.pathname.endsWith('/events')) return {body:state.queued?'id: 1\nevent: queue_wait\ndata: {"id":1,"type":"queue_wait","data":{"reason":"provider_capacity"}}\n\n':'',contentType:'text/event-stream'};
    if(request.method()==='POST') return {status:422,json:{code:'synthetic_stop'}};
  });
  async function open() {
    if(await page.locator('#menu').isVisible() && await page.evaluate(()=>innerWidth<=620)) await page.locator('#menu').click();
    await page.locator('#history .conversation-row > button').filter({hasText:'Synthetic review'}).click();
    await page.waitForFunction(()=>!loading && document.querySelector('#conversation-title').textContent==='Synthetic review');
  }
  return {state,open};
}
async function capture(page,name) {
  if(!process.env.EVAL_OUTPUT)return;
  const folder=path.join(process.env.EVAL_OUTPUT,'round10');await fs.mkdir(folder,{recursive:true});
  const cdp=await page.context().newCDPSession(page);
  const shot=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  await fs.writeFile(path.join(folder,name+'.png'),Buffer.from(shot.data,'base64'));await cdp.detach();
}
async function geometry(page) {
  return page.locator('#resource-option-0').evaluate(node=>{
    const r=node.getBoundingClientRect(),p=node.closest('.resource-options').getBoundingClientRect();
    const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
    return {height:Math.min(r.bottom,p.bottom,innerHeight)-Math.max(r.top,p.top,0),hit:node===hit||node.contains(hit),focused:document.activeElement===node};
  });
}
(async()=>{
  const browser=await chromium.launch(), failures=[];
  async function check(name,fn){if(process.env.ONLY&&!name.startsWith(process.env.ONLY))return;try{await fn();console.log('PASS '+name);}catch(e){failures.push(name+': '+e.stack);console.error('FAIL '+name+': '+e.message);}}
  try {
    await check('A1-F1 painted Attention glyph across themes and widths',async()=>{
      for(const width of [400,1440])for(const theme of ['porcelain','amethyst','petroleum']){
        const p=await browser.newPage({viewport:{width,height:812}});await fixture(p);await p.evaluate(t=>TailTheme.apply(t),theme);
        const glyph=await p.locator('#attention-bell > span').evaluate(n=>{const r=n.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return{width:r.width,height:r.height,clip:getComputedStyle(n).clip,hit:hit===n||n.contains(hit)}});
        await capture(p,`bell-${width}-${theme}`);assert(glyph.width>=12&&glyph.height>=12&&glyph.clip==='auto'&&glyph.hit,JSON.stringify(glyph));
        await p.locator('#attention-bell').click();assert(await p.locator('#attention-popover').isVisible());await p.close();
      }
    });
    await check('A1-F2 Attention popup tracks its trigger and viewport',async()=>{
      for(const width of [400,1024,1280,1440])for(const quotas of [false,true]){
        const p=await browser.newPage({viewport:{width,height:812}});await fixture(p,{quotas});
        if(quotas&&width>700)await p.locator('.provider-quota-meter').waitFor({state:'attached'});
        await p.locator('#attention-bell').click();const bell=await p.locator('#attention-bell').boundingBox(),pop=await p.locator('#attention-popover').boundingBox();
        assert(pop.x>=0&&pop.x+pop.width<=width);assert(pop.x<=bell.x+bell.width&&pop.x+pop.width>=bell.x,JSON.stringify({width,quotas,bell,pop}));
        const target=p.locator('#attention-open-inbox');assert(await target.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}));
        await capture(p,`attention-${width}-${quotas}`);await p.close();
      }
    });
    await check('A2-F1 editor exposes popup selection and empty status',async()=>{
      for(const width of [400,1440]){
        const p=await browser.newPage({viewport:{width,height:812}});await fixture(p);const editor=p.locator('#prompt');
        assert.equal(await editor.getAttribute('aria-expanded'),'false');await editor.fill('/reviewer');await p.locator('#resource-option-0').waitFor();
        assert.equal(await editor.getAttribute('aria-expanded'),'true');assert.equal(await editor.getAttribute('aria-activedescendant'),'resource-option-0');
        const cdp=await p.context().newCDPSession(p),ax=await cdp.send('Accessibility.getFullAXTree');
        const combo=ax.nodes.find(n=>n.name?.value==='Message'&&n.role?.value==='combobox');assert(combo);assert(combo.properties.some(v=>v.name==='expanded'&&v.value.value===true));await cdp.detach();
        await editor.press('Tab');assert.equal(await p.locator('.resource-chip').count(),1);assert.equal(await editor.getAttribute('aria-expanded'),'false');assert.equal(await editor.getAttribute('aria-activedescendant'),null);
        await editor.fill('/zzzzzznonexistent');await p.locator('.resource-empty').filter({hasText:'No resource'}).waitFor();
        assert.equal(await p.locator('#resource-status').getAttribute('role'),'status');assert.match(await p.locator('#resource-status').textContent(),/No resource/);assert.equal(await editor.getAttribute('aria-activedescendant'),null);
        await editor.press('Escape');assert.equal(await editor.getAttribute('aria-expanded'),'false');await p.close();
      }
    });
    await check('A2-F2 polling recovery restores Settings opener and console focus',async()=>{
      for(const width of [400,1440])for(const surface of ['settings','console']){
        const p=await browser.newPage({viewport:{width,height:812}});const f=await fixture(p);await p.locator('#prompt').fill('Preserved draft\nUnicode ✨');
        if(surface==='settings'){await p.locator('#settings').focus();await p.keyboard.press('Enter');await p.locator('#settings-tour').focus();}
        else {await f.open();await p.keyboard.press('Control+j');await p.locator('#run-tab-logs').click();}
        let outages=0;await p.route('**/v1/projects',route=>{outages++;return route.abort('connectionreset')});
        await p.locator('#startup-gate').waitFor({state:'visible',timeout:15000});await new Promise((resolve,reject)=>{const end=Date.now()+20000;const poll=setInterval(()=>{if(outages>=2){clearInterval(poll);resolve();}else if(Date.now()>end){clearInterval(poll);reject(Error('Second readiness failure did not occur'));}},100)});await p.unroute('**/v1/projects');await p.locator('#startup-gate').waitFor({state:'hidden',timeout:15000});
        await p.waitForTimeout(300);const active=await p.evaluate(()=>({id:document.activeElement.id,visible:document.activeElement.checkVisibility(),inert:!!document.activeElement.closest('[inert]')}));
        assert.equal(active.id,surface==='settings'?'settings':'run-tab-logs',JSON.stringify(active));assert(active.visible&&!active.inert);
        if(surface==='settings')assert.equal(await p.locator('#prompt').inputValue(),'Preserved draft\nUnicode ✨');await capture(p,`recovery-${width}-${surface}`);await p.close();
      }
    });
    await check('A2-F3 native zoom retains pointer and keyboard option targets',async()=>{
      const root=await fs.mkdtemp(path.join(os.tmpdir(),'tail-round10-zoom-')),extension=path.join(root,'extension');await fs.mkdir(extension);
      await fs.writeFile(path.join(extension,'manifest.json'),JSON.stringify({manifest_version:3,name:'Synthetic native zoom',version:'1.0',permissions:['tabs'],host_permissions:['http://console.test/*'],background:{service_worker:'background.js'}}));
      await fs.writeFile(path.join(extension,'background.js'),'chrome.runtime.onInstalled.addListener(()=>{});');
      const context=await chromium.launchPersistentContext(path.join(root,'profile'),{viewport:null,channel:'chromium',args:['--window-size=1440,900','--disable-extensions-except='+extension,'--load-extension='+extension]});
      try{
        const p=context.pages()[0],f=await fixture(p);await f.open();const sw=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');
        for(const zoom of [1,2]){
          await sw.evaluate(async zoom=>{const [tab]=await chrome.tabs.query({active:true,currentWindow:true});await chrome.tabs.setZoom(tab.id,zoom);},zoom);await p.waitForFunction(zoom=>devicePixelRatio===zoom,zoom);
          if(await p.locator('#prompt').evaluate(n=>!!n.closest('[inert]')))await p.keyboard.press('Escape');
          for(const selection of ['keyboard','pointer']){
            await p.locator('#prompt').fill('/reviewer');await p.locator('#resource-option-0').waitFor();await p.keyboard.press('ArrowDown');const g=await geometry(p);await capture(p,`palette-zoom-${zoom}-${selection}`);
            assert(g.height>=24&&g.hit&&g.focused,JSON.stringify({zoom,selection,g}));
            if(selection==='keyboard')await p.keyboard.press('Tab');else await p.locator('#resource-option-0').click();
            assert.equal(await p.locator('.resource-chip').count(),1);await p.locator('#prompt').fill('');
          }
        }
      }finally{await context.close();await fs.rm(root,{recursive:true,force:true});}
    });
    await check('A5-F1 queued conversation uses readable scheduling labels',async()=>{
      const p=await browser.newPage({viewport:{width:1440,height:900}}),f=await fixture(p,{queued:true});await f.open();
      await p.waitForFunction(()=>window.round10Statuses.some(text=>/queue_wait|Waiting/.test(text)));
      const statuses=await p.evaluate(()=>window.round10Statuses);assert(statuses.includes('Waiting for another task on this provider'),JSON.stringify(statuses));assert(!statuses.includes('queue_wait')); assert.match(await p.locator('#history').innerText(),/Waiting for another task on this provider/);
      await p.keyboard.press('Control+j');await p.locator('#run-tab-runs').click();assert.match(await p.locator('#run-console').innerText(),/Waiting for another task on this provider/);
      const labels=await p.evaluate(()=>['human_approval','conversation_parent','conversation','work_item','writable_root','Already readable'].map(waitReasonLabel));assert.deepEqual(labels,['Waiting for your approval','Waiting for the previous response','Waiting for this conversation','Waiting for this work item','Waiting for access to project files','Already readable']);await p.close();
    });
  }finally{await browser.close();}
  if(failures.length){console.error(failures.join('\n'));process.exitCode=1;}
})();
