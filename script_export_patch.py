from short_patch import app

_SCRIPT_EXPORT = r'''
<script>
(function(){
  function safeName(value, fallback){
    const s=String(value||fallback||'guion').normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9-_]+/gi,'-').replace(/^-+|-+$/g,'').slice(0,60);
    return s || fallback || 'guion';
  }

  function downloadText(filename, text){
    const blob=new Blob([text],{type:'text/plain;charset=utf-8'});
    const a=document.createElement('a');
    a.href=URL.createObjectURL(blob);
    a.download=filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href),500);
  }

  function addLongExport(){
    const extras=document.getElementById('aiExtras');
    if(!extras || document.getElementById('exportLongScript')) return;
    const ta=extras.querySelector('textarea');
    if(!ta || !ta.value.trim()) return;
    const b=document.createElement('button');
    b.type='button';
    b.id='exportLongScript';
    b.className='secondary';
    b.style.marginTop='10px';
    b.textContent='Exportar guion largo (.txt)';
    b.onclick=()=>{
      const title=document.getElementById('publishForm')?.elements?.title?.value || 'video-largo';
      downloadText(`guion-${safeName(title,'video-largo')}.txt`, ta.value);
    };
    extras.appendChild(b);
  }

  function addShortExport(){
    const ta=document.getElementById('shortScriptText');
    if(!ta || document.getElementById('exportShortScript')) return;
    const b=document.createElement('button');
    b.type='button';
    b.id='exportShortScript';
    b.className='secondary';
    b.style.margin='10px 8px 10px 0';
    b.textContent='Exportar guion Short (.txt)';
    b.onclick=()=>{
      const title=(window.__shortPack&&window.__shortPack.short_title) || 'short';
      let content=ta.value;
      const pack=window.__shortPack||{};
      if(pack.hook) content=`GANCHO\n${pack.hook}\n\nGUION\n${content}`;
      if(pack.cta) content+=`\n\nCTA\n${pack.cta}`;
      downloadText(`guion-short-${safeName(title,'short')}.txt`,content);
    };
    ta.insertAdjacentElement('afterend',b);
  }

  function refresh(){ addLongExport(); addShortExport(); }
  const observer=new MutationObserver(refresh);
  observer.observe(document.body,{childList:true,subtree:true});
  document.addEventListener('input',refresh);
  refresh();
})();
</script>
'''

_previous_home = app.view_functions.get('home')
def home_with_script_export():
    html = _previous_home()
    if isinstance(html, str) and '</body>' in html and 'exportLongScript' not in html:
        return html.replace('</body>', _SCRIPT_EXPORT + '</body>', 1)
    return html

app.view_functions['home'] = home_with_script_export
