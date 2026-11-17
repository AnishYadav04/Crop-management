"""Build the short project report from checked-in code and saved run artifacts.

Use the document runtime's Python. UI photographs are captured separately in
artifacts/report_qa; generated report files are placed in reports.
"""
from pathlib import Path
import csv
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'artifacts' / 'report_qa'
OUT = ROOT / 'reports'
QA.mkdir(parents=True, exist_ok=True)
OUT.mkdir(exist_ok=True)

# Original schematic, derived from this repository's service and data flow.
im = Image.new('RGB', (1700, 850), 'white')
dr = ImageDraw.Draw(im)
font = ImageFont.truetype('C:/Windows/Fonts/times.ttf', 32)
small = ImageFont.truetype('C:/Windows/Fonts/times.ttf', 27)
def box(x, y, w, h, lines):
    dr.rectangle((x,y,x+w,y+h),outline='black',width=3)
    for i,line in enumerate(lines):
        dr.text((x+w/2,y+24+i*38),line,fill='black',font=font,anchor='mt')
def arrow(x1,y1,x2,y2,label=None):
    dr.line((x1,y1,x2,y2),fill='black',width=3)
    if x2>x1: pts=[(x2,y2),(x2-13,y2-8),(x2-13,y2+8)]
    else: pts=[(x2,y2),(x2-8,y2-13),(x2+8,y2-13)]
    dr.polygon(pts,fill='black')
    if label: dr.text(((x1+x2)/2,(y1+y2)/2-32),label,font=small,fill='black',anchor='mm')
box(30,100,360,145,['Wokwi ESP32','Eight simulated readings','About every 10 seconds'])
box(650,100,360,145,['MQTT broker','Subscriber validates data','PostgreSQL stores rows'])
box(1270,100,390,145,['Rule monitor','Persistent conditions','Stored inspection alerts'])
arrow(390,172,650,172,'Wi-Fi and MQTT')
arrow(1010,172,1270,172,'Latest readings')
box(30,420,360,145,['Open-Meteo API','Kapriwas weather','Archive and rain outlook'])
box(650,420,360,145,['Website and chatbot','Readings and action log','Farmer asks a question'])
box(1270,420,390,145,['Tavily and Gemini','Retrieve web evidence','Compose a cited answer'])
dr.text((210,330),'Independent weather source',font=small,fill='black',anchor='mm')
arrow(390,492,650,492,'Weather context')
arrow(830,245,830,420)
arrow(1465,245,1465,420)
arrow(1010,492,1270,492,'Question and context')
dr.text((850,660),'A person inspects the plant and records the action on the website.',font=font,fill='black',anchor='mm')
dr.text((850,715),'No physical irrigation actuator is connected in this prototype.',font=small,fill='black',anchor='mm')
im.save(QA/'architecture.png')

doc=Document()
sec=doc.sections[0]
sec.page_width=Inches(8.27);sec.page_height=Inches(11.69)
sec.top_margin=Inches(.65);sec.bottom_margin=Inches(.65)
sec.left_margin=sec.right_margin=Inches(.78)
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Caption','Table Grid']:
    style=doc.styles[name]
    style.font.name='Times New Roman';style.font.color.rgb=RGBColor(0,0,0)
    style.font.size=Pt(12)
    fonts=style.element.get_or_add_rPr().get_or_add_rFonts()
    for attribute in list(fonts.attrib):
        if attribute.endswith('Theme'):del fonts.attrib[attribute]
    for script in ['ascii','hAnsi','eastAsia','cs']:
        fonts.set(qn('w:'+script),'Times New Roman')
    style.paragraph_format.space_after=Pt(7)
    # Some Word templates add a coloured title border through the style.
    for border in list(style.element.xpath('.//w:pBdr')):
        border.getparent().remove(border)
doc.styles['Normal'].paragraph_format.line_spacing=1.1
doc.styles['Title'].font.size=Pt(25)
doc.styles['Heading 1'].font.size=Pt(15)
doc.styles['Heading 2'].font.size=Pt(12)
doc.styles['Caption'].font.size=Pt(10)
doc.styles['Caption'].font.bold=False
doc.styles['Caption'].font.italic=True
for name in ['Heading 1','Heading 2']:
    doc.styles[name].paragraph_format.space_before=Pt(10)
    doc.styles[name].paragraph_format.keep_with_next=True
doc.core_properties.title='Crop Care System'
doc.core_properties.subject='IoT project report for BML Munjal University'
doc.core_properties.author='Student project group'
head=sec.header.paragraphs[0]
head.text='BML Munjal University  |  IoT Project Report'
head.style=doc.styles['Normal'];head.runs[0].font.size=Pt(9)
foot=sec.footer.paragraphs[0];foot.alignment=WD_ALIGN_PARAGRAPH.CENTER
foot.add_run('Crop Care System  |  ')
fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');foot._p.append(fld)
for r in foot.runs:r.font.size=Pt(9)

def p(text='',style=None):return doc.add_paragraph(text,style)
_new_page=False
def h(text):
    global _new_page
    para=doc.add_heading(text,level=1)
    if _new_page:para.paragraph_format.page_break_before=True
    _new_page=False
def sub(text):doc.add_heading(text,level=2)
def newpage():
    global _new_page
    _new_page=True
def table(headers,rows,widths):
    t=doc.add_table(rows=1, cols=len(headers));t.style='Table Grid';t.autofit=False
    for cell,w,txt in zip(t.rows[0].cells,widths,headers):cell.width=Inches(w);cell.text=txt
    for values in rows:
        cells=t.add_row().cells
        for c,w,txt in zip(cells,widths,values):c.width=Inches(w);c.text=str(txt)
    for i,row in enumerate(t.rows):
        props=row._tr.get_or_add_trPr();cant=OxmlElement('w:cantSplit');props.append(cant)
        if i==0:props.append(OxmlElement('w:tblHeader'))
        for cell in row.cells:
            pr=cell._tc.get_or_add_tcPr();borders=OxmlElement('w:tcBorders')
            for edge in ['top','left','bottom','right']:
                b=OxmlElement('w:'+edge);b.set(qn('w:val'),'single');b.set(qn('w:sz'),'4');b.set(qn('w:color'),'D9D9D9');borders.append(b)
            pr.append(borders)
            margins=OxmlElement('w:tcMar')
            for edge in ['top','left','bottom','right']:
                b=OxmlElement('w:'+edge);b.set(qn('w:w'),'85');b.set(qn('w:type'),'dxa');margins.append(b)
            pr.append(margins)
            if i==0:
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'EEEEEE');pr.append(shade)
            for para in cell.paragraphs:
                para.paragraph_format.space_after=Pt(2);para.paragraph_format.line_spacing=1.0
                for r in para.runs:r.font.size=Pt(10.5);r.bold=i==0
    p('')
    return t
def figure(path,caption,width=6.6):
    para=p();para.alignment=WD_ALIGN_PARAGRAPH.CENTER
    shape=para.add_run().add_picture(str(path),width=Inches(width))
    # Native Word greyscale effect keeps the original screenshot intact.
    blip=shape._inline.xpath('.//a:blip')[0];blip.append(OxmlElement('a:grayscl'))
    para.paragraph_format.keep_with_next=True
    p(caption,'Caption')

title=p('Crop Care System','Title');title.alignment=WD_ALIGN_PARAGRAPH.CENTER
for text in ['A Simulation Based IoT Project Report','BML Munjal University','Course  IoT     |     Submitted to  Sandeep Sir','17 November 2026']:
    para=p(text);para.alignment=WD_ALIGN_PARAGRAPH.CENTER
table(['Student','Name','Enrolment number'],[(str(i),'________________________','____________________') for i in range(1,6)],[.65,3.25,2.7])
h('Abstract')
p('This project demonstrates an IoT crop care system for Kapriwas, Haryana. A simulated ESP32 in Wokwi reads eight environmental inputs and publishes them through MQTT. A Python subscriber stores the readings in PostgreSQL. Rules check repeated low moisture, high moisture and wet-canopy conditions and create inspection alerts. The website presents sensor trends, weather context, action history and a question-answering agent. The agent uses the selected plant profile, recent readings and Tavily web results to prepare a cited response with Gemini. A separate forecasting experiment uses one year of hourly weather reanalysis. The prototype successfully demonstrated sensor transmission, storage and a dry-root-zone alert. Its readings are simulated, its moisture limits are demonstration settings, and its forecasts describe environmental variables rather than crop disease. The project is intended for learning and controlled demonstrations.')
p('Keywords: Internet of Things, Wokwi, MQTT, PostgreSQL, time series, crop care.')
h('Acknowledgement')
p('We thank Sandeep Sir and BML Munjal University for the opportunity to present this IoT project. We acknowledge the documentation and open-source tools used to build the simulation, database, forecasting workflow and website.')

newpage();h('1 Introduction')
p('Farm decisions depend on several conditions at the same time. A low reading near the soil surface may need a manual inspection, while upcoming rain may affect when an inspection or watering decision is made. A useful monitoring system should preserve the evidence, explain what changed and keep a record of the user’s response. This project brings these steps together for a simulated site in Kapriwas.')
p('The main objective is to demonstrate a complete information flow: sensor control, device message, database record, rule-based alert, explanation and recorded human action. The application supports different plants through an editable profile. It does not assume that every crop has the same watering needs.')
sub('1 1 Scope')
p('The project includes an ESP32 simulation, eight input channels, MQTT ingestion, hourly summaries, weather API context, a dashboard, saved conversations and an environmental forecasting experiment. It does not include a physical farm deployment, an automatic pump, soil nutrient measurements or a trained disease classifier.')
h('2 Literature Review')
sub('2 1 Crop water management')
p('FAO Irrigation and Drainage Paper 56 explains how crop water use and water stress depend on weather, crop characteristics and root-zone water availability [1]. This supports a cautious design: a universal percentage from a simulated knob cannot establish an irrigation dose. The prototype therefore recommends checking conditions and asks for the plant and growth stage.')
sub('2 2 IoT simulation and weather information')
p('Wokwi supports ESP32 networking, allowing a virtual device to communicate with internet services [2]. This makes it suitable for demonstrating MQTT and data ingestion without building hardware. Open-Meteo provides historical weather reanalysis and forecast interfaces [3]. Such data add regional context but do not measure the soil around an individual plant.')
sub('2 3 Forecasting and retrieved guidance')
p('Time-ordered validation avoids training on future observations when evaluating earlier predictions [4]. Gradient boosting offers a practical regression model for lagged environmental data [5]. Tavily supplies retrieved web results for a question [6]; the language model then summarizes them alongside local context. Retrieval helps provide references, but an answer still needs checking for relevance and support.')
sub('2 4 Design choice')
p('The system separates monitoring rules from generated advice. Rules determine when a simulated condition deserves attention. The agent explains the evidence and suggests cautious next steps. This allows monitoring to continue even when a web-search or language-model provider is unavailable.')

newpage();h('3 Methodology')
p('The work follows a prototype-and-test method. First, define a labelled sensor message and store it with its source and time. Next, detect sustained conditions instead of single spikes. Finally, present the evidence and evaluate the system with controlled simulation scenarios.')
figure(QA/'architecture.png','Figure 1. System architecture derived from the implemented services. Weather and Wokwi remain separate sources.')
table(['Reading','Simulation input','Meaning'],[
('Air temperature and humidity','DHT22 on GPIO4','Two configurable channels'),
('Shallow and deep moisture','Knobs on GPIO34 and GPIO33','Relative indexes from 0 to 100%'),
('Soil temperature','NTC on GPIO32','Simulated temperature'),
('Light','Photoresistor on GPIO35','Approximate lux conversion'),
('Rainfall','Knob on GPIO36','Simulated amount per sample'),
('Leaf wetness','Knob on GPIO39','Relative wetness index')],[2.1,2.1,2.4])
sub('3 1 Collection and storage')
p('The ESP32 publishes about every ten seconds with a device ID, boot-session ID, sample number and timestamp. The subscriber validates values and stores them in PostgreSQL. Source labels distinguish Wokwi simulation, current model-based weather and the ERA5 archive. Completed hours are summarized for trend review; incomplete coverage is shown rather than treated as a full hour.')
sub('3 2 Alert rules')
p('Three consecutive fresh readings must span at least 20 seconds and no more than 300 seconds. Default demo limits are below 30% shallow moisture, above 80% shallow moisture, or humidity of at least 85% together with leaf wetness of at least 60%. The monitor runs about every 15 seconds. Existing unresolved alerts and a repeat limit reduce duplicate notifications.')

newpage();h('4 Website and Agent Implementation')
p('The dashboard separates simulated field readings from current Kapriwas weather. The chart shows a numbered scale, time labels, the latest value and, for shallow moisture, the demonstration limits. A constant input correctly produces a flat line. Changing a Wokwi control changes future samples rather than rewriting stored history.')
if (QA/'graph.png').exists():figure(QA/'graph.png','Figure 2. Actual dashboard graph from the Wokwi demonstration. The moisture control changed from about 68% to about 20%.')
sub('4 1 Actions and guidance')
p('The Actions panel shows current inspection suggestions. Alert history keeps the original event, its evidence, generated guidance and reference links. Separate labels show whether an alert is awaiting review, reviewed or resolved. Resolving an alert records a workflow decision; it does not change the sensor or confirm that the plant has recovered.')
sub('4 2 Question answering')
p('A farmer can enter a question on the Ask crop agent page. The service combines the question with the plant profile, latest readings, recent actions and weather outlook. Tavily retrieves relevant pages and Gemini prepares a cited answer. The search can be retried once for an evidence gap, and the application rereads device context before synthesis. When generation fails, a labelled local-status fallback remains available.')
sub('4 3 Environmental forecasting')
p('The saved training audit contains 8,760 hourly ERA5 rows from 3 November 2025 to 2 November 2026, with no missing or duplicate hourly timestamps. Models predict temperature, humidity, moisture, soil temperature, rain and solar radiation at 1, 6 and 24 hours. Lagged and rolling features feed a histogram gradient-boosting regressor. Persistence and daily or weekly seasonal baselines provide comparisons. The saved evaluation uses three expanding time folds; reported results below are archived cross-validation results, not a new field trial.')
p('Weather-model soil moisture and Wokwi control percentages have different meanings. The trained archive models are environmental experiments; transferring them to live data needs a feature and validation audit. The plant attention alerts in this project are rule based.')

newpage();h('5 Results and Discussion')
p('The implementation was checked on 17 November 2026. All eight corrected Wokwi channels reached MQTT, PostgreSQL and the dashboard. A demonstration reading showed 28°C air temperature, 65% humidity, 19.6% shallow moisture, 74.3% deep moisture, 24°C soil temperature, about 905 lux, zero simulated rain and zero leaf wetness. Repeated low shallow moisture created a stored dry-root-zone inspection alert.')
table(['Check','Observed result'],[
('Sensor ingestion','Eight channels received through the simulator and MQTT.'),
('Persistent dry scenario','Inspection alert created after three qualifying readings.'),
('Persistent wet scenario','Changing the Wokwi control to 90% created a drainage-check alert.'),
('Chatbot synthesis','Live requests returned Gemini answers with Tavily references.'),
('Software checks','19 existing automated tests passed.'),
('Revised dashboard','Headless desktop and narrow-screen checks found no JavaScript errors or horizontal overflow.')],[2.0,4.6])
sub('5 1 Saved temperature forecast results')
metrics=list(csv.DictReader((ROOT/'artifacts/forecasting/metrics.csv').open()))
rows=[]
for r in metrics:
    if r['target']=='air_temp_c':
        rows.append((r['horizon_hours']+' h',f"{float(r['hist_gradient_boosting_mae']):.3f}",f"{float(r['persistence_mae']):.3f}",'Boosting' if r['selected_by_mae']=='hist_gradient_boosting' else 'Persistence'))
table(['Horizon','Boosting MAE °C','Persistence MAE °C','Selected method'],rows,[1,1.8,1.9,1.9])
p('Source: saved metrics.csv and run_summary.json, run dated 8 November 2026. MAE is mean absolute error; smaller values are better. Boosting performed better than persistence at one and six hours, while persistence was selected at 24 hours. These are results on weather reanalysis. They do not measure disease detection, crop yield or irrigation savings. The archive evaluation should be reproduced against the latest serving code before claiming deployment performance.','Caption')
sub('5 2 Limitations')
p('The system has no physical sensor validation. Moisture and leaf-wetness indexes are manually controlled, and light conversion is approximate. A year of weather covers seasons but does not provide labelled crop problems. Web guidance can be incomplete or incorrect, and provider availability varies. The public MQTT broker is suitable for demonstrations, not private farm deployment. Browser notifications require the dashboard to remain open. No fertilizer dose, disease diagnosis or pump action is established by this prototype.')

newpage();h('6 Classroom Demonstration')
for text in [
'1. Start Docker Desktop and the project services. Open localhost:8000, enter a plant and growth stage, and keep Kapriwas as the location.',
'2. Start Wokwi in VS Code. After a fresh boot, run scripts/upload_wokwi.py with the project Python environment. Click the circuit background and press F to fit the diagram.',
'3. Select Wokwi sensors, Shallow soil moisture and Every reading on the dashboard. Turn the shallow-moisture knob below 30%. Wait for three readings and the next monitoring cycle, normally about 30–45 seconds after a successful change.',
'4. Explain the new inspection suggestion and its evidence. Ask the chatbot why it appeared. Record the inspection, then resolve the alert. The same alert has a one-hour repeat limit; for a second demonstration, move shallow moisture above 80% and show the drainage-check suggestion.',
'5. Click the DHT22 and change its temperature. Show the graph responding. Explain that changing temperature alone does not trigger the soil-moisture rule. Stop Wokwi only at the end to demonstrate the offline state after about two minutes.'
]:p(text)
h('7 Conclusion and Future Work')
p('The project demonstrates an IoT monitoring workflow through a virtual ESP32, MQTT, PostgreSQL and a website. Recorded readings produce explainable inspection alerts, while a separate agent adds referenced guidance. The next steps are calibrated physical sensors, crop-specific validation, authenticated messaging, measured alert accuracy and a safe simulated actuator loop with acknowledgements. These improvements must be evaluated before real-farm use.')
h('References')
refs=[
'[1] Allen RG, Pereira LS, Raes D and Smith M. Crop evapotranspiration. FAO Irrigation and Drainage Paper 56, 1998. https://www.fao.org/4/X0490E/x0490e00.htm',
'[2] Wokwi. ESP32 WiFi Networking and simulator documentation. https://docs.wokwi.com/guides/esp32-wifi',
'[3] Open-Meteo. Historical Weather API documentation. https://open-meteo.com/en/docs/historical-weather-api',
'[4] scikit-learn. TimeSeriesSplit documentation. https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html',
'[5] scikit-learn. HistGradientBoostingRegressor documentation. https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.HistGradientBoostingRegressor.html',
'[6] Tavily. Search API documentation. https://docs.tavily.com/documentation/api-reference/endpoint/search'
]
for ref in refs:
    para=p(ref);para.paragraph_format.space_after=Pt(5)
    for r in para.runs:r.font.size=Pt(9)
p('Documentation accessed 17 November 2026. Project evidence: repository code, stored demonstration event and local forecasting artifacts.','Caption')
doc.save(OUT/'Crop_Care_System_Report.docx')
print(OUT/'Crop_Care_System_Report.docx')
