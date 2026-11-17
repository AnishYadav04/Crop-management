The current report follows the supplied `SectionNo_GroupNo.docx` format: A4,
one-inch margins, Times New Roman, numbered sections, figures and tables.
The five student names, enrolment numbers, section and group number remain editable.

Content is maintained in `report_content.json`; figures are in `figures/`.
To rebuild with the bundled document Python runtime:

```powershell
python scripts/format_project_report.py --template "PATH\SectionNo_GroupNo.docx"
```

The reference is read without modification. The formatter preserves its untouched
ZIP parts and replaces the report content. Render and inspect the Word output after
editing. The older `scripts/build_project_report.py` generates the previous layout;
use the formatter above for the current submission format.

The shared Google Doc was updated in place. It is not automatically synchronized
with subsequent edits to this local Word file or JSON source.
