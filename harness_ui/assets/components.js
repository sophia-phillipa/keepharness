(()=>{
 const theme=window.HarnessTheme;
 function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('th-icon');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('focusable','false');svg.setAttribute('aria-hidden','true');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','/assets/icons.svg#'+name);svg.append(use);return svg;}
 function decorate(container=document){
  container.querySelectorAll('button.button').forEach(b=>{b.classList.add('btn');b.classList.toggle('btn-primary',b.classList.contains('primary'));});
  container.querySelectorAll('input:not([type=checkbox]):not([type=hidden]),textarea').forEach(e=>e.classList.add('form-control'));
  container.querySelectorAll('select').forEach(e=>e.classList.add('form-select'));
 }
 // Each target owns its timer; a previous success cannot dismiss a later error.
 const notices=new WeakMap();
 function notice(target,text,{error=false,persistent=false}={}){
  const old=notices.get(target);if(old)clearTimeout(old.timer);
  target.replaceChildren();target.hidden=false;target.className='th-notice'+(error?' error':'');target.setAttribute('role',error?'alert':'status');target.setAttribute('aria-live',error?'assertive':'polite');
  const message=document.createElement('span');message.className='notice-text';message.textContent=text;
  const close=document.createElement('button');close.type='button';close.setAttribute('aria-label','Close message');close.append(icon('x'));
  const entry={timer:null,remaining:5000,started:0};notices.set(target,entry);
  const hide=()=>{clearTimeout(entry.timer);target.hidden=true;};close.onclick=hide;target.append(message,close);
  const pause=()=>{if(entry.timer){clearTimeout(entry.timer);entry.timer=null;entry.remaining=Math.max(0,entry.remaining-(performance.now()-entry.started));}};
  const resume=()=>{if(error||persistent||target.hidden||target.matches(':hover')||target.contains(document.activeElement))return;entry.started=performance.now();entry.timer=setTimeout(hide,entry.remaining);};
  target.onmouseenter=pause;target.onmouseleave=resume;target.onfocusin=pause;target.onfocusout=()=>setTimeout(resume,0);resume();
 }
 function toast(text,options){let box=document.getElementById('th-toast');if(!box){const container=document.createElement('div');container.className='th-toast-container';box=document.createElement('div');box.id='th-toast';container.append(box);document.body.append(container);}notice(box,text,options);}
 function mountThemes(container){const grid=document.createElement('div');grid.className='theme-picker';grid.setAttribute('role','group');grid.setAttribute('aria-label','Theme for this interface');
  for(const t of theme.themes){const b=document.createElement('button');b.type='button';b.className='theme-choice';b.dataset.themeChoice=t.id;const swatches=document.createElement('span');swatches.className='theme-swatches';swatches.setAttribute('aria-hidden','true');for(const color of t.colors){const s=document.createElement('span');s.style.backgroundColor=color;swatches.append(s);}const title=document.createElement('strong');title.className='theme-name';title.textContent=t.name;const mode=document.createElement('small');mode.textContent=t.mode==='dark'?'Dark':'Light';b.append(swatches,title,mode);b.onclick=()=>theme.apply(t.id);grid.append(b);}container.append(grid);theme.apply(document.documentElement.dataset.palette,false);
 }
 // Picker policy only: keep legacy execution IDs intact for existing sessions.
 function selectableModel(provider,id){return provider!=='claude'||/^claude-[a-z]+-\d{1,3}(?:-\d{1,3})?$/.test(id);}
 // D42: one display name per provider and coordinator, shared by the app and the admin.
 const providerNames={codex:'Codex',claude:'Claude Code',deepseek:'DeepSeek',local:'Local models',maestro:'Maestro',gemini:'Gemini CLI'};
 function providerName(id,fallback){return providerNames[id]||fallback||id;}
 window.HarnessUI={icon,decorate,notice,toast,mountThemes,selectableModel,providerName,providerNames};
 document.addEventListener('DOMContentLoaded',()=>{decorate();document.querySelectorAll('[data-theme-picker]').forEach(mountThemes);});
})();
