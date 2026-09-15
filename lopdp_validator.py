#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lopdp_validator.py
-------------------
Verifica una política/aviso de privacidad frente a un checklist de requisitos
de la LOPDP (Ecuador) y genera un informe en Word (.docx) con:
  - % de cumplimiento global y por categoría
  - Detalle punto a punto (cumple / parcial / no cumple)
  - Recomendaciones de mejora priorizadas

USO:
    python lopdp_validator.py --policy "ruta/politica_cliente.docx" \
                               --client "Nombre del Cliente" \
                               [--checklist checklist_lopdp.json] \
                               [--output informe_cumplimiento.docx] \
                               [--json-report resultados.json]

Formatos de entrada soportados para --policy: .docx, .pdf, .txt

IMPORTANTE (léase antes de confiar en el resultado):
Este script hace una búsqueda de palabras clave / expresiones regulares sobre
el TEXTO de la política. Es un cribado documental de los deberes de
información y transparencia de la LOPDP, no una auditoría legal integral.
Un ítem marcado "CUMPLE" solo indica que el texto contiene una mención
razonable al tema — no valida que el contenido sea sustantivamente correcto,
completo o coherente con el RAT/EIPDP real del cliente. La revisión final
sigue siendo responsabilidad del DPO/consultor.

Dependencias: python-docx, pdfplumber (opcional para PDF), pypdf (fallback PDF)
    pip install python-docx pdfplumber pypdf
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BANNER = os.path.join(SCRIPT_DIR, "assets", "protego_banner.png")
DEFAULT_SIGNATURE = os.path.join(SCRIPT_DIR, "assets", "protego_signature.png")

# ---------------------------------------------------------------------------
# Extracción de texto
# ---------------------------------------------------------------------------

def extraer_texto_docx(path):
    from docx import Document
    doc = Document(path)
    partes = []
    for p in doc.paragraphs:
        if p.text.strip():
            partes.append(p.text)
    for tabla in doc.tables:
        for fila in tabla.rows:
            for celda in fila.cells:
                if celda.text.strip():
                    partes.append(celda.text)
    return "\n".join(partes)


def extraer_texto_pdf(path):
    texto = []
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for pagina in pdf.pages:
                t = pagina.extract_text() or ""
                texto.append(t)
        if any(t.strip() for t in texto):
            return "\n".join(texto)
    except Exception:
        pass
    # Fallback: pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        for pagina in reader.pages:
            texto.append(pagina.extract_text() or "")
    except Exception as e:
        raise RuntimeError(f"No se pudo extraer texto del PDF ({path}): {e}")
    return "\n".join(texto)


def extraer_texto_txt(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def cargar_texto_politica(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return extraer_texto_docx(path)
    elif ext == ".pdf":
        return extraer_texto_pdf(path)
    elif ext in (".txt", ".md"):
        return extraer_texto_txt(path)
    else:
        raise ValueError(
            f"Formato no soportado: '{ext}'. Usa .docx, .pdf o .txt."
        )


def normalizar(texto):
    """minúsculas + sin tildes, para que los patrones no dependan de acentos."""
    texto = texto.lower()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\s+", " ", texto)
    return texto


# ---------------------------------------------------------------------------
# Motor de verificación
# ---------------------------------------------------------------------------

def buscar_patron(patron, texto_normalizado):
    """Cada patrón puede ser texto simple o regex; probamos como regex
    (word-ish) tolerante. Si el patrón no es regex válido, se busca literal."""
    patron_norm = normalizar(patron)
    try:
        return re.search(patron_norm, texto_normalizado) is not None
    except re.error:
        return patron_norm in texto_normalizado


def evaluar_item(item, texto_normalizado):
    patrones = item.get("patrones", [])
    if not patrones:
        return {"estado": "NO_CUMPLE", "fraccion": 0.0, "coincidencias": []}

    encontrados = [p for p in patrones if buscar_patron(p, texto_normalizado)]
    modo = item.get("modo", "any")

    if modo == "all":
        fraccion = len(encontrados) / len(patrones)
    else:  # "any"
        fraccion = 1.0 if encontrados else 0.0

    if fraccion >= 0.999:
        estado = "CUMPLE"
    elif fraccion > 0:
        estado = "PARCIAL"
    else:
        estado = "NO_CUMPLE"

    return {"estado": estado, "fraccion": round(fraccion, 2), "coincidencias": encontrados}


def evaluar_politica(texto, checklist):
    texto_norm = normalizar(texto)
    resultados = []
    for item in checklist["items"]:
        res = evaluar_item(item, texto_norm)
        fila = dict(item)
        fila.update(res)
        resultados.append(fila)
    return resultados


def calcular_cumplimiento(resultados):
    """Devuelve % global ponderado y % por categoría."""
    total_peso = sum(r["peso"] for r in resultados)
    total_logrado = sum(r["peso"] * r["fraccion"] for r in resultados)
    global_pct = (total_logrado / total_peso * 100) if total_peso else 0.0

    por_categoria = {}
    for r in resultados:
        cat = r["categoria"]
        por_categoria.setdefault(cat, {"peso": 0, "logrado": 0})
        por_categoria[cat]["peso"] += r["peso"]
        por_categoria[cat]["logrado"] += r["peso"] * r["fraccion"]

    resumen_categorias = []
    for cat, d in por_categoria.items():
        pct = (d["logrado"] / d["peso"] * 100) if d["peso"] else 0.0
        resumen_categorias.append({"categoria": cat, "porcentaje": round(pct, 1)})
    # Mantener el orden en que aparecen las categorías en el checklist
    orden = []
    vistos = set()
    for r in resultados:
        if r["categoria"] not in vistos:
            orden.append(r["categoria"])
            vistos.add(r["categoria"])
    resumen_categorias.sort(key=lambda x: orden.index(x["categoria"]))

    return round(global_pct, 1), resumen_categorias


def generar_recomendaciones(resultados):
    """Recomendaciones priorizadas: primero obligatorios no cumplidos/parciales,
    luego opcionales."""
    pendientes = [r for r in resultados if r["estado"] != "CUMPLE"]
    pendientes.sort(key=lambda r: (not r.get("obligatorio", False), -r["peso"]))

    recomendaciones = []
    for r in pendientes:
        prioridad = "ALTA" if r.get("obligatorio") and r["estado"] == "NO_CUMPLE" else (
            "MEDIA" if r.get("obligatorio") else "BAJA"
        )
        texto = (
            f"Incorporar/ampliar en la política: {r['requisito']} "
            f"({r['articulo']})."
        )
        if r["estado"] == "PARCIAL":
            texto += " Contenido presente pero incompleto — verificar que se cubran todos los elementos exigidos."
        recomendaciones.append({
            "id": r["id"],
            "categoria": r["categoria"],
            "articulo": r["articulo"],
            "prioridad": prioridad,
            "texto": texto,
        })
    return recomendaciones


# ---------------------------------------------------------------------------
# Generación del informe Word
# ---------------------------------------------------------------------------

ESTADO_COLOR = {
    "CUMPLE": "1E7B34",     # verde
    "PARCIAL": "B8860B",    # ámbar
    "NO_CUMPLE": "B22222",  # rojo
}


def _set_cell_background(cell, hex_color):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    cell._tc.get_or_add_tcPr().append(shd)


def generar_informe_docx(output_path, cliente, texto_fuente_nombre, global_pct,
                          resumen_categorias, resultados, recomendaciones,
                          checklist_meta, banner_path=None, signature_path=None,
                          marca=True):
    from docx import Document
    from docx.shared import Pt, Cm, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = Document()

    # Página y márgenes iguales a la plantilla corporativa Protego
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1.25)
    section.right_margin = Inches(1.25)

    # Estilos base
    normal = doc.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    marca_activa = marca and banner_path and os.path.exists(banner_path)

    # Pie de página corporativo (aparece en todas las hojas)
    if marca_activa:
        footer_p = section.footer.paragraphs[0]
        footer_p.text = ""
        footer_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer_p.add_run("PROTEGO CONSULTING | CUMPLE. PROTEGE. CRECE")
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor.from_string("7F7F7F")

    # Banner de cabecera (portada)
    if marca_activa:
        p_banner = doc.add_paragraph()
        p_banner.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_banner = p_banner.add_run()
        run_banner.add_picture(banner_path, width=Inches(6.0))

    # Portada
    titulo = doc.add_heading("Informe de Verificación de Cumplimiento LOPDP", level=0)
    titulo.alignment = WD_ALIGN_PARAGRAPH.CENTER

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = sub.add_run("Política de Protección de Datos Personales")
    run.italic = True
    run.font.size = Pt(13)

    doc.add_paragraph()
    tabla_meta = doc.add_table(rows=0, cols=2)
    tabla_meta.alignment = WD_TABLE_ALIGNMENT.CENTER
    filas_meta = [
        ("Cliente / Entidad evaluada", cliente),
        ("Documento analizado", texto_fuente_nombre),
        ("Fecha del análisis", datetime.now().strftime("%d/%m/%Y")),
        ("Norma de referencia", checklist_meta.get("fuente", "LOPDP - Ecuador")),
        ("% de cumplimiento global", f"{global_pct}%"),
    ]
    for k, v in filas_meta:
        row = tabla_meta.add_row()
        row.cells[0].text = k
        row.cells[0].paragraphs[0].runs[0].bold = True
        row.cells[1].text = str(v)

    doc.add_paragraph()
    p = doc.add_paragraph()
    run = p.add_run(
        "Nota metodológica: este informe se generó mediante verificación automatizada "
        "por palabras clave/expresiones regulares sobre el texto del documento. Un ítem "
        "\"Cumple\" indica que el texto menciona razonablemente el requisito exigido por la "
        "LOPDP; no certifica que el contenido sea legalmente exacto, completo o coherente "
        "con el Registro de Actividades de Tratamiento (RAT) real de la entidad. Se recomienda "
        "validación final por el Delegado de Protección de Datos (DPO) o asesor legal."
    )
    run.italic = True
    run.font.size = Pt(9)

    doc.add_page_break()

    # Resumen ejecutivo
    doc.add_heading("1. Resumen ejecutivo", level=1)
    doc.add_paragraph(
        f"La política analizada alcanza un {global_pct}% de cumplimiento ponderado frente "
        f"a los requisitos de información, principios, derechos, seguridad y transferencia "
        f"de datos establecidos en la LOPDP."
    )

    tabla_res = doc.add_table(rows=1, cols=2)
    tabla_res.style = "Light Grid Accent 1"
    hdr = tabla_res.rows[0].cells
    hdr[0].text = "Categoría"
    hdr[1].text = "% Cumplimiento"
    for h in hdr:
        h.paragraphs[0].runs[0].bold = True

    for cat in resumen_categorias:
        row = tabla_res.add_row().cells
        row[0].text = cat["categoria"]
        row[1].text = f"{cat['porcentaje']}%"

    doc.add_paragraph()

    # Distribución de estados
    n_cumple = sum(1 for r in resultados if r["estado"] == "CUMPLE")
    n_parcial = sum(1 for r in resultados if r["estado"] == "PARCIAL")
    n_no = sum(1 for r in resultados if r["estado"] == "NO_CUMPLE")
    p = doc.add_paragraph()
    p.add_run(f"Ítems evaluados: {len(resultados)}   |   ").bold = False
    r1 = p.add_run(f"Cumple: {n_cumple}")
    r1.font.color.rgb = RGBColor.from_string(ESTADO_COLOR["CUMPLE"])
    p.add_run("   |   ")
    r2 = p.add_run(f"Parcial: {n_parcial}")
    r2.font.color.rgb = RGBColor.from_string(ESTADO_COLOR["PARCIAL"])
    p.add_run("   |   ")
    r3 = p.add_run(f"No cumple: {n_no}")
    r3.font.color.rgb = RGBColor.from_string(ESTADO_COLOR["NO_CUMPLE"])

    doc.add_page_break()

    # Detalle punto a punto
    doc.add_heading("2. Verificación punto a punto", level=1)

    categoria_actual = None
    for r in resultados:
        if r["categoria"] != categoria_actual:
            categoria_actual = r["categoria"]
            doc.add_heading(categoria_actual, level=2)

        tabla_item = doc.add_table(rows=0, cols=2)
        tabla_item.autofit = True

        def add_row(label, value, bold_label=True):
            row = tabla_item.add_row().cells
            row[0].text = label
            if bold_label:
                row[0].paragraphs[0].runs[0].bold = True
            row[1].text = str(value)
            return row

        add_row("Requisito", f"[{r['id']}] {r['requisito']}")
        add_row("Referencia legal", r["articulo"])
        row_estado = add_row("Estado", r["estado"])
        estado_run = row_estado[1].paragraphs[0].runs[0]
        estado_run.bold = True
        estado_run.font.color.rgb = RGBColor.from_string(ESTADO_COLOR[r["estado"]])
        _set_cell_background(row_estado[1], {
            "CUMPLE": "E2F0D9", "PARCIAL": "FFF2CC", "NO_CUMPLE": "FBE4E4"
        }[r["estado"]])
        add_row("Obligatorio", "Sí" if r.get("obligatorio") else "No / condicional")
        if r["coincidencias"]:
            add_row("Evidencia textual (patrón detectado)", "; ".join(r["coincidencias"][:3]))

        doc.add_paragraph()

    doc.add_page_break()

    # Recomendaciones
    doc.add_heading("3. Recomendaciones de mejora", level=1)
    if not recomendaciones:
        doc.add_paragraph("No se identificaron brechas: todos los ítems evaluados cumplen.")
    else:
        doc.add_paragraph(
            "Recomendaciones ordenadas por prioridad (ALTA = requisito obligatorio ausente; "
            "MEDIA = requisito obligatorio incompleto o condicional ausente; "
            "BAJA = mejora recomendable no obligatoria)."
        )
        tabla_rec = doc.add_table(rows=1, cols=4)
        tabla_rec.style = "Light Grid Accent 1"
        hdr = tabla_rec.rows[0].cells
        for i, txt in enumerate(["Prioridad", "ID", "Categoría", "Recomendación"]):
            hdr[i].text = txt
            hdr[i].paragraphs[0].runs[0].bold = True
        for rec in recomendaciones:
            row = tabla_rec.add_row().cells
            row[0].text = rec["prioridad"]
            row[0].paragraphs[0].runs[0].font.color.rgb = RGBColor.from_string(
                {"ALTA": "B22222", "MEDIA": "B8860B", "BAJA": "555555"}[rec["prioridad"]]
            )
            row[0].paragraphs[0].runs[0].bold = True
            row[1].text = rec["id"]
            row[2].text = rec["categoria"]
            row[3].text = rec["texto"]

    doc.add_page_break()
    doc.add_heading("4. Alcance y limitaciones", level=1)
    doc.add_paragraph(
        checklist_meta.get(
            "advertencia",
            "Este informe es un cribado documental automatizado y no reemplaza una auditoría legal completa."
        )
    )
    doc.add_paragraph(
        "Para una verificación integral se recomienda contrastar este resultado con el RAT "
        "(Registro de Actividades de Tratamiento), la Evaluación de Impacto (EIPDP) cuando "
        "corresponda, los contratos con encargados del tratamiento, y una revisión de las "
        "medidas de seguridad técnicas realmente implementadas (no solo declaradas en el texto)."
    )

    # Tarjeta de firma / contacto del DPO al cierre del informe
    signature_activa = marca and signature_path and os.path.exists(signature_path)
    if signature_activa:
        doc.add_paragraph()
        p_firma = doc.add_paragraph()
        p_firma.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run_firma = p_firma.add_run()
        run_firma.add_picture(signature_path, width=Inches(4.17))

    doc.save(output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Verifica una política de protección de datos frente a la LOPDP y genera un informe Word."
    )
    parser.add_argument("--policy", required=True, help="Ruta al archivo de la política (.docx, .pdf, .txt)")
    parser.add_argument("--client", default="Cliente sin nombre", help="Nombre del cliente/entidad evaluada")
    parser.add_argument("--checklist", default=os.path.join(os.path.dirname(__file__), "checklist_lopdp.json"),
                         help="Ruta al checklist JSON (editable)")
    parser.add_argument("--output", default=None, help="Ruta del informe .docx de salida")
    parser.add_argument("--json-report", default=None, help="Ruta opcional para exportar los resultados en JSON")
    parser.add_argument("--banner", default=DEFAULT_BANNER, help="Ruta al PNG del banner de cabecera (marca Protego)")
    parser.add_argument("--signature", default=DEFAULT_SIGNATURE, help="Ruta al PNG de la tarjeta de firma/contacto (marca Protego)")
    parser.add_argument("--no-brand", action="store_true", help="Genera el informe SIN branding de Protego Consulting (documento neutro)")
    args = parser.parse_args()

    if not os.path.exists(args.policy):
        print(f"ERROR: no se encontró el archivo de política: {args.policy}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.checklist):
        print(f"ERROR: no se encontró el checklist: {args.checklist}", file=sys.stderr)
        sys.exit(1)

    with open(args.checklist, "r", encoding="utf-8") as f:
        checklist = json.load(f)

    print(f"Leyendo política: {args.policy}")
    texto = cargar_texto_politica(args.policy)
    if not texto.strip():
        print("ERROR: no se pudo extraer texto del documento (¿está escaneado como imagen?).", file=sys.stderr)
        sys.exit(1)

    print(f"Evaluando {len(checklist['items'])} ítems del checklist LOPDP...")
    resultados = evaluar_politica(texto, checklist)
    global_pct, resumen_categorias = calcular_cumplimiento(resultados)
    recomendaciones = generar_recomendaciones(resultados)

    output_path = args.output or (
        os.path.splitext(os.path.basename(args.policy))[0] + "_informe_LOPDP.docx"
    )

    print(f"Cumplimiento global: {global_pct}%")
    print("Generando informe Word...")
    generar_informe_docx(
        output_path=output_path,
        cliente=args.client,
        texto_fuente_nombre=os.path.basename(args.policy),
        global_pct=global_pct,
        resumen_categorias=resumen_categorias,
        resultados=resultados,
        recomendaciones=recomendaciones,
        checklist_meta=checklist.get("_meta", {}),
        banner_path=args.banner,
        signature_path=args.signature,
        marca=not args.no_brand,
    )
    print(f"Informe generado: {output_path}")

    if args.json_report:
        with open(args.json_report, "w", encoding="utf-8") as f:
            json.dump({
                "cliente": args.client,
                "documento": os.path.basename(args.policy),
                "fecha": datetime.now().isoformat(),
                "cumplimiento_global_pct": global_pct,
                "resumen_categorias": resumen_categorias,
                "resultados": resultados,
                "recomendaciones": recomendaciones,
            }, f, ensure_ascii=False, indent=2)
        print(f"Resultados JSON exportados: {args.json_report}")


if __name__ == "__main__":
    main()
