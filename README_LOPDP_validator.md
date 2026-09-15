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
