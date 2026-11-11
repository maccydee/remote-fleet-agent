/* Owner-created, one-device kits. Enrollment secrets stay outside conversations. */
async function renderEnrollmentSettings(route) {
  if (routeFromURL().page !== 'Settings' || S.boot?.actor?.role !== 'owner') return;
  const client = route.client || S.boot.clients.find(c => !c.simulation)?.id || '';
  const container = document.createElement('section');
  container.id = 'mac-enrollment';
  container.innerHTML = panel('Connect a Mac', `<div class="panel-body"><p>Create a single-use kit, copy it to the Mac and double-click Connect.command. The Mac sends its account and host key back securely. Native checks work immediately after SSH verification.</p><p>Optional Python setup runs over the verified SSH connection and uses a private runtime. macOS may require approval for Remote Login in System Settings.</p><div class="actions"><button id="new-enrollment-client">Add authorized client</button>${client && !S.boot.clients.find(c=>c.id===client)?.simulation ? '<button id="new-mac-kit" class="primary">Create Mac connection kit</button>' : ''}<button id="refresh-enrollment">Refresh connection status</button></div><div id="enrollment-status"></div></div>`);
  if (document.querySelector('#mac-enrollment')) return;
  document.querySelector('#view').prepend(container);
  async function statuses() {
    const area=container.querySelector('#enrollment-status');
    if (!client || S.boot.clients.find(c=>c.id===client)?.simulation) { area.textContent='Select an authorized real client, or add one here.'; return; }
    try {
      const data=await api('/api/clients/'+encodeURIComponent(client)+'/mac-enrollment');
      if (!container.isConnected) return;
      area.innerHTML='<p>Selected client: '+esc(client)+'</p>'+data.devices.map(d=>'<div class="section-card"><strong>'+esc(d.device||d.target||'Waiting for Mac')+'</strong><p>SSH: '+esc(pretty(d.connection_state))+' · Python: '+esc(pretty(d.python_state))+' · Site: '+esc(d.site)+'</p>'+(d.error?'<p>'+esc(d.error)+'</p>':'')+(d.connection_state==='waiting'?'<p>Kit expires '+date(d.expires_at)+'</p><button data-revoke-kit="'+esc(d.id)+'">Revoke unused kit</button>':'')+'</div>').join('');
      area.querySelectorAll('[data-revoke-kit]').forEach(b=>b.onclick=async()=>{try{await api('/api/clients/'+encodeURIComponent(client)+'/mac-enrollment/'+b.dataset.revokeKit+'/revoke',{});await statuses()}catch(e){toast(e.message)}});
    } catch(e) {area.textContent=e.message;}
  }
  container.querySelector('#refresh-enrollment').onclick=statuses;
  container.querySelector('#new-enrollment-client').onclick=()=>form('Add authorized client',field('Client ID','client','','text','required')+field('Authorization reference','authorisation_reference','','text','required'),async v=>{const result=await api('/api/enrollment-clients',v);S.boot=await api('/api/bootstrap');goRoute({page:'Settings',client:result.client});});
  const button=container.querySelector('#new-mac-kit');
  if(button) button.onclick=async()=>{
    try {
      const state=await api('/api/clients/'+encodeURIComponent(client)+'/mac-enrollment');
      form('Create a one-device Mac kit',field('Controller private IPv4','controller_address',state.controller_address,'text','required')+field('Site','site',route.site||'main','text','required')+`<label><input type="checkbox" name="install_python" ${state.defaults.install_python?'checked':''}> Install private Python after SSH is connected</label><p>This authorizes Remote Login setup and source-bound SSH key access when the kit is run. Keep the workspace running on the same private network. Access persists until removed or the client expiry. Revoking a kit does not remove an installed key.</p>`,async v=>{
        const result=await api('/api/clients/'+encodeURIComponent(client)+'/mac-enrollment',{...v,install_python:v.install_python==='on'});
        const bytes=Uint8Array.from(atob(result.zip_base64),c=>c.charCodeAt(0));
        const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
        const link=document.createElement('a');link.href=url;link.download=result.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
      });
    }catch(e){toast(e.message)}
  };
  await statuses();
  const poll=()=>setTimeout(async()=>{if(!container.isConnected)return;await statuses();poll();},3000);
  poll();
}
