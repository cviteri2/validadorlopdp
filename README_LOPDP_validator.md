# Validador de Políticas de Protección de Datos vs. LOPDP

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
Además del CLI, hay una capa web (FastAPI) en `web/app.py` para que un visitante suba su política y
descargue el informe sin intervención manual. Reutiliza directamente las funciones de
`lopdp_validator.py` — el checklist y la lógica de evaluación son la misma fuente de verdad.

Decisiones de diseño relevantes para producción:
- **No se persiste nada del cliente**: cada solicitud usa un directorio temporal que se borra
  (`shutil.rmtree`) justo después de enviar la respuesta, incluso si el procesamiento falla.
- **Límite de tamaño** (`MAX_FILE_MB`, default 8 MB) y **timeout de procesamiento**
  (`PROCESS_TIMEOUT_SEC`, default 25s) para evitar archivos que agoten memoria/CPU.
- **Verificación de magic bytes**, no solo la extensión del archivo, para rechazar archivos
  renombrados que no coinciden con su contenido real.
- **Rate limiting básico por IP** (`RATE_LIMIT_PER_HOUR`, default 5) en memoria — válido para una
  sola instancia; si se despliega con varias réplicas hay que mover esto a Redis o similar.
- **Aviso legal obligatorio** antes de subir el archivo (checkbox de consentimiento validado también
  en el servidor), para que quede claro que el resultado es un cribado documental, no un dictamen legal.

### Ejecutar en local
```
pip install -r requirements.txt
uvicorn web.app:app --reload --port 8000
```
Abre `http://localhost:8000`.

### Desplegar con Docker
```
docker build -t validador-lopdp .
docker run -p 8000:8000 --memory=512m --cpus=1 validador-lopdp
```
El límite de memoria/CPU del contenedor (`--memory`, `--cpus`) es la última línea de defensa contra un
archivo malicioso que intente agotar recursos durante el parseo de PDF/DOCX — se recomienda fijarlo
también en el orquestador de producción (Render, Railway, Fly.io, ECS, etc.), no solo en local.

### Pendiente antes de publicar en protego-consulting.com
1. Elegir dónde vive el contenedor (subdominio propio tipo `validador.protego-consulting.com`,
   o un iframe embebido en el sitio actual) — este repo no incluye el hosting del sitio principal.
2. Poner el servicio detrás de HTTPS y, si el volumen lo justifica, un WAF o Cloudflare para
   mitigar abuso más allá del rate limit en memoria.
3. Revisar el enlace de contacto en `web/templates/index.html` (`https://protego-consulting.com/`)
   y apuntarlo a la página de contacto real cuando exista.
