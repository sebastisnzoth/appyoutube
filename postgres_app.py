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
        def __init__(self, cursor, lastrowid=None):
            self._cursor = cursor
            self.lastrowid = lastrowid

        def fetchone(self):
            return self._cursor.fetchone()

        def fetchall(self):
            return self._cursor.fetchall()

        def __iter__(self):
            return iter(self._cursor)

    class ConnectionProxy:
        def __init__(self):
            self._conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
            self.row_factory = None

        def execute(self, sql, params=()):
            stripped = sql.strip()

            pragma = re.fullmatch(r"PRAGMA\s+table_info\(([^)]+)\)", stripped, flags=re.IGNORECASE)
            if pragma:
                table = pragma.group(1).strip().strip('"\'')
                cur = self._conn.cursor(row_factory=dict_row)
                cur.execute(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
                return CursorProxy(cur)

            sql = re.sub(
                r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT",
                "BIGSERIAL PRIMARY KEY",
                sql,
                flags=re.IGNORECASE,
            )

            if re.search(r"INSERT\s+OR\s+REPLACE\s+INTO\s+radar_run_channels", sql, flags=re.IGNORECASE):
                sql = re.sub(
                    r"INSERT\s+OR\s+REPLACE\s+INTO",
                    "INSERT INTO",
                    sql,
                    count=1,
                    flags=re.IGNORECASE,
                )
                sql += (
                    " ON CONFLICT (run_id, channel_id) DO UPDATE SET "
                    "position=EXCLUDED.position, channel_score=EXCLUDED.channel_score, "
                    "momentum=EXCLUDED.momentum, outliers=EXCLUDED.outliers, "
                    "audience_efficiency=EXCLUDED.audience_efficiency, freshness=EXCLUDED.freshness, "
                    "consistency=EXCLUDED.consistency, observed_growth_per_day=EXCLUDED.observed_growth_per_day, "
                    "confidence_score=EXCLUDED.confidence_score, confidence_label=EXCLUDED.confidence_label, "
                    "created_at=EXCLUDED.created_at"
                )

            sql = sql.replace("?", "%s")
            wants_lastrowid = bool(re.match(r"\s*INSERT\s+INTO\s+radar_runs\b", sql, flags=re.IGNORECASE))
            if wants_lastrowid and "RETURNING" not in sql.upper():
                sql += " RETURNING id"

            cur = self._conn.cursor(row_factory=dict_row)
            cur.execute(sql, params)
            lastrowid = None
            if wants_lastrowid:
                row = cur.fetchone()
                if row:
                    lastrowid = row["id"]
            return CursorProxy(cur, lastrowid=lastrowid)

        def commit(self):
            self._conn.commit()

        def rollback(self):
            self._conn.rollback()

        def close(self):
            self._conn.close()

    def _postgres_connect(*args, **kwargs):
        return ConnectionProxy()

    sqlite3.connect = _postgres_connect

from entry import app


def _extract_gemini_text(payload):
    try:
        parts = payload["candidates"][0]["content"]["parts"]
        return "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError):
        return ""


@app.post("/api/ai/content-pack")
def ai_content_pack():
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        return jsonify({"error": "Falta GEMINI_API_KEY en Vercel."}), 503

    body = request.get_json(silent=True) or {}
    niche = str(body.get("niche", "")).strip()
    evidence = body.get("evidence") or []
    language = str(body.get("language", "es")).strip() or "es"
    if not niche:
        return jsonify({"error": "Falta el nicho u oportunidad."}), 400

    evidence_lines = []
    for item in evidence[:5]:
        if isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            channel = str(item.get("channel", "")).strip()
            outlier = item.get("outlier_score", "")
            views_day = item.get("views_per_day", "")
            evidence_lines.append(f"- {title} | canal: {channel} | outlier: {outlier}x | views/día: {views_day}")

    prompt = f"""Actúa como estratega senior de YouTube. Crea un paquete de contenido accionable en idioma {language} para esta oportunidad detectada por un radar de crecimiento.

Oportunidad/micro-nicho: {niche}
Evidencia observada:
{chr(10).join(evidence_lines) if evidence_lines else '- Sin evidencia adicional'}

Devuelve SOLO JSON válido, sin markdown, con exactamente estas claves:
{{
  "title": "título atractivo y específico, máximo 90 caracteres",
  "description": "descripción lista para YouTube, natural, útil y SEO, 2-4 párrafos",
  "tags": ["tag1", "tag2"],
  "hook": "gancho de apertura para los primeros 15 segundos",
  "thumbnail_concept": "concepto visual de miniatura, breve y concreto",
  "angle": "ángulo editorial que explica por qué este video tiene oportunidad"
}}

No inventes estadísticas. Usa la evidencia solo como señal de demanda. Evita clickbait engañoso."""

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            json=payload,
            timeout=45,
        )
        if not resp.ok:
            detail = ""
            try:
                detail = (resp.json().get("error") or {}).get("message", "")
            except Exception:
                detail = resp.text[:300]
            return jsonify({"error": f"Gemini API respondió {resp.status_code}: {detail or 'error desconocido'}"}), 502
        text = _extract_gemini_text(resp.json())
        if not text:
            return jsonify({"error": "Gemini no devolvió contenido."}), 502
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(cleaned)
        tags = data.get("tags") or []
        if not isinstance(tags, list):
            tags = [x.strip() for x in str(tags).split(",") if x.strip()]
        return jsonify({
            "title": str(data.get("title", ""))[:100],
            "description": str(data.get("description", "")),
            "tags": tags[:30],
            "hook": str(data.get("hook", "")),
            "thumbnail_concept": str(data.get("thumbnail_concept", "")),
            "angle": str(data.get("angle", "")),
        })
    except requests.RequestException as exc:
        return jsonify({"error": f"No se pudo contactar Gemini: {exc}"}), 502
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"Respuesta inválida de Gemini: {exc}"}), 502


_AI_CARD = r'''
<div id="aiPackCard" class="card hidden">
  <h2>Paquete de contenido con IA</h2>
  <p class="note">Gemini convierte una oportunidad del radar en título, descripción, tags, gancho y concepto de miniatura.</p>
  <div id="aiPackResult"></div>
</div>
'''

_AI_SCRIPT = r'''
<script>
window.__radarData = null;
const __baseRender = window.render;
window.render = function(d){
  window.__radarData = d;
  __baseRender(d);
  setTimeout(()=>{
    document.querySelectorAll('#niches .niche').forEach((el,i)=>{
      if(el.querySelector('.ai-generate-btn')) return;
      const b=document.createElement('button');
      b.className='secondary ai-generate-btn';
      b.style.marginTop='12px';
      b.textContent='Generar contenido con IA';
      b.onclick=()=>window.generateContentPack(i);
      el.appendChild(b);
    });
  },0);
};
window.generateContentPack = async function(i){
  const niche=(window.__radarData?.niches||[])[i];
  if(!niche) return;
  aiPackCard.classList.remove('hidden');
  aiPackResult.innerHTML='<div class="notice">Generando con Gemini…</div>';
  aiPackCard.scrollIntoView({behavior:'smooth',block:'start'});
  try{
    const d=await api('/api/ai/content-pack',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({niche:niche.name,evidence:niche.evidence||[],language:'es'})
    });
    aiPackResult.innerHTML=`
      <div class="notice ok"><b>Paquete generado</b></div>
      <div class="card" style="margin:10px 0;background:#0f162c">
        <div class="note">Título</div><h3>${esc(d.title)}</h3>
        <div class="note">Gancho</div><p>${esc(d.hook)}</p>
        <div class="note">Ángulo</div><p>${esc(d.angle)}</p>
        <div class="note">Miniatura</div><p>${esc(d.thumbnail_concept)}</p>
        <div class="note">Tags</div><p>${esc((d.tags||[]).join(', '))}</p>
        <button id="applyAiPack" class="secondary">Usar en Publicar en YouTube</button>
      </div>`;
    const apply=document.getElementById('applyAiPack');
    apply.onclick=()=>{
      if(typeof publishForm==='undefined') return;
      publishForm.elements.title.value=d.title||'';
      publishForm.elements.description.value=d.description||'';
      publishForm.elements.tags.value=(d.tags||[]).join(', ');
      publishCard.classList.remove('hidden');
      publishCard.scrollIntoView({behavior:'smooth',block:'start'});
    };
  }catch(e){
    aiPackResult.innerHTML=`<div class="notice err">${esc(e.message)}</div>`;
  }
};
</script>
'''


def _home_with_ai():
    html = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
    marker = '<div class="card"><h2>Radar global</h2>'
    if marker in html:
        html = html.replace(marker, _AI_CARD + marker, 1)
    html = html.replace("</body>", _AI_SCRIPT + "</body>", 1)
    return html


app.view_functions["home"] = _home_with_ai
