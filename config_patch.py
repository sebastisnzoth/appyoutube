import json
from flask import jsonify

from ai_patch import app

_UPLOAD_CONFIG_UI = r'''
<div id="uploadConfigTools" class="toolbar" style="margin:10px 0 14px">
  <button type="button" id="exportUploadConfig" class="secondary">Exportar configuración</button>
  <label class="button secondary" style="cursor:pointer">Importar configuración
    <input id="importUploadConfig" type="file" accept="application/json,.json" style="display:none">
  </label>
  <span id="uploadConfigStatus" class="note"></span>
</div>
'''

_UPLOAD_CONFIG_SCRIPT = r'''
<script>
(function(){
  const form=document.getElementById('publishForm');
  if(!form) return;
  const storageKey='nicheradar-upload-config-v1';
  const status=document.getElementById('uploadConfigStatus');
  function readConfig(){
    return {
      version:1,
      exported_at:new Date().toISOString(),
      title:form.elements.title?.value||'',
      description:form.elements.description?.value||'',
      tags:form.elements.tags?.value||'',
      category_id:form.elements.category_id?.value||'22',
      privacy_status:form.elements.privacy_status?.value||'private',
      channel_id:(document.getElementById('publishChannelId')?.value||''),
      video_filename:form.elements.video?.files?.[0]?.name||'',
      thumbnail_filename:form.elements.thumbnail?.files?.[0]?.name||''
    };
  }
  function applyConfig(c){
    if(!c || typeof c!=='object') throw new Error('Archivo de configuración inválido.');
    if(form.elements.title) form.elements.title.value=c.title||'';
    if(form.elements.description) form.elements.description.value=c.description||'';
    if(form.elements.tags) form.elements.tags.value=c.tags||'';
    if(form.elements.category_id) form.elements.category_id.value=c.category_id||'22';
    if(form.elements.privacy_status) form.elements.privacy_status.value=c.privacy_status||'private';
    const ch=document.getElementById('publishChannelId');
    if(ch && c.channel_id) ch.value=c.channel_id;
    localStorage.setItem(storageKey,JSON.stringify(readConfig()));
    if(status) status.textContent='Configuración cargada. Volvé a seleccionar video y miniatura.';
  }
  function saveLocal(){
    try{ localStorage.setItem(storageKey,JSON.stringify(readConfig())); }catch(_){ }
  }
  form.addEventListener('input',saveLocal);
  form.addEventListener('change',saveLocal);
  const exp=document.getElementById('exportUploadConfig');
  if(exp) exp.addEventListener('click',()=>{
    const cfg=readConfig();
    const blob=new Blob([JSON.stringify(cfg,null,2)],{type:'application/json'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    const safe=(cfg.title||'video').replace(/[^a-z0-9-_]+/gi,'-').replace(/^-+|-+$/g,'').slice(0,50)||'video';
    a.download=`nicheradar-${safe}.json`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(a.href);
    saveLocal();
    if(status) status.textContent='Configuración exportada.';
  });
  const imp=document.getElementById('importUploadConfig');
  if(imp) imp.addEventListener('change',async()=>{
    const f=imp.files?.[0]; if(!f) return;
    try{ applyConfig(JSON.parse(await f.text())); }
    catch(e){ if(status) status.textContent=e.message; }
    finally{ imp.value=''; }
  });
  try{
    const cached=JSON.parse(localStorage.getItem(storageKey)||'null');
    if(cached && !form.elements.title.value && !form.elements.description.value){
      applyConfig(cached);
      if(status) status.textContent='Borrador recuperado automáticamente. Volvé a seleccionar los archivos.';
    }
  }catch(_){ }
})();
</script>
'''

_previous_home = app.view_functions.get("home")
def home_with_upload_config_tools():
    html = _previous_home()
    if not isinstance(html, str):
        return html
    marker = '<form id="publishForm" class="publish-grid">'
    if marker in html and 'id="uploadConfigTools"' not in html:
        html = html.replace(marker, _UPLOAD_CONFIG_UI + marker, 1)
    if '</body>' in html and 'nicheradar-upload-config-v1' not in html:
        html = html.replace('</body>', _UPLOAD_CONFIG_SCRIPT + '</body>', 1)
    return html

app.view_functions["home"] = home_with_upload_config_tools
