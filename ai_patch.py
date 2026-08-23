import os
import tempfile
from datetime import datetime, timezone

import requests
from flask import jsonify, request
from googleapiclient.http import MediaFileUpload

from postgres_app import app, MODEL
import oauth_app as oauth_core


def _gemini_text(prompt):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Falta GEMINI_API_KEY en Vercel.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    r = requests.post(
        url,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60,
    )
    if not r.ok:
        try:
            detail = (r.json().get("error") or {}).get("message", "")
        except Exception:
            detail = r.text[:300]
        raise RuntimeError(f"Gemini API respondió {r.status_code}: {detail or 'error desconocido'}")
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("Gemini no devolvió contenido.")
    return "".join(p.get("text", "") for p in parts).strip()


def ai_script_resilient():
    b = request.get_json(silent=True) or {}
    title = str(b.get("title", "")).strip()
    hook = str(b.get("hook", "")).strip()
    angle = str(b.get("angle", "")).strip()
    if not title:
        return jsonify({"error": "Primero genera un paquete de contenido."}), 400

    prompt = f"""Escribe un guion completo de YouTube en español para el título: {title}.
Gancho sugerido: {hook}
Ángulo: {angle}
Duración objetivo: 6 a 8 minutos.

Quiero SOLO el guion final en texto plano, sin JSON, sin markdown y sin bloques de código.
Debe sonar hablado y natural. Incluye apertura de alta retención, promesa clara, desarrollo por bloques con transiciones, ejemplos concretos sin inventar datos, recapitulación y CTA breve.
Usa encabezados simples como [APERTURA], [DESARROLLO], [CIERRE] si ayudan a leerlo."""
    try:
        script = _gemini_text(prompt)
        return jsonify({"script": script, "chapters": [], "estimated_minutes": 7})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


app.view_functions["ai_script"] = ai_script_resilient


@app.post("/api/publish/youtube/start-resumable")
def start_resumable_youtube_upload():
    body = request.get_json(silent=True) or {}
    title = str(body.get("title", "")).strip()
    if not title:
        return jsonify({"error": "Falta el título"}), 400

    description = str(body.get("description", ""))
    privacy = str(body.get("privacy_status", "private"))
    if privacy not in {"private", "unlisted", "public"}:
        privacy = "private"
    tags = body.get("tags") or []
    if not isinstance(tags, list):
        tags = [x.strip() for x in str(tags).split(",") if x.strip()]
    tags = tags[:30]
    category_id = str(body.get("category_id", "22") or "22")
    channel_id = str(body.get("channel_id", "")).strip() or None
    content_type = str(body.get("content_type", "video/mp4") or "video/mp4")
    try:
        file_size = int(body.get("file_size", 0) or 0)
    except (TypeError, ValueError):
        file_size = 0
    if file_size <= 0:
        return jsonify({"error": "No se pudo determinar el tamaño del video"}), 400

    try:
        account, credentials, _ = oauth_core.authorized_youtube(channel_id)
        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {"privacyStatus": privacy},
        }
        response = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {credentials.token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(file_size),
                "X-Upload-Content-Type": content_type,
            },
            json=metadata,
            timeout=30,
            allow_redirects=False,
        )
        if response.status_code not in {200, 201}:
            try:
                detail = (response.json().get("error") or {}).get("message", "")
            except Exception:
                detail = response.text[:300]
            return jsonify({"error": f"YouTube no pudo iniciar la subida ({response.status_code}): {detail}"}), 502
        upload_url = response.headers.get("Location")
        if not upload_url:
            return jsonify({"error": "YouTube no devolvió la URL de subida resumible"}), 502
        return jsonify({
            "upload_url": upload_url,
            "channel_id": account["channel_id"],
            "privacy_status": privacy,
            "title": title,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/publish/youtube/complete-resumable")
def complete_resumable_youtube_upload():
    body = request.get_json(silent=True) or {}
    video_id = str(body.get("video_id", "")).strip()
    channel_id = str(body.get("channel_id", "")).strip()
    title = str(body.get("title", "")).strip()
    privacy = str(body.get("privacy_status", "private")).strip()
    if not video_id or not channel_id:
        return jsonify({"error": "Faltan datos de la subida completada"}), 400
    youtube_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        conn = oauth_core.db()
        conn.execute(
            "INSERT INTO published_videos(youtube_video_id,channel_id,title,privacy_status,created_at,youtube_url) VALUES(?,?,?,?,?,?)",
            (video_id, channel_id, title, privacy, datetime.now(timezone.utc).isoformat(), youtube_url),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "video_id": video_id, "youtube_url": youtube_url, "channel_id": channel_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/publish/youtube/thumbnail-small")
def upload_small_thumbnail():
    video_id = request.form.get("video_id", "").strip()
    channel_id = request.form.get("channel_id", "").strip() or None
    thumb = request.files.get("thumbnail")
    if not video_id or not thumb or not thumb.filename:
        return jsonify({"error": "Falta miniatura o video_id"}), 400
    if request.content_length and request.content_length > 3 * 1024 * 1024:
        return jsonify({"error": "La miniatura debe pesar menos de 3 MB"}), 413
    path = None
    try:
        _, _, youtube = oauth_core.authorized_youtube(channel_id)
        suffix = os.path.splitext(thumb.filename)[1] or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_thumb:
            thumb.save(temp_thumb.name)
            path = temp_thumb.name
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(path, resumable=False)).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


_DIRECT_UPLOAD_SCRIPT = r'''
<script>
(function(){
  const form=document.getElementById('publishForm');
  if(!form) return;
  form.addEventListener('submit', async function(e){
    e.preventDefault();
    e.stopImmediatePropagation();
    const video=form.elements.video.files[0];
    if(!video){ publishResult.innerHTML='<div class="notice err">Selecciona un video.</div>'; return; }
    const title=(form.elements.title.value||'').trim();
    if(!title){ publishResult.innerHTML='<div class="notice err">Falta el título.</div>'; return; }
    publishBtn.disabled=true;
    publishBtn.textContent='Preparando subida…';
    try{
      const start=await api('/api/publish/youtube/start-resumable',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          title:title,
          description:form.elements.description.value||'',
          privacy_status:form.elements.privacy_status.value||'private',
          tags:(form.elements.tags.value||'').split(',').map(x=>x.trim()).filter(Boolean),
          category_id:form.elements.category_id.value||'22',
          channel_id:publishChannelId.value||'',
          content_type:video.type||'video/mp4',
          file_size:video.size
        })
      });
      publishBtn.textContent='Subiendo directo a YouTube…';
      publishResult.innerHTML=`<div class="notice">Subiendo ${(video.size/1024/1024).toFixed(1)} MB directamente a YouTube. No cierres esta pestaña.</div>`;

      const upload=await fetch(start.upload_url,{
        method:'PUT',
        headers:{'Content-Type':video.type||'application/octet-stream','Content-Length':String(video.size)},
        body:video
      });
      let yd={};
      const raw=await upload.text();
      try{ yd=raw?JSON.parse(raw):{}; }catch(_){ throw new Error('YouTube devolvió una respuesta inesperada al finalizar la subida.'); }
      if(!upload.ok || !yd.id){
        const msg=yd?.error?.message||`YouTube respondió ${upload.status}`;
        throw new Error(msg);
      }

      const complete=await api('/api/publish/youtube/complete-resumable',{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          video_id:yd.id,channel_id:start.channel_id,title:title,privacy_status:start.privacy_status
        })
      });

      const thumb=form.elements.thumbnail.files[0];
      let thumbNote='';
      if(thumb){
        if(thumb.size<=3*1024*1024){
          const fd=new FormData(); fd.append('video_id',yd.id); fd.append('channel_id',start.channel_id); fd.append('thumbnail',thumb);
          try{ await api('/api/publish/youtube/thumbnail-small',{method:'POST',body:fd}); thumbNote=' · Miniatura aplicada'; }
          catch(err){ thumbNote=` · Video subido, pero miniatura falló: ${esc(err.message)}`; }
        }else{
          thumbNote=' · Video subido; miniatura omitida porque supera 3 MB';
        }
      }
      publishResult.innerHTML=`<div class="notice ok">Video subido correctamente${thumbNote}. ID: ${esc(yd.id)} · <a href="${esc(complete.youtube_url)}" target="_blank" style="color:#22d3a6">Abrir en YouTube</a></div>`;
      form.reset(); publishChannelId.value=start.channel_id;
    }catch(err){
      publishResult.innerHTML=`<div class="notice err">${esc(err.message)}</div>`;
    }finally{
      publishBtn.disabled=false; publishBtn.textContent='Subir a YouTube';
    }
  }, true);
})();
</script>
'''

_previous_home = app.view_functions.get("home")
def home_with_direct_upload():
    html = _previous_home()
    if isinstance(html, str):
        return html.replace("</body>", _DIRECT_UPLOAD_SCRIPT + "</body>", 1)
    return html
app.view_functions["home"] = home_with_direct_upload
