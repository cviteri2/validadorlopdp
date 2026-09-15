#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — capa web de autoservicio para lopdp_validator.py

Un visitante anónimo sube su política de privacidad, el servidor la evalúa
contra el checklist LOPDP y devuelve el informe .docx con branding de
Protego Consulting. No se persiste ningún documento del cliente: todo se
procesa en un directorio temporal que se borra tras enviar la respuesta.

Framework: Flask (WSGI nativo). Deliberadamente NO es FastAPI/ASGI: en
PythonAnywhere (plan gratuito) uWSGI corre sin --enable-threads, así que
cualquier puente ASGI-a-WSGI basado en hilos (a2wsgi) se queda colgado en
cada request hasta que HARAKIRI mata el worker. WSGI puro no tiene ese
problema porque no depende de hilos adicionales para nada.

Variables de entorno de configuración:
    MAX_FILE_MB           tamaño máximo de archivo aceptado (default 8)
    RATE_LIMIT_PER_HOUR   solicitudes permitidas por IP y hora (default 5)
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from flask import Flask, after_this_request, jsonify, render_template, request, send_file

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import lopdp_validator as validator  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
CHECKLIST_PATH = REPO_ROOT / "checklist_lopdp.json"
BANNER_PATH = REPO_ROOT / "assets" / "protego_banner.png"
SIGNATURE_PATH = REPO_ROOT / "assets" / "protego_signature.png"

MAX_FILE_MB = float(os.environ.get("MAX_FILE_MB", "8"))
MAX_FILE_BYTES = int(MAX_FILE_MB * 1024 * 1024)
RATE_LIMIT_PER_HOUR = int(os.environ.get("RATE_LIMIT_PER_HOUR", "5"))

ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}
MAGIC_BYTES = {
    ".docx": (b"PK\x03\x04",),
    ".pdf": (b"%PDF",),
}

with open(CHECKLIST_PATH, "r", encoding="utf-8") as f:
    CHECKLIST = json.load(f)

app = Flask(__name__, static_folder=str(WEB_DIR / "static"), template_folder=str(WEB_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_BYTES + (256 * 1024)  # margen para campos de formulario

_rate_lock = threading.Lock()
_hits_by_ip = defaultdict(deque)


class ValidationError(Exception):
    def __init__(self, message: str):
        self.message = message


def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    with _rate_lock:
        hits = _hits_by_ip[client_ip]
        while hits and now - hits[0] > 3600:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_PER_HOUR:
            raise ValidationError(
                "Has alcanzado el límite de análisis gratuitos por hora desde tu conexión. "
                "Vuelve a intentarlo más tarde o contáctanos para una auditoría completa."
            )
        hits.append(now)


def _validate_upload_name(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            "Formato no soportado. Sube tu política en .docx, .pdf o .txt."
        )
    return ext


def _validate_magic_bytes(ext: str, head: bytes) -> None:
    signatures = MAGIC_BYTES.get(ext)
    if signatures and not any(head.startswith(sig) for sig in signatures):
        raise ValidationError(
            "El archivo no parece ser un " + ext.lstrip(".").upper() +
            " válido (la extensión no coincide con el contenido real)."
        )


def _run_analysis(policy_path: Path, client_name: str, output_path: Path) -> None:
    texto = validator.cargar_texto_politica(str(policy_path))
    if not texto.strip():
        raise ValidationError(
            "No se pudo extraer texto del documento. Si es un PDF escaneado como imagen, "
            "conviértelo a texto (OCR) antes de subirlo."
        )
    resultados = validator.evaluar_politica(texto, CHECKLIST)
    global_pct, resumen_categorias = validator.calcular_cumplimiento(resultados)
    recomendaciones = validator.generar_recomendaciones(resultados)
    validator.generar_informe_docx(
        output_path=str(output_path),
        cliente=client_name or "Cliente web",
        texto_fuente_nombre=policy_path.name,
        global_pct=global_pct,
        resumen_categorias=resumen_categorias,
        resultados=resultados,
        recomendaciones=recomendaciones,
        checklist_meta=CHECKLIST.get("_meta", {}),
        banner_path=str(BANNER_PATH),
        signature_path=str(SIGNATURE_PATH),
        marca=True,
    )


@app.errorhandler(413)
def too_large(_e):
    return render_template(
        "index.html", max_mb=MAX_FILE_MB,
        error=f"El archivo supera el límite de {MAX_FILE_MB:.0f} MB permitido en el análisis gratuito.",
    ), 413


@app.get("/")
def index():
    return render_template("index.html", max_mb=MAX_FILE_MB, error=None)


@app.post("/analizar")
def analizar():
    client_ip = request.remote_addr or "desconocido"

    def error_page(message: str, status_code: int = 400):
        return render_template("index.html", max_mb=MAX_FILE_MB, error=message), status_code

    if request.form.get("consiento") != "on":
        return error_page(
            "Debes confirmar que has leído el aviso sobre el alcance de este análisis "
            "antes de continuar."
        )

    politica = request.files.get("politica")
    if politica is None or not politica.filename:
        return error_page("Selecciona un archivo de política antes de enviar.")

    try:
        _check_rate_limit(client_ip)
        ext = _validate_upload_name(politica.filename)
        head = politica.stream.read(8)
        politica.stream.seek(0)
        _validate_magic_bytes(ext, head)
    except ValidationError as e:
        return error_page(e.message)

    work_dir = tempfile.mkdtemp(prefix="lopdp_")
    policy_path = Path(work_dir) / f"politica{ext}"
    politica.save(str(policy_path))
    if policy_path.stat().st_size == 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        return error_page("El archivo está vacío.")

    output_path = Path(work_dir) / f"informe_LOPDP_{uuid.uuid4().hex[:8]}.docx"
    cliente = request.form.get("cliente", "").strip()

    try:
        _run_analysis(policy_path, cliente, output_path)
    except ValidationError as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        return error_page(e.message)
    except Exception:
        app.logger.exception("fallo procesando la política subida")
        shutil.rmtree(work_dir, ignore_errors=True)
        return error_page(
            "No se pudo procesar el documento. Verifica que no esté dañado o protegido "
            "con contraseña, e inténtalo de nuevo.",
            status_code=500,
        )

    @after_this_request
    def _cleanup(response):
        shutil.rmtree(work_dir, ignore_errors=True)
        return response

    download_name = f"informe_LOPDP_{(cliente or 'cliente').replace(' ', '_')}.docx"
    return send_file(
        str(output_path),
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


if os.environ.get("GITHUB_WEBHOOK_SECRET"):
    from .deploy_webhook import bp as deploy_webhook_bp
    app.register_blueprint(deploy_webhook_bp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
