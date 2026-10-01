/* Owner-only provisioning UI. Credential values are never retained after submission. */
(() => {
  const byId = id => document.getElementById(id);
  let catalogState, vaultState, preview = null, busy = false;
  const text = (tag, value) => { const node=document.createElement(tag); node.textContent=value; return node; };
  const selected = () => ({project_id:byId('catalog-project').value,catalog_id:byId('catalog-source').value});
  function message(value) { byId('catalog-message').textContent=value; }
  function clearPreview() { preview=null; byId('catalog-move').disabled=true; byId('catalog-diff').replaceChildren(); }
  function options(select, values, current) {
    select.replaceChildren(...values.map(([value,label]) => {const option=text('option',label);option.value=value;return option;}));
    if (values.some(([value])=>value===current)) select.value=current;
  }
  function declaredContract() {
    const selection=selected(), name=byId('vault-integration').value.trim();
    const check=catalogState?.preflight.find(item=>item.project_id===selection.project_id && item.catalog_id===selection.catalog_id);
    return check?.integrations?.find(item=>item.integration===name) || vaultState?.contracts?.find(item=>item.integration===name);
  }
  function credentialFields() {
    const contract=declaredContract();
    for(const id of ['vault-mode','vault-consumer','vault-variable','vault-endpoint'])byId(id).disabled=!!contract;
    if(contract) {
      byId('vault-mode').value=contract.mediated?'mediated':'environment';
      byId('vault-consumer').value=contract.consumers[0];
      byId('vault-consumer').title='Declared consumers: '+contract.consumers.join(', ');
      byId('vault-variable').value=Object.keys(contract.environment).join(', ');
      byId('vault-endpoint').value=contract.endpoint||'';
    }
    byId('vault-mediated').hidden=byId('vault-mode').value!=='mediated';
    const fields=contract && !contract.mediated ? [...new Set(Object.values(contract.environment))] : ['token'];
    byId('vault-fields').replaceChildren(...fields.flatMap((field,index)=>{
      const id=index===0?'vault-secret':'vault-secret-'+index, label=text('label','Secret value · '+field), input=document.createElement('input');
      label.htmlFor=id;input.id=id;input.type='password';input.required=true;input.autocomplete='new-password';input.dataset.credentialField=field;
      return [label,input];
    }));
  }
  function renderSelection() {
    clearPreview();
    const project=catalogState.projects.find(item=>item.id===byId('catalog-project').value);
    options(byId('catalog-source'),catalogState.catalogs.filter(item=>project?.catalogs?.includes(item.id)).map(item=>[item.id,item.id]),byId('catalog-source').value);
    const selection=selected(), pin=project?.catalog_pins?.[selection.catalog_id];
    byId('catalog-current').textContent=pin ? 'Pinned revision: '+pin.commit : 'This catalog follows its source folder.';
    byId('catalog-pin').disabled=!!pin || !selection.catalog_id;
    byId('catalog-preview').disabled=!pin;
    const check=catalogState.preflight.find(item=>item.project_id===selection.project_id && item.catalog_id===selection.catalog_id);
    byId('catalog-preflight').replaceChildren(...(check?.preflight?.length ? check.preflight : [check?.manifest?'Provisioning checks passed.':'No catalog manifest. Existing maintenance commands remain available.']).map(value=>text('p',value)));
    byId('catalog-provision').disabled=!check?.manifest;
    credentialFields();
  }
  function renderCatalogs(value) {
    catalogState=value;
    options(byId('catalog-project'),value.projects.map(item=>[item.id,item.label||item.id]),byId('catalog-project').value);
    renderSelection();
    byId('catalog-drift').replaceChildren(...(value.drift.length ? value.drift.map(item=>{const row=document.createElement('div');row.append(text('strong',item.resource_id));for(const location of item.locations)row.append(text('p',location+' · revision '+(item.revisions[location].revision||'unknown')+(item.revisions[location].catalog_commit?' · commit '+item.revisions[location].catalog_commit:'')));return row;}) : [text('p','No resource revision drift detected.')]));
  }
  function renderVault(value) {
    vaultState=value;
    byId('vault-status').replaceChildren(...(value.credentials.length?value.credentials.map(item=>{
      const row=text('p',item.binding+' · configured fields: '+item.fields.join(', ')+' ');
      const remove=text('button','Remove binding');remove.type='button';remove.className='button secondary';
      remove.onclick=()=>operate(async()=>{renderVault(await request('vault',{action:'delete',binding:item.binding}));message('Credential binding removed.');});
      row.append(remove);return row;
    }):[text('p','No credentials stored.')]));
  }
  async function operate(task) {
    if (busy) return;
    const focused=document.activeElement;
    busy=true;byId('catalog-panel').setAttribute('aria-busy','true');
    const buttons=[...byId('catalog-panel').querySelectorAll('button, input, select')], states=buttons.map(button=>button.disabled);
    buttons.forEach(button=>button.disabled=true);
    try { await task(); } catch(error) { message(error.message); }
    finally {
      busy=false;byId('catalog-panel').removeAttribute('aria-busy');
      buttons.forEach((button,index)=>{if(button.isConnected)button.disabled=states[index];});
      const project=catalogState?.projects.find(item=>item.id===selected().project_id);
      byId('catalog-pin').disabled=!!project?.catalog_pins?.[selected().catalog_id] || !selected().catalog_id;
      byId('catalog-preview').disabled=!project?.catalog_pins?.[selected().catalog_id];
      byId('catalog-move').disabled=!preview;
      const check=catalogState?.preflight.find(item=>item.project_id===selected().project_id && item.catalog_id===selected().catalog_id);
      byId('catalog-provision').disabled=!check?.manifest;
      if(focused?.isConnected && byId('catalog-panel').contains(focused)) {
        const target=focused.disabled?byId('catalog-message'):focused;
        if(target===byId('catalog-message'))target.tabIndex=-1;
        target.focus({preventScroll:true});
      }
    }
  }
  async function load() {
    if(location.hash!=='#catalogs')return;
    await operate(async()=>{
      const [catalogs,vault]=await Promise.all([request('catalogs'),request('vault')]);
      renderCatalogs(catalogs);renderVault(vault);
    });
  }
  byId('catalog-project').onchange=renderSelection;
  byId('catalog-source').onchange=renderSelection;
  byId('catalog-ref').oninput=clearPreview;
  byId('catalog-refresh').onclick=load;
  for(const [id,action] of [['catalog-pin','pin'],['catalog-provision','provision']]) {
    byId(id).onclick=()=>operate(async()=>{
      renderCatalogs(await request('catalog-pin',{...selected(),action,ref:byId('catalog-ref').value.trim()}));
      message(action==='pin'?'Catalog pinned.':'Catalog provisioning completed.');
    });
  }
  byId('catalog-preview').onclick=()=>operate(async()=>{
    clearPreview();
    preview=await request('catalog-pin',{...selected(),action:'preview',ref:byId('catalog-ref').value.trim()});
    byId('catalog-diff').replaceChildren(text('p','Proposed revision: '+preview.pin.commit),...(preview.diff.length?preview.diff.map(item=>text('p',item.resource_id+' · '+(item.before||'new')+' → '+(item.after||'removed'))):[text('p','No resource revisions changed.')]));
    message('Review the revisions, then choose Move pin to apply this update.');
  });
  byId('catalog-move').onclick=()=>operate(async()=>{
    if(!preview)return;
    renderCatalogs(await request('catalog-pin',{...selected(),action:'update',preview_token:preview.preview_token}));
    message('Catalog pin moved to the reviewed revision.');
  });
  byId('vault-integration').oninput=credentialFields;
  byId('vault-mode').onchange=()=>{byId('vault-mediated').hidden=byId('vault-mode').value!=='mediated';};
  byId('vault-form').onsubmit=event=>{
    event.preventDefault();
    operate(async()=>{
      const integration=byId('vault-integration').value.trim(), binding=byId('vault-binding').value.trim();
      const mediated=byId('vault-mode').value==='mediated', endpoint=byId('vault-endpoint').value.trim();
      const existing=declaredContract();
      const contract={integration,consumers:[byId('vault-consumer').value],environment:mediated?{}:{[byId('vault-variable').value.trim()]:'token'},precedence:'vault',mediated};
      if(endpoint)contract.endpoint=endpoint;
      const values=Object.fromEntries([...byId('vault-fields').querySelectorAll('input')].map(input=>[input.dataset.credentialField||'token',input.value]));
      const data={...selected(),action:'set',binding,integration,values};
      if(!existing)data.contract=contract;
      if(mediated) {
        data.values.email=byId('vault-email').value.trim();
        data.effect_contract={integration,operation:'jira.create_issue',endpoint,mediated:true,credential_binding:binding,destination_allowlist:byId('vault-destinations').value.split(',').map(value=>value.trim()).filter(Boolean)};
      }
      try { renderVault(await request('vault',data));message('Credential saved. Its value cannot be read back.'); }
      finally {byId('vault-fields').querySelectorAll('input').forEach(input=>input.value='');byId('vault-email').value='';}
    });
  };
  window.addEventListener('hashchange',load);
  load();
})();
