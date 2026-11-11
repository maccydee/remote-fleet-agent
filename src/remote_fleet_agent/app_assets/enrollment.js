/* Owner-created, one-device kits. Enrollment secrets stay outside conversations. */
async function renderEnrollmentSettings(route) {
  if (routeFromURL().page !== 'Settings' || S.boot?.actor?.role !== 'owner') return;
  const client = route.client || S.boot.clients.find(c => !c.simulation)?.id || '';
  const container = document.createElement('section');
  container.id = 'mac-enrollment';
  const real=client&&!S.boot.clients.find(c=>c.id===client)?.simulation;
  container.innerHTML=panel('Connect a Mac or Linux device',`<div class="panel-body"><p>Create a single-use kit and run it on one device. Its account and host key return securely; no identity details to copy. Native checks work after SSH verification while Python setup runs separately.</p><p>Mac: double-click Connect.command. Linux: run sh ./Connect.sh as the target account. Approve administrator access if required for SSH setup.</p><div class="actions"><button id="new-enrollment-client">Add authorized client</button>${real?'<button data-enroll-platform="mac" class="primary">Create Mac connection kit</button><button data-enroll-platform="linux" class="primary">Create Linux connection kit</button>':''}<button id="refresh-enrollment">Refresh connection status</button></div><div id="enrollment-status"></div></div>`);
  if(document.querySelector('#mac-enrollment'))return;
  document.querySelector('#view').prepend(container);
  const endpoint=platform=>'/api/clients/'+encodeURIComponent(client)+'/'+platform+'-enrollment';
  async function statuses(){
    const area=container.querySelector('#enrollment-status');
    if(!real){area.textContent='Select an authorized real client, or add one here.';return;}
    try{
      const results=await Promise.all(['mac','linux'].map(async platform=>({platform,...await api(endpoint(platform))})));
      if(!container.isConnected)return;
      area.innerHTML='<p>Selected client: '+esc(client)+'</p>'+results.flatMap(data=>data.devices.map(d=>'<div class="section-card"><strong>'+esc(d.device||d.target||('Waiting for '+(data.platform==='mac'?'Mac':'Linux')))+'</strong><p>'+esc(data.platform==='mac'?'macOS':'Linux')+' · SSH: '+esc(pretty(d.connection_state))+' · Python: '+esc(pretty(d.python_state))+' · Site: '+esc(d.site)+'</p>'+(d.error?'<p>'+esc(d.error)+'</p>':'')+(d.connection_state==='waiting'?'<p>Kit expires '+date(d.expires_at)+'</p><button data-revoke-kit="'+esc(d.id)+'" data-platform="'+data.platform+'">Revoke unused kit</button>':'')+'</div>')).join('');
      area.querySelectorAll('[data-revoke-kit]').forEach(b=>b.onclick=async()=>{try{await api(endpoint(b.dataset.platform)+'/'+b.dataset.revokeKit+'/revoke',{});await statuses()}catch(e){toast(e.message)}});
    }catch(e){if(container.isConnected)area.textContent=e.message;}
  }
  container.querySelector('#refresh-enrollment').onclick=statuses;
  container.querySelector('#new-enrollment-client').onclick=()=>form('Add authorized client',field('Client ID','client','','text','required')+field('Authorization reference','authorisation_reference','','text','required'),async v=>{const result=await api('/api/enrollment-clients',v);S.boot=await api('/api/bootstrap');goRoute({page:'Settings',client:result.client});});
  container.querySelectorAll('[data-enroll-platform]').forEach(button=>button.onclick=async()=>{
    const platform=button.dataset.enrollPlatform,label=platform==='mac'?'Mac':'Linux';
    try{
      const state=await api(endpoint(platform));
      form('Create a one-device '+label+' kit',field('Controller private IPv4','controller_address',state.controller_address,'text','required')+field('Site','site',route.site||'main','text','required')+`<label><input type="checkbox" name="install_python" ${state.defaults.install_python?'checked':''}> Set up Python after SSH is connected</label><p>This authorizes source-bound SSH key access and required SSH setup when the kit is run. Linux may install OpenSSH Server and curl if missing. Keep the controller running on the same private network. Revoking a kit does not remove an installed key; use the kit’s removal script.</p>`,async v=>{
        const result=await api(endpoint(platform),{...v,install_python:v.install_python==='on'});
        const url=URL.createObjectURL(new Blob([Uint8Array.from(atob(result.zip_base64),c=>c.charCodeAt(0))],{type:'application/zip'}));
        const link=document.createElement('a');link.href=url;link.download=result.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
      });
    }catch(e){toast(e.message)}
  });
  await statuses();
  const poll=()=>setTimeout(async()=>{if(!container.isConnected)return;await statuses();poll();},3000);poll();
}
