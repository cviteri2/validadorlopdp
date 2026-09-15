#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — capa web de autoservicio para lopdp_validator.py

Un visitante anónimo sube su política de privacidad, el servidor la evalúa
contra el checklist LOPDP y devuelve el informe .docx con branding de
Protego Consulting. No se persiste ningún documento del cliente: todo se
procesa en un directorio temporal que se borra tras enviar la respuesta.

Variables de entorno de configuración:
    MAX_FILE_MB           tamaño máximo de archivo aceptado (default 8)
    PROCESS_TIMEOUT_SEC   tiempo máximo de procesamiento (default 25)
    RATE_LIMIT_PER_HOUR   solicitudes permitidas por IP y hora (default 5)
"""

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import lopdp_validator as validator  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent
CHECKLIST_PATH = REPO_ROOT / "checklist_lopdp.json"
BANNER_PATH = REPO_ROOT / "assets" / "protego_banner.png"
SIGNATURE_PATH = REPO_ROOT / "assets" / "protego_signature.png"

MAX_FILE_MB = float(os.environ.get("MAX_FILE_MB", "8"))
MAX_FILE_BYTES = int(MAX_FILE_MB * 1024 * 1024)
PROCESS_TIMEOUT_SEC = float(os.environ.get("PROCESS_TIMEOUT_SEC", "25"))
RATE_LIMIT_PER_HOUR = int(os.environ.get("RATE_LIMIT_PER_HOUR", "5"))

ALLOWED_EXTENSIONS = {".docx", ".pdf", ".txt"}
MAGIC_BYTES = {
    ".docx": (b"PK\x03\x04",),
    ".pdf": (b"%PDF",),
}

with open(CHECKLIST_PATH, "r", encoding="utf-8") as f:
    CHECKLIST = json.load(f)

app = FastAPI(title="Validador LOPDP — Protego Consulting")
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))

_rate_lock = asyncio.Lock()
_hits_by_ip = defaultdict(deque)


class ValidationError(Exception):
    def __init__(self, message: str):
        self.message = message


async def _check_rate_limit(client_ip: str) -> None:
    now = time.time()
    async with _rate_lock:
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


async def _read_upload_within_limit(upload: UploadFile) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise ValidationError(
                f"El archivo supera el límite de {MAX_FILE_MB:.0f} MB permitido en el análisis gratuito."
            )
        chunks.append(chunk)
    return b"".join(chunks)


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


def _cleanup(directory: str) -> None:
    import shutil
    shutil.rmtree(directory, ignore_errors=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "max_mb": MAX_FILE_MB, "error": None},
    )


@app.post("/analizar")
async def analizar(
    request: Request,
    cliente: str = Form(default=""),
    consiento: str = Form(default=""),
    politica: UploadFile = None,
):
    client_ip = request.client.host if request.client else "desconocido"

    def error_page(message: str, status_code: int = 400):
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "max_mb": MAX_FILE_MB, "error": message},
            status_code=status_code,
        )

    if consiento != "on":
        return error_page(
            "Debes confirmar que has leído el aviso sobre el alcance de este análisis "
            "antes de continuar."
        )

    if politica is None or not politica.filename:
        return error_page("Selecciona un archivo de política antes de enviar.")

    try:
        await _check_rate_limit(client_ip)
        ext = _validate_upload_name(politica.filename)
        contenido = await _read_upload_within_limit(politica)
        if not contenido:
            raise ValidationError("El archivo está vacío.")
        _validate_magic_bytes(ext, contenido[:8])
    except ValidationError as e:
        return error_page(e.message)

    work_dir = tempfile.mkdtemp(prefix="lopdp_")
    policy_path = Path(work_dir) / f"politica{ext}"
    policy_path.write_bytes(contenido)
    output_path = Path(work_dir) / f"informe_LOPDP_{uuid.uuid4().hex[:8]}.docx"

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_run_analysis, policy_path, cliente.strip(), output_path),
            timeout=PROCESS_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        _cleanup(work_dir)
        return error_page(
            "El documento tardó demasiado en procesarse. Prueba con un archivo más simple "
            "o contáctanos para revisarlo manualmente.",
            status_code=504,
        )
    except ValidationError as e:
        _cleanup(work_dir)
        return error_page(e.message)
    except Exception:
        _cleanup(work_dir)
        return error_page(
            "No se pudo procesar el documento. Verifica que no esté dañado o protegido "
            "con contraseña, e inténtalo de nuevo.",
            status_code=500,
        )

    download_name = f"informe_LOPDP_{(cliente.strip() or 'cliente').replace(' ', '_')}.docx"
    return FileResponse(
        path=str(output_path),
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        background=BackgroundTask(_cleanup, work_dir),
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
