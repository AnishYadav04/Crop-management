"""Apply the supplied report template without changing its original package.

Uses the bundled document runtime. Content lives in reports/report_content.json.
Usage: python scripts/format_project_report.py --template PATH
"""
import argparse
import copy
import io
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]

def build(template):
    reference = Path(template).read_bytes()
    doc = Document(io.BytesIO(reference))
    section = copy.deepcopy(doc.sections[0]._sectPr)
    body = doc._element.body
    for child in list(body):
        body.remove(child)
    body.append(section)
    blocks = json.loads((ROOT/'reports/report_content.json').read_text(encoding='utf-8'))

    def format_p(p, kind='p'):
        f = p.paragraph_format
        f.line_spacing = 1.5
        f.space_before = Pt(0)
        f.space_after = Pt(0)
        f.page_break_before = False
        f.widow_control = True
        f.keep_together = True
        f.keep_with_next = kind in ('title','h1','h2','table_caption','figure')
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER if kind in ('title','caption','figure') else WD_ALIGN_PARAGRAPH.JUSTIFY
        if kind in ('h1','h2'):
            outline = OxmlElement('w:outlineLvl'); outline.set(qn('w:val'),'0' if kind=='h1' else '1')
            p._p.get_or_add_pPr().append(outline)
        for r in p.runs:
            r.font.name='Times New Roman';r.font.color.rgb=RGBColor(0,0,0)
            r.font.size=Pt(16 if kind=='title' else 14 if kind=='h1' else 10)
            r.bold=kind in ('title','h1','h2','table_caption')
            r.italic=kind=='caption'
            fonts=r._r.get_or_add_rPr().get_or_add_rFonts()
            for script in ('ascii','hAnsi','eastAsia','cs'):fonts.set(qn('w:'+script),'Times New Roman')
        return p

    for b in blocks:
        kind=b['kind']
        if kind=='table':
            t=doc.add_table(rows=0, cols=len(b['rows'][0]));t.autofit=False
            widths=b.get('widths',[6.268/len(b['rows'][0])]*len(b['rows'][0]))
            for col,w in zip(t.columns,widths): col.width=Inches(w)
            for i,row in enumerate(b['rows']):
                cells=t.add_row().cells
                for c,txt,w in zip(cells,row,widths):
                    c.width=Inches(w);c.text=txt
                    format_p(c.paragraphs[0],'h2' if i==0 else 'p')
                    c.paragraphs[0].alignment=WD_ALIGN_PARAGRAPH.LEFT
                    c.paragraphs[0].paragraph_format.line_spacing=1.0
                    c.paragraphs[0].paragraph_format.keep_with_next=i<len(b['rows'])-1
                    for run in c.paragraphs[0].runs:run.font.size=Pt(10)
                    pr=c._tc.get_or_add_tcPr();borders=OxmlElement('w:tcBorders')
                    for side in ('top','left','bottom','right'):
                        edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'single');edge.set(qn('w:sz'),'4');edge.set(qn('w:color'),'000000');borders.append(edge)
                    pr.append(borders)
                trPr=t.rows[i]._tr.get_or_add_trPr();trPr.append(OxmlElement('w:cantSplit'))
                if i==0:trPr.append(OxmlElement('w:tblHeader'))
        elif kind=='image':
            p=format_p(doc.add_paragraph(),'figure')
            shape=p.add_run().add_picture(str(ROOT/b['path']),width=Inches(6.268))
            shape._inline.xpath('.//a:blip')[0].append(OxmlElement('a:grayscl'))
            p.paragraph_format.line_spacing=1.0
        else:
            p=format_p(doc.add_paragraph(b['text']),kind)
            if b['text'].startswith('['):p.alignment=WD_ALIGN_PARAGRAPH.LEFT
            if b.get('break'):p.paragraph_format.page_break_before=True
    doc.core_properties.title='Crop Care System'
    doc.core_properties.author='Student project group, BML Munjal University'
    intermediate=io.BytesIO();doc.save(intermediate)
    # Only adopt changed content/relationships and new media. All template styles,
    # settings, fonts, theme and existing media remain byte-identical.
    with ZipFile(io.BytesIO(reference)) as src,ZipFile(intermediate) as edited:
        changed={'word/document.xml','word/_rels/document.xml.rels','docProps/core.xml'}
        old=set(src.namelist());new=set(edited.namelist())-old
        destination=ROOT/'reports/Crop_Care_System_Report.docx'
        with ZipFile(destination,'w',ZIP_DEFLATED) as out:
            for name in src.namelist():out.writestr(name,edited.read(name) if name in changed else src.read(name))
            for name in new:out.writestr(name,edited.read(name))
        with ZipFile(destination) as final:
            assert all(final.read(n)==src.read(n) for n in old-changed)
    print(destination)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--template',required=True)
    build(parser.parse_args().template)
