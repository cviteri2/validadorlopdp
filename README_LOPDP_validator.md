# Validador de Políticas de Protección de Datos vs. LOPDP

<!-- prueba del webhook de auto-deploy: este comentario se puede borrar -->

## Instalación (una sola vez)
```
pip install python-docx pdfplumber pypdf
```

## Estructura de carpeta (no muevas nada de sitio)
```
validador/
├── lopdp_validator.py
├── checklist_lopdp.json
└── assets/
    ├── protego_banner.png       (cabecera de portada)
    └── protego_signature.png    (tarjeta de firma de cierre)
```
El script busca `assets/protego_banner.png` y `assets/protego_signature.png` en la MISMA carpeta
donde está `lopdp_validator.py`. Si mueves el .py, mueve la carpeta `assets` con él.

## Uso
```
python lopdp_validator.py --policy "ruta/politica_cliente.docx" --client "Nombre del Cliente"
```

Formatos de `--policy` soportados: `.docx`, `.pdf`, `.txt`.

Cada informe sale automáticamente con el branding de Protego Consulting: banner de portada,
pie de página en todas las hojas y tarjeta de firma/contacto al cierre — igual que el
informe de referencia de CESSIO.

Parámetros opcionales:
- `--checklist checklist_lopdp.json` → ruta al checklist (por defecto usa el que está en la misma carpeta).
- `--output informe.docx` → nombre del informe de salida (por defecto: `<nombre_politica>_informe_LOPDP.docx`).
- `--json-report resultados.json` → exporta también los resultados crudos en JSON.
- `--no-brand` → genera el informe SIN el branding de Protego (documento neutro, por si algún día lo necesitas white-label).
- `--banner ruta.png` / `--signature ruta.png` → usar otras imágenes de marca en vez de las por defecto.

## Editar el checklist
`checklist_lopdp.json` es la fuente de verdad. Cada ítem tiene:
- `patrones`: lista de palabras/frases o regex a buscar (edítalas libremente, agrega sinónimos).
- `modo`: `"any"` (basta 1 coincidencia) o `"all"` (deben aparecer todas; si solo aparecen algunas → PARCIAL).
- `peso`: importancia relativa en el % ponderado.
- `obligatorio`: true/false — afecta solo la prioridad de la recomendación (ALTA/MEDIA/BAJA), no el cálculo del %.

## Límite real de esta herramienta
Es un cribado por palabras clave, no una lectura semántica de la política. "CUMPLE" significa que el texto
menciona razonablemente el tema — no certifica que el contenido sea correcto o completo. Úsalo como primer
filtro antes de tu revisión manual como DPO.

## Versión web de autoservicio (`web/`)
Además del CLI, hay una capa web (Flask) en `web/app.py` para que un visitante suba su política y
descargue el informe sin intervención manual. Reutiliza directamente las funciones de
`lopdp_validator.py` — el checklist y la lógica de evaluación son la misma fuente de verdad.

**Por qué Flask y no FastAPI**: la primera versión usaba FastAPI (ASGI) envuelto con `a2wsgi` para
poder correr en PythonAnywhere. En producción eso se colgaba en *cada* request (HARAKIRI de uWSGI a
los pocos minutos) porque `a2wsgi` necesita un hilo interno para conectar el mundo async con WSGI, y
uWSGI en el plan gratuito de PythonAnywhere corre sin `--enable-threads` (no es configurable ahí) —
ese hilo nunca se programa y la petición espera para siempre. Flask es WSGI nativo: no necesita
ningún adaptador ni hilos adicionales, así que ese problema desaparece por diseño, no por parche.

Decisiones de diseño relevantes para producción:
- **No se persiste nada del cliente**: cada solicitud usa un directorio temporal que se borra
  (`shutil.rmtree`) justo después de enviar la respuesta (`flask.after_this_request`), incluso si el
  procesamiento falla.
- **Límite de tamaño** (`MAX_FILE_MB`, default 8 MB) vía `MAX_CONTENT_LENGTH` de Flask — Werkzeug
  rechaza la petición (413) antes de terminar de leer un archivo demasiado grande.
- **Verificación de magic bytes**, no solo la extensión del archivo, para rechazar archivos
  renombrados que no coinciden con su contenido real.
- **Rate limiting básico por IP** (`RATE_LIMIT_PER_HOUR`, default 5) en memoria — válido para una
  sola instancia; si se despliega con varias réplicas hay que mover esto a Redis o similar.
- **Aviso legal obligatorio** antes de subir el archivo (checkbox de consentimiento validado también
  en el servidor), para que quede claro que el resultado es un cribado documental, no un dictamen legal.
- **Sin timeout de aplicación explícito**: se apoya en el límite de tamaño de archivo + el timeout del
  propio servidor (`--timeout 30` en gunicorn/Docker, el HARAKIRI de uWSGI en PythonAnywhere) como
  respaldo, en vez de un timeout implementado a mano — cualquier mecanismo basado en hilos o señales
  tiene el mismo riesgo de plan gratuito que ya causó el problema de arriba.

### Ejecutar en local
```
pip install -r requirements.txt
python web/app.py
```
Abre `http://localhost:8000`. (`FLASK_DEBUG=1 python web/app.py` para autorecarga en desarrollo.)

### Desplegar con Docker
```
docker build -t validador-lopdp .
docker run -p 8000:8000 --memory=512m --cpus=1 validador-lopdp
```
Corre con `gunicorn` (`--timeout 30`, 2 workers). El límite de memoria/CPU del contenedor (`--memory`,
`--cpus`) es la última línea de defensa contra un archivo malicioso que intente agotar recursos durante
el parseo de PDF/DOCX — se recomienda fijarlo también en el orquestador de producción (Render, Railway,
Fly.io, ECS, etc.), no solo en local.

### Desplegar en PythonAnywhere (cuenta gratuita, Python 3.10)
Al ser Flask (WSGI nativo), PythonAnywhere la sirve directamente — sin adaptadores. Probado localmente
subiendo un archivo real a través de un servidor WSGI de un solo hilo (`wsgiref`, simulando el uWSGI
sin `--enable-threads` de PythonAnywhere) antes de documentar estos pasos, precisamente para no repetir
el problema de la versión con FastAPI. El repositorio es público, así que `git clone`/`git pull`
funcionan sin credenciales — `github.com` está en la lista blanca de sitios permitidos para cuentas
gratuitas de PythonAnywhere.

**1. Consola Bash** (pestaña "Consoles" → "Bash"):
```
git clone https://github.com/cviteri2/validadorlopdp.git
cd validadorlopdp
mkvirtualenv --python=/usr/bin/python3.10 validador-env
pip install -r requirements.txt
```

**2. Pestaña "Web"** → "Add a new web app" → **salta el asistente de dominio** (usa el que te dan
gratis) → **"Manual configuration"** → elige **Python 3.10**.

**3. "Virtualenv"**: escribe `/home/TU_USUARIO/.virtualenvs/validador-env` y confirma con el check ✓.

**4. "Code" → WSGI configuration file**: click en el link del archivo, borra todo su contenido y
pega el de `deploy/pythonanywhere_wsgi.py.example`, cambiando `<USERNAME>` por tu usuario real. Si no
vas a activar el webhook del paso 7 ahora, deja las líneas de `os.environ[...]` comentadas tal cual.

**5. "Static files"**: añade el mapeo URL `/static/` → Directory
`/home/TU_USUARIO/validadorlopdp/web/static/`.

**6. Click "Reload"** (botón verde arriba de la pestaña Web). Tu validador queda en
`https://TU_USUARIO.pythonanywhere.com`.

**7. Auto-deploy con webhook de GitHub (opcional pero es lo que pediste):**

La app ya trae una ruta `/deploy/webhook` que, al recibir un push de GitHub con la firma correcta,
hace `git fetch` + `git reset --hard` sobre la rama configurada, reinstala `requirements.txt` y le
pide a la API de PythonAnywhere que recargue la web app — sin que tengas que entrar manualmente.
Esa ruta **no existe** en el servidor a menos que definas `GITHUB_WEBHOOK_SECRET`, así que hasta que
sigas estos pasos no hay ninguna superficie expuesta de más.

7.1. **Generar el secreto y el token**:
   - Secreto del webhook: cualquier cadena larga y aleatoria tuya, por ejemplo con
     `python3 -c "import secrets; print(secrets.token_hex(32))"` en la consola Bash de PythonAnywhere.
   - Token de API: pestaña **"Account" → "API Token"** en PythonAnywhere → "Create a new API token".

7.2. **Pegar ambos en el WSGI file** (mismo archivo del paso 4), descomentando y completando:
   ```python
   os.environ["GITHUB_WEBHOOK_SECRET"] = "el-secreto-que-generaste"
   os.environ["PYTHONANYWHERE_API_TOKEN"] = "el-token-de-la-pestaña-Account"
   os.environ["PYTHONANYWHERE_USERNAME"] = "TU_USUARIO"
   os.environ["DEPLOY_BRANCH"] = "main"
   ```
   Guarda y da clic en **"Reload"** en la pestaña Web para que tome estas variables.

7.3. **Crear el webhook en GitHub**: en el repo, `Settings` → `Webhooks` → `Add webhook`:
   - Payload URL: `https://TU_USUARIO.pythonanywhere.com/deploy/webhook`
   - Content type: `application/json`
   - Secret: el mismo valor exacto que pusiste en `GITHUB_WEBHOOK_SECRET`
   - "Which events": *Just the push event*
   - Guardar. GitHub manda un evento `ping` inmediatamente — en "Recent Deliveries" debe verse
     respuesta `200` con `{"status":"pong"}`.

7.4. **Probar de verdad**: haz un commit y `git push` a `main`. En unos segundos, revisa la pestaña
   "Web" → "Log files" → "Error log" de PythonAnywhere: deberías ver las líneas `deploy: ejecutando
   git fetch...`, `git reset --hard...`, `pip install...` y la recarga solicitada.

**Importante**: con el webhook activo, el checkout en PythonAnywhere pasa a actualizarse solo en cada
push a `main` (`git reset --hard` descarta cualquier edición manual hecha ahí directamente) — todo
cambio de código debe llegar vía `git push`, nunca editando archivos a mano en el servidor.

**Actualizar sin webhook**: si no lo activas, hazlo manual — `git pull` en la consola Bash + botón
"Reload" en la pestaña Web cada vez que quieras publicar cambios.

Limitación del plan gratuito de PythonAnywhere: la URL es fija en `TU_USUARIO.pythonanywhere.com`,
**no puedes usar un subdominio propio** como `validador.protego-consulting.com` — eso requiere el
plan "Hacker" (de pago, con dominio propio soportado).

### Presentarlo en un sitio estático (el caso de protego-consulting.com)
Un sitio estático en Git (GitHub Pages, Netlify, Cloudflare Pages, etc.) **no puede ejecutar Python**:
solo sirve HTML/CSS/JS. El validador tiene que vivir aparte (p. ej. en PythonAnywhere, como arriba) y
el sitio estático solo necesita **enlazarlo**, no incrustar su código.

Recomendado: un botón/enlace que abra el validador en pestaña nueva, no un `<iframe>` — el formulario
incluye una subida de archivo y un aviso legal que se leen mejor a pantalla completa, y evitas
problemas de cabeceras de seguridad (`X-Frame-Options`) que muchos hosts añaden por defecto.

```html
<a href="https://TU_USUARIO.pythonanywhere.com/" target="_blank" rel="noopener"
   class="boton-cta">
  Valida gratis tu Política de Privacidad (LOPDP)
</a>
```

### Pendiente antes de publicar en protego-consulting.com
1. Elegir el hosting definitivo del backend (PythonAnywhere gratuito para validar la idea, o el plan
   "Hacker"/Docker en Render-Railway-Fly.io si luego quieres el subdominio propio) — este repo no
   incluye el hosting del sitio principal.
2. Confirmar que el sitio estático queda servido en HTTPS y que el enlace al validador también lo está
   (PythonAnywhere ya sirve `https://` por defecto).
3. Revisar el enlace de contacto en `web/templates/index.html` (`https://protego-consulting.com/`)
   y apuntarlo a la página de contacto real cuando exista.
