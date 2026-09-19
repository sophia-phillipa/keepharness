const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const b=await chromium.launch();
 try{
  const p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[];
  p.on('pageerror',e=>errors.push(e.message));
  await p.route('**/v1/models',r=>r.fulfill({json:{models:[{id:'qwen-local',name:'Qwen local',backend:'local',efforts:['low']}],providers:{local:{}}}}));
  await p.route('**/v1/conversations',r=>r.fulfill({json:{conversations:[]}}));
  await p.route('**/v1/jobs',r=>r.abort()); // Layout tests must never start inference.
  await p.goto(process.env.HARNESS_URL||'http://127.0.0.1:8095/');
  await p.fill('#prompt','Rascunho deve sobreviver aos temas');
  for(const theme of ['violet-bordeaux','porcelain','mineral-rose','amethyst','petroleum','arizona']){
   await p.setViewportSize({width:1440,height:1000});
   await p.click('#settings');
   await p.locator('[data-theme-choice='+theme+']').click();
   await p.click('#settings-close');
   assert.equal(await p.locator('html').getAttribute('data-palette'),theme);
   for(const width of [390,768,1440]){
    await p.setViewportSize({width,height:1000});
    if(await p.locator('#activity-panel').isVisible())await p.click('#activity-close');
    const data=await p.evaluate(()=>{
     const box=id=>document.getElementById(id).getBoundingClientRect();
     const centers=['menu','attach','send'].map(id=>{
      const e=document.getElementById(id),a=e.getBoundingClientRect(),s=e.querySelector('svg').getBoundingClientRect();
      return{id,dx:Math.abs(s.x+s.width/2-a.x-a.width/2),dy:Math.abs(s.y+s.height/2-a.y-a.height/2)};
     });
     const a=box('attach'),m=box('model'),e=box('effort'),s=box('send');
     return{overflow:document.documentElement.scrollWidth>innerWidth,centers,project:box('project').width,attachGap:m.left-a.right,sendGap:Math.max(s.left-e.right,e.left-s.right,s.top-e.bottom,e.top-s.bottom)};
    });
    assert(!data.overflow,theme+' overflow at '+width);
    for(const x of data.centers)assert(x.dx<=1&&x.dy<=1,theme+' '+width+' '+JSON.stringify(x));
    assert(data.attachGap>=12,theme+' attachment gap '+data.attachGap);
    assert(data.sendGap>=12,theme+' send gap '+data.sendGap);
    if(width===390){
     assert(data.project>=180,'Project label must remain readable on mobile');
     assert(await p.locator('#activity-toggle').evaluate(e=>e.scrollWidth<=e.clientWidth),'Activity label must fit its button');
    }
    assert.equal(await p.locator('#prompt').inputValue(),'Rascunho deve sobreviver aos temas');
   }
  }
  await p.setViewportSize({width:1440,height:1000});
  if(await p.locator('#activity-panel').isVisible())await p.click('#activity-close');
  await p.evaluate(()=>document.documentElement.style.zoom='2');
  const zoom=await p.evaluate(()=>{
   const boxes=['attach','model','effort','send'].map(id=>document.getElementById(id).getBoundingClientRect());
   const gaps=[];for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++){
    const a=boxes[i],b=boxes[j];gaps.push(Math.max(a.left-b.right,b.left-a.right,a.top-b.bottom,b.top-a.bottom));
   }
   return{overflow:document.documentElement.scrollWidth>innerWidth,gaps};
  });
  assert(!zoom.overflow,'CSS 200% horizontal overflow');
  assert(zoom.gaps.every(gap=>gap>=24),'CSS 200% controls need at least 12 CSS px separation: '+zoom.gaps);
  await p.evaluate(()=>document.documentElement.style.zoom='1');
  await p.setViewportSize({width:390,height:844});
  await p.keyboard.press('Control+k');
  assert.equal(await p.evaluate(()=>document.activeElement.id),'conversation-search');
  await p.keyboard.press('Escape');
  assert.equal(await p.evaluate(()=>document.activeElement.id),'menu');
  assert.deepEqual(errors,[]);
  console.log('PASS: six themes, centered toolbar icons, control gaps, readable mobile project, responsive layout, draft and keyboard');
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
