const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{const browser=await chromium.launch();try{
 const p=await browser.newPage();let uploads=0,jobs=0,audioRoute;const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/v1/**',async r=>{const path=new URL(r.request().url()).pathname;let data={};
 if(path==='/v1/projects')data={projects:['sem-projeto','demo'],details:{}};
 if(path==='/v1/models')data={uploads_enabled:true,providers:{local:true},models:[{id:'qwen-local',backend:'local',efforts:['configured'],permissions:{upload:true,internet:true,shell:true}},{id:'gemma-local',backend:'local',efforts:['configured'],permissions:{upload:false,internet:false,shell:false}}]};
 if(path==='/v1/conversations')data={conversations:[]};
 if(path==='/v1/files'){uploads++;if(r.request().headers()['x-filename']==='speech.wav'){audioRoute=r;return;}data={file_id:'fixture-file'};}
 if(path==='/v1/jobs'){jobs++;throw Error('Must not execute inference in permission test');}
 if(path==='/v1/models'&&new URL(r.request().url()).searchParams.get('project_id')==='demo')data.models=data.models.map(m=>({...m,permissions:{...m.permissions,upload:true,internet:true}}));
 await r.fulfill({json:data});});
 await p.goto(process.env.HARNESS_URL||'http://127.0.0.1:18196/');await p.waitForFunction(()=>document.querySelector('#models-retry').textContent==='Verificar novamente');
 assert(await p.locator('#attach').isEnabled());assert.match(await p.locator('#model-permissions').innerText(),/Internet permitida/);
 await p.locator('#file').setInputFiles({name:'teste.txt',mimeType:'text/plain',buffer:Buffer.from('Arquivo de teste sem dados pessoais')});await p.waitForFunction(()=>document.querySelector('#attachments').textContent.includes('teste.txt'));assert.equal(uploads,1);
 await p.clock.install();
 await p.locator('#file').setInputFiles({name:'speech.wav',mimeType:'audio/wav',buffer:Buffer.from('fixture audio')});
 await p.waitForFunction(()=>document.querySelector('#status').textContent.includes('speech.wav'));
 while(!audioRoute)await new Promise(resolve=>setTimeout(resolve,10));
 await p.clock.fastForward(31000);
 assert.match(await p.locator('#status').innerText(),/speech.wav/);
 await audioRoute.fulfill({json:{file_id:'audio-fixture'}});
 await p.waitForFunction(()=>document.querySelector('#attachments').textContent.includes('speech.wav'));
 assert.equal(uploads,2);

 await p.selectOption('#model','gemma-local');assert(await p.locator('#attach').isDisabled());assert.match(await p.locator('#model-permissions').innerText(),/Internet desativada/);
 await p.fill('#prompt','Analisar anexo');await p.click('#send');assert.match(await p.locator('#status').innerText(),/não permite anexos/);assert.equal(jobs,0);assert.equal(await p.locator('#prompt').inputValue(),'Analisar anexo');
 await p.selectOption('#model','qwen-local');assert(await p.locator('#attach').isEnabled());assert.match(await p.locator('#attachments').innerText(),/teste.txt/);await p.selectOption('#model','gemma-local');await p.selectOption('#project','demo');await p.waitForFunction(()=>!document.querySelector('#attach').disabled);assert.match(await p.locator('#model-permissions').innerText(),/Internet permitida/);await p.selectOption('#project','sem-projeto');await p.waitForFunction(()=>document.querySelector('#attach').disabled);assert.match(await p.locator('#model-permissions').innerText(),/Internet desativada/);assert.deepEqual(errors,[]);
 console.log('PASS: Qwen attachments and internet permissions, isolated Gemma restrictions, draft and attachment retained on model switch');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
