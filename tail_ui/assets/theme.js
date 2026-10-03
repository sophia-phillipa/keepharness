/* Blocking head script: choose this surface's theme before the first paint. */
(()=>{
 const root=document.documentElement,surface=root.dataset.surface==='admin'?'admin':'harness';
 const themes=[
  {id:'paper',name:'Paper',mode:'light',colors:['#ffffff','#1a1a1a','#2f6fde']},
  {id:'graphite',name:'Graphite',mode:'dark',colors:['#141414','#e3e3e3','#9cc1ff']},
  {id:'violet-bordeaux',name:'Violet & Bordeaux',mode:'light',colors:['#ffffff','#643b92','#792f49']},
  {id:'porcelain',name:'Porcelain',mode:'light',colors:['#ffffff','#176b78','#244f69']},
  {id:'mineral-rose',name:'Mineral Rose',mode:'light',colors:['#ffffff','#873c62','#684253']},
  {id:'amethyst',name:'Amethyst',mode:'dark',colors:['#17131e','#c0a0ef','#eaa5c1']},
  {id:'petroleum',name:'Petroleum',mode:'dark',colors:['#111c22','#6bd4c9','#f0bc68']},
  {id:'arizona',name:'Arizona',mode:'dark',colors:['#232323','#fc4c02','#eeeeee']}
 ];
 const key='tail-harness:theme:'+surface;
 const defaultLight="paper";
 const defaultDark="graphite";
 let initial=defaultLight;
 try{
  const saved=localStorage.getItem(key);
  if(saved)initial=saved;
  else if(matchMedia('(prefers-color-scheme: dark)').matches)initial=defaultDark;
 }catch{}
 function apply(id,persist=true){
  const t=themes.find(t=>t.id===id)||themes[0];
  root.dataset.palette=t.id;root.dataset.bsTheme=t.mode;root.dataset.theme=t.mode;root.style.colorScheme=t.mode;
  if(persist)try{localStorage.setItem(key,t.id);}catch{}
  document.querySelectorAll('[data-theme-choice]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.themeChoice===t.id)));
  const select=document.getElementById('theme-select');if(select)select.value=t.id;
 }
 window.TailTheme={themes,apply,key,surface};apply(initial,false);
 addEventListener('storage',e=>{if(e.key===key)apply(e.newValue,false);});
})();
