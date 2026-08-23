import os
import requests
from flask import jsonify, request

from postgres_app import app, MODEL


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
