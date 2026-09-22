const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{const browser=await chromium.launch();try{
 let createdProject=null;const origin='http://127.0.0.1:8094';
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.route(origin+'/**',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(p.startsWith('/v1/')){
   let data={};
   if(p==='/v1/projects'&&route.request().method()==='POST'){createdProject=route.request().postDataJSON();return route.fulfill({json:{project_id:'novo'}});}
   if(p==='/v1/projects')data={projects:createdProject?['sem-projeto','novo']:['sem-projeto'],details:createdProject?{novo:{label:createdProject.name}}:{}};
   if(p==='/v1/project-directories')data={roots:[{id:'home',label:'Pastas locais'}],root_id:'home',path:'',absolute_path:'/home/test-user',entries:url.searchParams.get('path')?[{name:'Subpasta',path:'Trabalho A/Subpasta',absolute_path:'/home/test-user/Trabalho A/Subpasta',type:'directory'},{name:'oculto.txt',path:'oculto.txt',type:'file'}]:['Trabalho A','Trabalho B'].map(name=>({name,path:name,absolute_path:'/home/test-user/'+name,type:'directory'})),limited:false};
   if(p==='/v1/models')data={models:[{id:'qwen-local',backend:'local',efforts:['configured']}]};
   if(p==='/v1/conversations')data={conversations:[]};
   if(p==='/v1/version')data={version:'test',build:'project-dialog-test'};
   if(p==='/v1/catalog')data={agents:[],skills:[],warnings:[]};
   return route.fulfill({json:data});
  }
  const file=p==='/'?'index.html':p.slice(1);return route.fulfill({body:await fs.readFile(path.join(__dirname,file.startsWith('assets/')?'../tail_ui':'../agent_service',file)),contentType:file.endsWith('.svg')?'image/svg+xml':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
 await page.goto(origin);await page.locator('#startup-gate').waitFor({state:'hidden'});
 await page.click('#add-project');
 assert.equal(await page.locator('#project-dialog-title').innerText(),'Criar projeto');
 assert.equal(await page.locator('#project-folder-browser').isVisible(),true);
 assert.equal(await page.locator('#project-selected-paths').isVisible(),false);
 assert(await page.locator('#project-name').evaluate(e=>e===document.activeElement));
 await page.evaluate(()=>TailTheme.apply('amethyst',false));
 await page.screenshot({path:'/tmp/project-create-desktop.png'});
 await page.fill('#project-name','Meu projeto');await page.click('#project-create');
 assert.match(await page.locator('#project-create-note').innerText(),/pelo menos uma pasta/);
 
 await page.locator('#project-directory-list .project-file-row').filter({hasText:'Trabalho A'}).click();assert.equal(await page.locator('#project-selected-paths li').count(),0);await page.click('#project-directory-add-current');
 await page.locator('#project-directory-list .project-file-row').filter({hasText:'Trabalho B'}).click();await page.click('#project-directory-add-current');
 await page.getByRole('button',{name:'Expandir Trabalho A',exact:true}).click();await page.locator('#project-directory-list').getByText('Subpasta',{exact:true}).waitFor();assert.equal(await page.locator('#project-directory-list').getByText('oculto.txt').count(),0);await page.getByRole('button',{name:'Recolher Trabalho A',exact:true}).click();assert.equal(await page.locator('#project-directory-list').getByText('Subpasta',{exact:true}).count(),0);assert.equal(await page.locator('#project-folder-browser').isVisible(),true);
 assert.equal(await page.locator('#project-selected-paths li').count(),2);
 await page.getByRole('button',{name:'Remover pasta Trabalho A',exact:true}).click();
 assert.equal(await page.locator('#project-selected-paths li').count(),1);
 await page.click('#project-dialog-cancel');assert.equal(await page.locator('#project-dialog').isVisible(),false);
 await page.click('#add-project');assert.equal(await page.locator('#project-name').inputValue(),'');
 assert.equal(await page.locator('#project-selected-paths li').count(),0);
 await page.setViewportSize({width:390,height:844});
 for(const palette of ['amethyst','porcelain']){
  await page.evaluate(p=>TailTheme.apply(p,false),palette);
  assert(await page.locator('#project-dialog').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&e.scrollWidth<=e.clientWidth;}));
  await page.screenshot({path:'/tmp/project-create-mobile-'+palette+'.png'});
 }
 await page.fill('#project-name','Novo projeto');
 await page.locator('#project-directory-list .project-file-row').filter({hasText:'Trabalho A'}).click();assert.equal(await page.locator('#project-selected-paths li').count(),0);await page.click('#project-directory-add-current');
 await page.locator('#project-directory-list .project-file-row').filter({hasText:'Trabalho B'}).click();await page.click('#project-directory-add-current');
 assert(await page.locator('#project-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth));
 await page.screenshot({path:'/tmp/project-create-browser-mobile.png'});
 await page.click('#project-create');
 await page.locator('#project-dialog').waitFor({state:'hidden'});
 assert.deepEqual(createdProject,{name:'Novo projeto',paths:['/home/test-user/Trabalho A','/home/test-user/Trabalho B']});
 assert.deepEqual(errors,[]);console.log('PASS: compact project modal, accessible selection, removal, cancel/reset, validation, create payload, mobile and themes');
}finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
