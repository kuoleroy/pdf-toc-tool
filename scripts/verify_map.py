# -*- coding: utf-8 -*-
# Verify printed-page -> pdf-index mapping by OCR'ing page numbers on sample body pages
import fitz, os, io, sys
from rapidocr_onnxruntime import RapidOCR

PDF = r'C:\Users\Administrator\Desktop\social-theory\列宁选集\列宁选集第1卷72版.pdf'
ocr = RapidOCR()

# (printed_page, pdf_index_to_check)
samples = [85, 116, 157, 220, 390, 452, 511, 637, 779, 847]

doc = fitz.open(PDF)
for printed in samples:
    idx = printed + 15
    page = doc[idx]
    pix = page.get_pixmap(dpi=150)
    img = pix.tobytes('png')
    res, _ = ocr(img)
    lines = [r[1] for r in res] if res else []
    # print number = first token of first 3 lines if it looks like a page number
    first3 = ' | '.join(lines[:3])
    print('printed=%3d -> pdf[%d]: first3=%s' % (printed, idx, first3[:90]))
doc.close()
