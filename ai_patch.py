import json
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
        return jsonify({"script": _gemini_text(prompt), "chapters": [], "estimated_minutes": 7})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


app.view_functions["ai_script"] = ai_script_resilient


def _encode_upload_token(payload):
    return oauth_core.cipher().encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decode_upload_token(token):
    try:
        raw = oauth_core.cipher().decrypt(token.encode(), ttl=60 * 60 * 6).decode()
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError("La sesión de subida expiró. Inicia la subida nuevamente.") from exc


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

        token = _encode_upload_token({
            "upload_url": upload_url,
            "channel_id": account["channel_id"],
            "privacy_status": privacy,
            "title": title,
            "content_type": content_type,
            "total": file_size,
        })
        return jsonify({
            "upload_token": token,
            "channel_id": account["channel_id"],
            "privacy_status": privacy,
            "title": title,
            "chunk_size": 3 * 1024 * 1024,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@app.post("/api/publish/youtube/upload-chunk")
def upload_youtube_chunk():
    token = request.headers.get("X-Upload-Token", "").strip()
    if not token:
        return jsonify({"error": "Falta la sesión de subida"}), 400
    try:
        info = _decode_upload_token(token)
        start = int(request.headers.get("X-Chunk-Start", "-1"))
        end = int(request.headers.get("X-Chunk-End", "-1"))
        total = int(request.headers.get("X-Upload-Total", "-1"))
        expected_total = int(info.get("total", 0))
        if start < 0 or end < start or total <= 0 or total != expected_total:
            return jsonify({"error": "Rango de subida inválido"}), 400

        chunk = request.get_data(cache=False)
        expected_len = end - start + 1
        if len(chunk) != expected_len:
            return jsonify({"error": f"Chunk incompleto: llegaron {len(chunk)} bytes y se esperaban {expected_len}"}), 400
        if len(chunk) > 3 * 1024 * 1024:
            return jsonify({"error": "El bloque de video supera el límite permitido"}), 413

        headers = {
            "Content-Type": info.get("content_type") or "application/octet-stream",
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{total}",
        }
        response = requests.put(info["upload_url"], headers=headers, data=chunk, timeout=75)

        if response.status_code == 308:
            server_range = response.headers.get("Range", "")
            return jsonify({"ok": True, "done": False, "range": server_range, "next": end + 1})

        if response.status_code in {200, 201}:
            try:
                payload = response.json()
            except Exception:
                payload = {}
            video_id = payload.get("id")
            if not video_id:
                return jsonify({"error": "YouTube completó la transferencia pero no devolvió el ID del video"}), 502
            return jsonify({"ok": True, "done": True, "video_id": video_id})

        try:
            detail = (response.json().get("error") or {}).get("message", "")
        except Exception:
            detail = response.text[:300]
        return jsonify({"error": f"YouTube rechazó un bloque ({response.status_code}): {detail or 'sin detalle'}"}), 502
    except requests.RequestException as exc:
        return jsonify({"error": f"No se pudo enviar este bloque a YouTube: {exc}"}), 502
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
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(path, resumable=False),
        ).execute()
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


_CHUNKED_UPLOAD_SCRIPT = r'''
<script>
(function(){
  const form=document.getElementById('publishForm');
  if(!form) return;

  async function sendChunk(token, blob, start, end, total, attempt=1){
    try{
      return await api('/api/publish/youtube/upload-chunk',{
        method:'POST',
        headers:{
          'Content-Type':'application/octet-stream',
          'X-Upload-Token':token,
          'X-Chunk-Start':String(start),
          'X-Chunk-End':String(end),
          'X-Upload-Total':String(total)
        },
        body:blob
      });
    }catch(err){
      if(attempt<3){
        await new Promise(r=>setTimeout(r,800*attempt));
        return sendChunk(token,blob,start,end,total,attempt+1);
      }
      throw err;
    }
  }

  async function uploadInChunks(startInfo, file){
    const chunkSize=startInfo.chunk_size||3145728;
    let offset=0;
    let videoId='';
    while(offset<file.size){
      const endExclusive=Math.min(offset+chunkSize,file.size);
      const end=endExclusive-1;
      const chunk=file.slice(offset,endExclusive);
      const result=await sendChunk(startInfo.upload_token,chunk,offset,end,file.size);
      offset=endExclusive;
      const pct=Math.max(1,Math.min(100,Math.round((offset/file.size)*100)));
      publishBtn.textContent=`Subiendo ${pct}%`;
      publishResult.innerHTML=`<div class="notice">Subiendo ${(file.size/1024/1024).toFixed(1)} MB a YouTube por bloques seguros · ${pct}%</div>`;
      if(result.done){ videoId=result.video_id||''; break; }
    }
    if(!videoId) throw new Error('La transferencia terminó sin recibir el ID del video.');
    return videoId;
  }

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
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          title,
          description:form.elements.description.value||'',
          privacy_status:form.elements.privacy_status.value||'private',
          tags:(form.elements.tags.value||'').split(',').map(x=>x.trim()).filter(Boolean),
          category_id:form.elements.category_id.value||'22',
          channel_id:publishChannelId.value||'',
          content_type:video.type||'video/mp4',
          file_size:video.size
        })
      });

      const videoId=await uploadInChunks(start,video);
      publishBtn.textContent='Finalizando…';
      const complete=await api('/api/publish/youtube/complete-resumable',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          video_id:videoId,
          channel_id:start.channel_id,
          title,
          privacy_status:start.privacy_status
        })
      });

      const thumb=form.elements.thumbnail.files[0];
      let thumbNote='';
      if(thumb){
        if(thumb.size<=3*1024*1024){
          const fd=new FormData();
          fd.append('video_id',videoId);
          fd.append('channel_id',start.channel_id);
          fd.append('thumbnail',thumb);
          try{
            await api('/api/publish/youtube/thumbnail-small',{method:'POST',body:fd});
            thumbNote=' · Miniatura aplicada';
          }catch(err){
            thumbNote=` · Video subido, pero miniatura falló: ${esc(err.message)}`;
          }
        }else{
          thumbNote=' · Video subido; miniatura omitida porque supera 3 MB';
        }
      }

      publishResult.innerHTML=`<div class="notice ok">Video subido correctamente${thumbNote}. ID: ${esc(videoId)} · <a href="${esc(complete.youtube_url)}" target="_blank" style="color:#22d3a6">Abrir en YouTube</a></div>`;
      form.reset();
      publishChannelId.value=start.channel_id;
    }catch(err){
      publishResult.innerHTML=`<div class="notice err">${esc(err.message)}</div>`;
    }finally{
      publishBtn.disabled=false;
      publishBtn.textContent='Subir a YouTube';
    }
  }, true);
})();
</script>
'''

_previous_home = app.view_functions.get("home")
def home_with_chunked_upload():
    html = _previous_home()
    if isinstance(html, str):
        return html.replace("</body>", _CHUNKED_UPLOAD_SCRIPT + "</body>", 1)
    return html

app.view_functions["home"] = home_with_chunked_upload
