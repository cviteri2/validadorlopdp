#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deploy_webhook.py — recibe el webhook "push" de GitHub, actualiza el
checkout local (git fetch + reset --hard) e instala dependencias, y le
pide a la API de PythonAnywhere que recargue la web app.

Solo se activa si GITHUB_WEBHOOK_SECRET está definido en el entorno — así,
en Docker/local (donde no se define) esta ruta ni siquiera se monta.

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
import threading
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, Response

logger = logging.getLogger("deploy_webhook")

REPO_ROOT = Path(__file__).resolve().parent.parent

WEBHOOK_SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
PA_API_TOKEN = os.environ.get("PYTHONANYWHERE_API_TOKEN", "")
PA_USERNAME = os.environ.get("PYTHONANYWHERE_USERNAME", "")
PA_DOMAIN = os.environ.get("PYTHONANYWHERE_DOMAIN", f"{PA_USERNAME}.pythonanywhere.com")
PA_API_HOST = os.environ.get("PYTHONANYWHERE_API_HOST", "www.pythonanywhere.com")
DEPLOY_BRANCH = os.environ.get("DEPLOY_BRANCH", "main")
GIT_TIMEOUT_SEC = 120

router = APIRouter()


def _verify_signature(raw_body: bytes, signature_header: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, received)


def _run(cmd: list) -> None:
    logger.info("deploy: ejecutando %s", " ".join(cmd))
    result = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=GIT_TIMEOUT_SEC
    )
    if result.stdout:
        logger.info("deploy stdout: %s", result.stdout.strip())
    if result.returncode != 0:
        logger.error("deploy stderr: %s", result.stderr.strip())
        raise RuntimeError(f"'{' '.join(cmd)}' salió con código {result.returncode}")


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


def _deploy_in_background() -> None:
    try:
        _run(["git", "fetch", "origin", DEPLOY_BRANCH])
        _run(["git", "reset", "--hard", f"origin/{DEPLOY_BRANCH}"])
        pip = [sys.executable, "-m", "pip", "install", "--quiet", "-r", "requirements.txt"]
        _run(pip)
    except Exception:
        logger.exception("deploy: fallo actualizando el código, se mantiene la versión actual en ejecución")
        return
    _reload_webapp()


@router.post("/deploy/webhook")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
):
    if not WEBHOOK_SECRET:
        raise HTTPException(status_code=501, detail="Webhook no configurado en este servidor")

    raw_body = await request.body()
    if not _verify_signature(raw_body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Firma inválida")

    if x_github_event == "ping":
        return {"status": "pong"}

    if x_github_event != "push":
        return Response(status_code=204)

    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Payload inválido")

    if payload.get("ref") != f"refs/heads/{DEPLOY_BRANCH}":
        return {"status": "ignorado", "motivo": "rama distinta a DEPLOY_BRANCH", "ref": payload.get("ref")}

    threading.Thread(target=_deploy_in_background, daemon=True).start()
    return Response(status_code=202)
