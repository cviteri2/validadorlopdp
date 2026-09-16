#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_webhook.py — recibe el webhook "push" de GitHub, actualiza el
checkout local (git fetch + reset --hard) e instala dependencias, y le
pide a la API de PythonAnywhere que recargue la web app.

Solo se registra si GITHUB_WEBHOOK_SECRET está definido en el entorno — así,
en Docker/local (donde no se define) esta ruta ni siquiera existe.

Se ejecuta TODO de forma síncrona dentro de la misma request, sin hilos:
en PythonAnywhere (plan gratuito) uWSGI corre sin --enable-threads, así que
un hilo en segundo plano lanzado por la propia app nunca llegaría a
ejecutarse. Como consecuencia, GitHub puede marcar la entrega del webhook
como "lenta" o "timeout" en su UI si el pull + pip install tardan más de
~10s — es cosmético, el despliegue ya se ejecutó igual del lado del
servidor antes de intentar escribir la respuesta.

IMPORTANTE: el checkout en el servidor pasa a ser de solo lectura para
humanos. Un `git reset --hard` en cada push descarta cualquier edición
manual hecha directamente ahí — el código siempre debe llegar vía git push.
"""

import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

logger = logging.getLogger("deploy_webhook")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _venv_python() -> str:
    """Ruta a un intérprete python de verdad, no a sys.executable.

    Bajo un proceso uWSGI con Python embebido, sys.executable es el propio
    binario de uwsgi (que trae su intérprete adentro), no un `python3`
    utilizable como comando — `uwsgi -m pip ...` falla porque uwsgi
    interpreta esos argumentos como sus propias opciones de CLI. sys.prefix
    sí apunta correctamente al virtualenv activo en cualquier entorno
    (embebido o no), así que buscamos el binario ahí.
    """
    for name in ("python3", "python"):
        candidate = Path(sys.prefix) / "bin" / name
        if candidate.exists():
            return str(candidate)
    return sys.executable

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
PA_API_TOKEN = os.environ.get("PYTHONANYWHERE_API_TOKEN", "")
PA_USERNAME = os.environ.get("PYTHONANYWHERE_USERNAME", "")
PA_DOMAIN = os.environ.get("PYTHONANYWHERE_DOMAIN", f"{PA_USERNAME}.pythonanywhere.com")
PA_API_HOST = os.environ.get("PYTHONANYWHERE_API_HOST", "www.pythonanywhere.com")
DEPLOY_BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
GIT_TIMEOUT_SEC = 90

bp = Blueprint("deploy_webhook", __name__)


def _verify_signature(raw_body: bytes, signature_header: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def _run(cmd: list) -> str:
    logger.info("deploy: ejecutando %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            f"no se pudo ejecutar '{cmd[0]}' en cwd={REPO_ROOT} "
            f"(PATH={os.environ.get('PATH')!r}): {e}"
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"'{' '.join(cmd)}' salió con código {result.returncode}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _reload_webapp() -> None:
    if not (PA_API_TOKEN and PA_USERNAME):
        logger.warning("deploy: PYTHONANYWHERE_API_TOKEN/USERNAME no configurados, no se recarga la web app")
        return
    url = f"https://{PA_API_HOST}/api/v0/user/{PA_USERNAME}/webapps/{PA_DOMAIN}/reload/"
    req = urllib.request.Request(url, method="POST", headers={"Authorization": f"Token {PA_API_TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            logger.info("deploy: recarga solicitada, status %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.error("deploy: fallo al recargar la web app (%s): %s", e.code, e.read().decode(errors="ignore"))
    except urllib.error.URLError as e:
        logger.error("deploy: fallo de red al recargar la web app: %s", e)


def _deploy() -> tuple:
    try:
        _run(["git", "fetch", "origin", DEPLOY_BRANCH])
        _run(["git", "reset", "--hard", f"origin/{DEPLOY_BRANCH}"])
        pip = [_venv_python(), "-m", "pip", "install", "--quiet", "-r", "requirements.txt"]
        _run(pip)
    except Exception as e:
        return False, str(e)
    _reload_webapp()
    return True, None


@bp.post("/deploy/webhook")
def github_webhook():
    if not WEBHOOK_SECRET:
        return jsonify({"detail": "Webhook no configurado en este servidor"}), 501

    raw_body = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(raw_body, signature):
        return jsonify({"detail": "Firma inválida"}), 401

    event = request.headers.get("X-GitHub-Event", "")
    if event == "ping":
        return jsonify({"status": "pong"})

    if event != "push":
        return Response(status=204)

    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        return jsonify({"detail": "Payload inválido"}), 400

    if payload.get("ref") != f"refs/heads/{DEPLOY_BRANCH}":
        return jsonify({"status": "ignorado", "motivo": "rama distinta a DEPLOY_BRANCH", "ref": payload.get("ref")})

    ok, error = _deploy()
    if ok:
        return jsonify({"status": "desplegado"})
    return jsonify({"status": "fallo", "error": error}), 500
