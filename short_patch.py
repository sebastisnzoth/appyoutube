import json
from flask import jsonify, request

from config_patch import app
from ai_patch import _gemini_text


@app.post("/api/ai/short")
def ai_short():
    body = request.get_json(silent=True) or {}
    title = str(body.get("title", "")).strip()
    description = str(body.get("description", "")).strip()
    source_script = str(body.get("script", "")).strip()
    hook = str(body.get("hook", "")).strip()
    angle = str(body.get("angle", "")).strip()
    if not title and not source_script:
        return jsonify({"error": "Falta un título o guion base para generar el Short."}), 400

    prompt = f"""Actúa como estratega experto en YouTube Shorts.
Crea UN Short en español basado en este contenido.
Título base: {title}
Descripción/contexto: {description}
Gancho base: {hook}
Ángulo: {angle}
Guion largo opcional:
{source_script[:10000]}

Objetivo: un Short vertical 9:16 de 25 a 45 segundos, con máxima retención sin clickbait engañoso.
Devuelve SOLO JSON válido con estas claves:
short_title: título breve, máximo 70 caracteres.
hook: primera frase de 1-2 segundos.
script: guion hablado completo de 25-45 segundos.
on_screen_text: array de 3 a 7 textos cortos que aparecerían en pantalla.
shot_list: array de objetos con time, visual y caption para editar el video vertical.
description: descripción breve para YouTube.
hashtags: array de 3 a 8 hashtags, incluyendo #Shorts cuando corresponda.
first_frame: concepto visual del primer frame 9:16.
cta: CTA final muy breve.
No inventes estadísticas ni hechos que no estén en el material base."""

    try:
        raw = _gemini_text(prompt).strip()
        clean = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(clean)
        hashtags = data.get("hashtags") or []
        if not isinstance(hashtags, list):
            hashtags = [x.strip() for x in str(hashtags).split() if x.strip()]
        on_screen = data.get("on_screen_text") or []
        shots = data.get("shot_list") or []
        return jsonify({
            "short_title": str(data.get("short_title", ""))[:70],
            "hook": str(data.get("hook", "")),
            "script": str(data.get("script", "")),
            "on_screen_text": on_screen if isinstance(on_screen, list) else [],
            "shot_list": shots if isinstance(shots, list) else [],
            "description": str(data.get("description", "")),
            "hashtags": hashtags[:8],
            "first_frame": str(data.get("first_frame", "")),
            "cta": str(data.get("cta", "")),
        })
    except json.JSONDecodeError:
        return jsonify({"error": "Gemini devolvió un formato inesperado al generar el Short. Inténtalo otra vez."}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


_SHORT_UI = r'''
<div id="shortCard" class="card">
  <h2>Generar YouTube Short</h2>
  <p class="note">Convierte el contenido del formulario o el paquete de IA en un Short vertical 9:16 de 25–45 segundos.</p>
  <div class="toolbar">
    <button type="button" id="generateShortBtn" class="secondary">Generar Short con IA</button>
    <span id="shortStatus" class="note"></span>
  </div>
  <div id="shortResult"></div>
</div>
'''

_SHORT_SCRIPT = r'''
<script>
(function(){
  const btn=document.getElementById('generateShortBtn');
  const out=document.getElementById('shortResult');
  const status=document.getElementById('shortStatus');
  const form=document.getElementById('publishForm');
  if(!btn || !out || !form) return;

  function getLongScript(){
    const ta=document.querySelector('#aiExtras textarea');
    return ta ? ta.value : '';
  }
  function getPack(){
    return window.__aiPack || {};
  }
  btn.addEventListener('click',async()=>{
    btn.disabled=true; btn.textContent='Generando Short…';
    status.textContent='';
    out.innerHTML='<div class="notice">Gemini está preparando el Short…</div>';
    try{
      const p=getPack();
      const r=await api('/api/ai/short',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          title:form.elements.title?.value||p.title||'',
          description:form.elements.description?.value||p.description||'',
          script:getLongScript(),
          hook:p.hook||'',
          angle:p.angle||''
        })
      });
      window.__shortPack=r;
      const hashtags=(r.hashtags||[]).join(' ');
      const texts=(r.on_screen_text||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join(' ');
      const shots=(r.shot_list||[]).map(x=>`<div class="evidence"><b>${esc(x.time||'')}</b> · ${esc(x.visual||'')}<br><span class="note">${esc(x.caption||'')}</span></div>`).join('');
      out.innerHTML=`<div class="card" style="margin-top:12px;background:#0f162c">
        <div class="note">Título del Short</div><h3>${esc(r.short_title||'')}</h3>
        <div class="note">Gancho</div><p>${esc(r.hook||'')}</p>
        <div class="note">Guion 25–45s</div><textarea id="shortScriptText" style="width:100%;min-height:180px">${esc(r.script||'')}</textarea>
        <div class="note">Texto en pantalla</div><div class="badges">${texts}</div>
        <div class="note" style="margin-top:12px">Primer frame 9:16</div><p>${esc(r.first_frame||'')}</p>
        <div class="note">Plan de tomas</div>${shots}
        <div class="note" style="margin-top:12px">Descripción</div><textarea id="shortDescriptionText" style="width:100%;min-height:100px">${esc(r.description||'')}\n\n${esc(hashtags)}</textarea>
        <div class="note">CTA</div><p>${esc(r.cta||'')}</p>
        <button type="button" id="useShortPublish" class="secondary">Usar datos del Short para publicar</button>
      </div>`;
      document.getElementById('useShortPublish').onclick=()=>{
        form.elements.title.value=r.short_title||'';
        form.elements.description.value=(r.description||'')+'\n\n'+(r.hashtags||[]).join(' ');
        const base=(form.elements.tags.value||'').split(',').map(x=>x.trim()).filter(Boolean);
        const h=(r.hashtags||[]).map(x=>String(x).replace(/^#/,''));
        form.elements.tags.value=[...new Set([...base,...h,'Shorts'])].slice(0,30).join(', ');
        if(status) status.textContent='Datos del Short cargados. Ahora seleccioná un video vertical 9:16 y publicalo.';
        form.scrollIntoView({behavior:'smooth'});
        form.dispatchEvent(new Event('input',{bubbles:true}));
      };
    }catch(e){ out.innerHTML=`<div class="notice err">${esc(e.message)}</div>`; }
    finally{ btn.disabled=false; btn.textContent='Generar Short con IA'; }
  });
})();
</script>
'''

_previous_home = app.view_functions.get("home")
def home_with_shorts():
    html = _previous_home()
    if not isinstance(html, str):
        return html
    marker = '<div class="card"><h2>Radar global</h2>'
    if marker in html and 'id="shortCard"' not in html:
        html = html.replace(marker, _SHORT_UI + marker, 1)
    if '</body>' in html and 'id="generateShortBtn"' not in html:
        html = html.replace('</body>', _SHORT_SCRIPT + '</body>', 1)
    return html

app.view_functions["home"] = home_with_shorts
