import json
import os
import re
import sqlite3
from pathlib import Path

import requests
from flask import jsonify, request

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if DATABASE_URL:
    import psycopg
    from psycopg.rows import dict_row

    class CursorProxy:
        def __init__(self, cursor, lastrowid=None): self._cursor, self.lastrowid = cursor, lastrowid
        def fetchone(self): return self._cursor.fetchone()
        def fetchall(self): return self._cursor.fetchall()
        def __iter__(self): return iter(self._cursor)

    class ConnectionProxy:
        def __init__(self):
            self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
            self.row_factory = None
        def execute(self, sql, params=()):
            stripped=sql.strip()
            pragma=re.fullmatch(r"PRAGMA\s+table_info\(([^)]+)\)",stripped,flags=re.IGNORECASE)
            if pragma:
                table=pragma.group(1).strip().strip('"\'')
                cur=self._conn.cursor(row_factory=dict_row)
                cur.execute("SELECT column_name AS name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,))
                return CursorProxy(cur)
            sql=re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT","BIGSERIAL PRIMARY KEY",sql,flags=re.IGNORECASE)
            if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+radar_run_channels",sql,flags=re.IGNORECASE):
                sql=re.sub(r"INSERT\s+OR\s+REPLACE\s+INTO","INSERT INTO",sql,count=1,flags=re.IGNORECASE)
                sql += " ON CONFLICT (run_id, channel_id) DO UPDATE SET position=EXCLUDED.position, channel_score=EXCLUDED.channel_score, momentum=EXCLUDED.momentum, outliers=EXCLUDED.outliers, audience_efficiency=EXCLUDED.audience_efficiency, freshness=EXCLUDED.freshness, consistency=EXCLUDED.consistency, observed_growth_per_day=EXCLUDED.observed_growth_per_day, confidence_score=EXCLUDED.confidence_score, confidence_label=EXCLUDED.confidence_label, created_at=EXCLUDED.created_at"
            sql=sql.replace("?","%s")
            wants=bool(re.match(r"\s*INSERT\s+INTO\s+radar_runs\b",sql,flags=re.IGNORECASE))
            if wants and "RETURNING" not in sql.upper(): sql += " RETURNING id"
            cur=self._conn.cursor(row_factory=dict_row); cur.execute(sql,params); lastrowid=None
            if wants:
                row=cur.fetchone(); lastrowid=row["id"] if row else None
            return CursorProxy(cur,lastrowid)
        def commit(self): self._conn.commit()
        def rollback(self): self._conn.rollback()
        def close(self): self._conn.close()
    def _postgres_connect(*args,**kwargs): return ConnectionProxy()
    sqlite3.connect=_postgres_connect

from entry import app

MODEL="gemini-3.6-flash"

def _gemini(prompt, json_mode=True):
    key=os.getenv("GEMINI_API_KEY","").strip()
    if not key: raise RuntimeError("Falta GEMINI_API_KEY en Vercel.")
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    config={"responseMimeType":"application/json"} if json_mode else {}
    r=requests.post(url,headers={"Content-Type":"application/json","x-goog-api-key":key},json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":config},timeout=55)
    if not r.ok:
        try: detail=(r.json().get("error") or {}).get("message","")
        except Exception: detail=r.text[:300]
        raise RuntimeError(f"Gemini API respondió {r.status_code}: {detail or 'error desconocido'}")
    try: parts=r.json()["candidates"][0]["content"]["parts"]
    except (KeyError,IndexError,TypeError): raise RuntimeError("Gemini no devolvió contenido.")
    return "".join(p.get("text","") for p in parts).strip()

def _json(text):
    clean=text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(clean)

@app.post("/api/ai/content-pack")
def ai_content_pack():
    b=request.get_json(silent=True) or {}; niche=str(b.get("niche","")).strip(); evidence=b.get("evidence") or []
    if not niche: return jsonify({"error":"Falta el nicho u oportunidad."}),400
    lines=[]
    for x in evidence[:5]:
        if isinstance(x,dict): lines.append(f"- {x.get('title','')} | canal: {x.get('channel','')} | outlier: {x.get('outlier_score','')}x | views/día: {x.get('views_per_day','')}")
    prompt=f'''Actúa como estratega senior de YouTube. Crea un paquete accionable en español para: {niche}.\nEvidencia:\n{chr(10).join(lines) or '- Sin evidencia adicional'}\nDevuelve SOLO JSON válido con: title (máx 90 caracteres), description (2-4 párrafos), tags (array), hook, thumbnail_concept y angle. No inventes estadísticas ni uses clickbait engañoso.'''
    try:
        d=_json(_gemini(prompt)); tags=d.get("tags") or []
        if not isinstance(tags,list): tags=[x.strip() for x in str(tags).split(",") if x.strip()]
        return jsonify(title=str(d.get("title",""))[:100],description=str(d.get("description","")),tags=tags[:30],hook=str(d.get("hook","")),thumbnail_concept=str(d.get("thumbnail_concept","")),angle=str(d.get("angle","")))
    except Exception as e: return jsonify({"error":str(e)}),502

@app.post("/api/ai/script")
def ai_script():
    b=request.get_json(silent=True) or {}; title=str(b.get("title","")).strip(); hook=str(b.get("hook","")).strip(); angle=str(b.get("angle","")).strip()
    if not title: return jsonify({"error":"Primero genera un paquete de contenido."}),400
    prompt=f'''Escribe un guion completo de YouTube en español para el título: {title}. Gancho sugerido: {hook}. Ángulo: {angle}. Duración objetivo 6-8 minutos. Debe sonar hablado y natural. Estructura: apertura de alta retención, promesa clara, desarrollo por bloques con transiciones, ejemplos concretos sin inventar datos, recapitulación y CTA breve. Devuelve SOLO JSON válido con las claves script, chapters (array de objetos con time y title) y estimated_minutes.'''
    try:
        d=_json(_gemini(prompt)); return jsonify(script=str(d.get("script","")),chapters=d.get("chapters") or [],estimated_minutes=d.get("estimated_minutes",7))
    except Exception as e: return jsonify({"error":str(e)}),502

@app.post("/api/ai/thumbnail")
def ai_thumbnail():
    b=request.get_json(silent=True) or {}; title=str(b.get("title","")).strip(); concept=str(b.get("concept","")).strip(); angle=str(b.get("angle","")).strip()
    if not title: return jsonify({"error":"Primero genera un paquete de contenido."}),400
    prompt=f'''Eres director creativo de miniaturas de YouTube. Para título: {title}. Concepto inicial: {concept}. Ángulo: {angle}. Diseña una miniatura 16:9 de alto CTR pero no engañosa. Devuelve SOLO JSON con image_prompt (prompt visual detallado para un generador de imágenes, sin logos ni marcas), overlay_text (máximo 4 palabras), composition, focal_subject y negative_prompt. El prompt debe pedir composición limpia, sujeto grande, contraste visual fuerte y espacio legible para texto.'''
    try: return jsonify(_json(_gemini(prompt)))
    except Exception as e: return jsonify({"error":str(e)}),502

_AI_CARD=r'''<div id="aiPackCard" class="card hidden"><h2>Paquete de contenido con IA</h2><p class="note">Gemini convierte una oportunidad en paquete, guion y concepto de miniatura.</p><div id="aiPackResult"></div></div>'''
_AI_SCRIPT=r'''<script>
window.__radarData=null; window.__aiPack=null;
const __baseRender=window.render;
window.render=function(d){window.__radarData=d;__baseRender(d);setTimeout(()=>{document.querySelectorAll('#niches .niche').forEach((el,i)=>{if(el.querySelector('.ai-generate-btn'))return;const b=document.createElement('button');b.className='secondary ai-generate-btn';b.style.marginTop='12px';b.textContent='Generar contenido con IA';b.onclick=()=>generateContentPack(i);el.appendChild(b);});},0)};
window.generateContentPack=async function(i){const n=(__radarData?.niches||[])[i];if(!n)return;aiPackCard.classList.remove('hidden');aiPackResult.innerHTML='<div class="notice">Generando con Gemini…</div>';aiPackCard.scrollIntoView({behavior:'smooth'});try{const d=await api('/api/ai/content-pack',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({niche:n.name,evidence:n.evidence||[]})});window.__aiPack=d;aiPackResult.innerHTML=`<div class="notice ok"><b>Paquete generado</b></div><div class="card" style="margin:10px 0;background:#0f162c"><div class="note">Título</div><h3>${esc(d.title)}</h3><div class="note">Gancho</div><p>${esc(d.hook)}</p><div class="note">Ángulo</div><p>${esc(d.angle)}</p><div class="note">Miniatura</div><p>${esc(d.thumbnail_concept)}</p><div class="note">Tags</div><p>${esc((d.tags||[]).join(', '))}</p><button id="applyAiPack" class="secondary">Usar en Publicar en YouTube</button> <button id="genScript" class="secondary">Generar guion completo</button> <button id="genThumb" class="secondary">Generar miniatura IA</button><div id="aiExtras" style="margin-top:14px"></div></div>`;document.getElementById('applyAiPack').onclick=()=>{publishForm.elements.title.value=d.title||'';publishForm.elements.description.value=d.description||'';publishForm.elements.tags.value=(d.tags||[]).join(', ');publishCard.classList.remove('hidden');publishCard.scrollIntoView({behavior:'smooth'});};document.getElementById('genScript').onclick=generateScript;document.getElementById('genThumb').onclick=generateThumbnail;}catch(e){aiPackResult.innerHTML=`<div class="notice err">${esc(e.message)}</div>`;}};
window.generateScript=async function(){const d=__aiPack;if(!d)return;aiExtras.innerHTML='<div class="notice">Escribiendo guion…</div>';try{const r=await api('/api/ai/script',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:d.title,hook:d.hook,angle:d.angle})});aiExtras.innerHTML=`<h3>Guion completo · ~${esc(String(r.estimated_minutes))} min</h3><textarea style="width:100%;min-height:420px">${esc(r.script)}</textarea><div class="note">Capítulos: ${esc((r.chapters||[]).map(x=>(x.time||'')+' '+(x.title||'')).join(' · '))}</div>`;}catch(e){aiExtras.innerHTML=`<div class="notice err">${esc(e.message)}</div>`;}};
window.generateThumbnail=async function(){const d=__aiPack;if(!d)return;aiExtras.innerHTML='<div class="notice">Diseñando miniatura…</div>';try{const r=await api('/api/ai/thumbnail',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:d.title,concept:d.thumbnail_concept,angle:d.angle})});aiExtras.innerHTML=`<h3>Miniatura IA</h3><p><b>Texto:</b> ${esc(r.overlay_text||'')}</p><p><b>Composición:</b> ${esc(r.composition||'')}</p><p><b>Prompt de imagen:</b></p><textarea style="width:100%;min-height:180px">${esc(r.image_prompt||'')}</textarea><p class="note">Prompt negativo: ${esc(r.negative_prompt||'')}</p>`;}catch(e){aiExtras.innerHTML=`<div class="notice err">${esc(e.message)}</div>`;}};
</script>'''
def _home_with_ai():
    html=(Path(__file__).parent/"static"/"index.html").read_text(encoding="utf-8"); marker='<div class="card"><h2>Radar global</h2>'
    if marker in html: html=html.replace(marker,_AI_CARD+marker,1)
    return html.replace("</body>",_AI_SCRIPT+"</body>",1)
app.view_functions["home"]=_home_with_ai
