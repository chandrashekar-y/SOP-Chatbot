"""
Everhealth SOP Converter — Excel/CSV/DOCX → structured JSON schema
Converts all SOP files from data/Everhealth SOPs/ into JSON records
compatible with ingest_schema.py for ChromaDB ingestion.

Sheet mapping for Everhealth Excel SOPs:
  - General Practice Information → practice metadata + payer list + holidays
  - Provider Information         → provider records
  - Billing Status Overview      → billing status records
  - Prebilling Checklist         → prebilling rules
  - Denial Guidelines            → denial scenario Q&A + new directives
  - AR Guidelines                → AR rules + out-of-network + paid-not-posted
  - Payment Posting              → payment posting rules
  - Statements and Patient...    → statement/patient engagement rules
  - Reports                      → report records
  - Portal Logins                → portal login records
  - Practice Overview            → practice overview records
  - Coding Guidelines            → coding rules (some files only)
  - Workflow Overview            → workflow records (some files only)
"""

import os, sys, json, re, zipfile, csv
from datetime import datetime, date, timedelta
from xml.etree import ElementTree as ET
from pathlib import Path

# ─── XML Namespaces ─────────────────────────────────────────────────────────
XL_NS  = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
XL_RNS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'

TODAY = datetime.today().strftime('%Y-%m-%d')

# ─── Excel reader (pure zipfile — avoids openpyxl hang on Windows) ──────────

def _get_shared_strings(zf):
    if 'xl/sharedStrings.xml' not in zf.namelist():
        return []
    with zf.open('xl/sharedStrings.xml') as f:
        root = ET.parse(f).getroot()
    strings = []
    for si in root.findall(f'{{{XL_NS}}}si'):
        text_parts = []
        runs = si.findall(f'{{{XL_NS}}}r')
        if runs:
            for r_el in runs:
                # Issue 9 fix: skip struck-through runs
                rpr = r_el.find(f'{{{XL_NS}}}rPr')
                if rpr is not None and rpr.find(f'{{{XL_NS}}}strike') is not None:
                    continue
                t_el = r_el.find(f'{{{XL_NS}}}t')
                if t_el is not None and t_el.text:
                    text_parts.append(t_el.text)
        else:
            # Plain <si><t>text</t></si> (no rich text runs)
            t_el = si.find(f'{{{XL_NS}}}t')
            if t_el is not None and t_el.text:
                text_parts.append(t_el.text)
        strings.append(''.join(text_parts))
    return strings


def _get_date_style_indices(zf):
    """Return set of xf style indices that represent date-formatted cells."""
    if 'xl/styles.xml' not in zf.namelist():
        return set()
    with zf.open('xl/styles.xml') as f:
        root = ET.parse(f).getroot()
    # Built-in Excel date numFmtIds: 14-17, 22, 45-47
    date_fmt_ids = set(range(14, 18)) | {22, 45, 46, 47}
    # Add custom numFmts that look like dates (contain y or d, not 'general')
    for nf in root.iter(f'{{{XL_NS}}}numFmt'):
        fid = int(nf.get('numFmtId', 0))
        code = nf.get('formatCode', '').lower()
        if 'general' not in code and ('y' in code or ('d' in code and 'm' in code)):
            date_fmt_ids.add(fid)
    # Find xf entries that reference date formats
    date_indices = set()
    xfs_el = root.find(f'.//{{{XL_NS}}}cellXfs')
    if xfs_el is not None:
        for i, xf in enumerate(xfs_el):
            if int(xf.get('numFmtId', 0)) in date_fmt_ids:
                date_indices.add(i)
    return date_indices


def _excel_serial_to_date(serial):
    """Convert Excel serial number to M/D/YYYY string."""
    try:
        n = int(float(serial))
        # Excel's epoch is 1900-01-00; it also wrongly treats 1900 as a leap year
        if n >= 60:
            n -= 1  # correct for Excel's phantom Feb 29 1900
        d = date(1899, 12, 31) + timedelta(days=n)
        return f"{d.month}/{d.day}/{d.year}"
    except Exception:
        return str(serial)


def _col_letter_to_index(col_str):
    """Convert Excel column letters to 0-based index. 'A'->0, 'B'->1, 'Z'->25, 'AA'->26."""
    idx = 0
    for ch in col_str.upper():
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


def _get_sheet_map(zf):
    """Returns list of (sheet_name, xml_path_in_zip)"""
    with zf.open('xl/workbook.xml') as f:
        wb = ET.parse(f).getroot()
    with zf.open('xl/_rels/workbook.xml.rels') as f:
        rels_root = ET.parse(f).getroot()
    rels = {r.get('Id'): r.get('Target') for r in rels_root}
    sheets = []
    for sh in wb.iter(f'{{{XL_NS}}}sheet'):
        name   = sh.get('name', '').strip()
        rid    = sh.get(f'{{{XL_RNS}}}id', '')
        target = rels.get(rid, '')
        path   = f'xl/{target}' if not target.startswith('xl/') else target
        sheets.append((name, path))
    return sheets


def _read_sheet(zf, path, shared_strings, date_style_indices=None):
    """Read all rows from a sheet XML. Returns list of list of str."""
    if date_style_indices is None:
        date_style_indices = set()
    if path not in zf.namelist():
        return []
    with zf.open(path) as f:
        root = ET.parse(f).getroot()
    rows = []
    for row_el in root.iter(f'{{{XL_NS}}}row'):
        row = []
        for c in row_el:
            t      = c.get('t', '')
            s_attr = c.get('s', '')
            v_el   = c.find(f'{{{XL_NS}}}v')

            # Issue 8 fix: use cell reference to place value in the correct column
            r_attr = c.get('r', '')
            col_idx = None
            if r_attr:
                m = re.match(r'([A-Z]+)', r_attr)
                if m:
                    col_idx = _col_letter_to_index(m.group(1))
                    # Pad row with empty strings up to this column
                    while len(row) < col_idx:
                        row.append('')

            val = ''
            if v_el is not None and v_el.text:
                if t == 's':
                    idx = int(v_el.text)
                    val = shared_strings[idx] if idx < len(shared_strings) else ''
                elif t == 'b':
                    val = 'TRUE' if v_el.text == '1' else 'FALSE'
                else:
                    raw = v_el.text.strip()
                    # Issue 2a fix: Excel serial date detection
                    if s_attr and date_style_indices and int(s_attr) in date_style_indices:
                        val = _excel_serial_to_date(raw)
                    # Issue 2b fix: scientific notation numbers (e.g. NPI 1.42772237E9)
                    elif re.match(r'^-?\d+\.?\d*[eE][+\-]?\d+$', raw):
                        try:
                            val = str(int(float(raw)))
                        except Exception:
                            val = raw
                    else:
                        val = raw

            if col_idx is not None:
                if col_idx < len(row):
                    row[col_idx] = str(val).strip()
                else:
                    row.append(str(val).strip())
            else:
                row.append(str(val).strip())

        if any(row):
            rows.append(row)
    return rows


def read_xlsx(filepath):
    """Return dict: {sheet_name: [[row values]]}"""
    zf = zipfile.ZipFile(filepath)
    ss = _get_shared_strings(zf)
    date_styles = _get_date_style_indices(zf)
    sheet_map = _get_sheet_map(zf)
    result = {}
    for name, path in sheet_map:
        result[name] = _read_sheet(zf, path, ss, date_styles)
    zf.close()
    return result


# ─── Helpers ────────────────────────────────────────────────────────────────

def clean(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def cell(row, idx, default=''):
    return clean(row[idx]) if idx < len(row) else default


def nonempty(row):
    return [v for v in row if clean(v)]


def _practice_name_from_filename(filename):
    """e.g. 'SOP - Absolute Health.xlsx' → 'Absolute Health'"""
    name = os.path.splitext(os.path.basename(filename))[0]
    # Strip common prefixes/suffixes
    name = re.sub(r'^SOP\s*[-–]\s*', '', name, flags=re.I)
    name = re.sub(r'\s*SOP\s*$', '', name, flags=re.I)
    name = re.sub(r'\s*\(.*?\)\s*', '', name)  # remove parenthetical
    name = re.sub(r'\s*updated.*$', '', name, flags=re.I)
    return name.strip()


def make_record(practice_name, source_file, category, question, instruction,
                scope='General', status='ACTIVE', effective_date='NA',
                termination_date='NA', received_from='', billing_manager='',
                sop_reference=''):
    if not clean(instruction):
        return None
    return {
        'practice_name'   : practice_name,
        'scope'           : scope,
        'category'        : category,
        'question'        : question,
        'instruction'     : instruction,
        'effective_date'  : effective_date,
        'termination_date': termination_date,
        'status'          : status,
        'billing_manager' : billing_manager,
        'received_from'   : received_from,
        'sop_reference'   : f'{source_file} | {category}',
        'source_file'     : source_file,
        'last_updated'    : TODAY,
    }


# ─── Sheet parsers ──────────────────────────────────────────────────────────

def parse_general_practice(rows, practice_name, source_file):
    """
    Parse General Practice Information sheet.
    Left side: key-value practice info.
    Right side: payer table + holidays.
    """
    records = []
    payers = []
    holidays = []

    for row in rows:
        if len(row) < 2:
            continue
        key = cell(row, 0)
        val = cell(row, 1)

        # Detect payer row (column 3 = payer name, col 5 = status)
        payer_name = cell(row, 3)
        payer_status = cell(row, 5) if len(row) > 5 else ''
        if payer_name and payer_name.lower() not in (
            'payer', 'ptan/group number', 'credentialed status',
            'practice holiday schedule', '', 'payer name'
        ):
            if payer_status or cell(row, 4):
                payers.append({
                    'payer': payer_name,
                    'ptan': cell(row, 4),
                    'status': payer_status or 'Unknown',
                    'bill_as': cell(row, 7) if len(row) > 7 else '',
                })

        # Detect holiday row (col 3 = holiday name, col 0 blank or key is holiday-like)
        if (payer_name and 'day' in payer_name.lower() and not val) or \
           ('day' in key.lower() and not val and len(nonempty(row)) == 1):
            holiday = payer_name or key
            if holiday and holiday.lower() not in ('payer', ''):
                holidays.append(holiday)

    # Practice info record
    info_parts = []
    in_info = True
    for row in rows:
        key = cell(row, 0)
        val = cell(row, 1)
        if key and val and not key.lower().startswith('payer') and 'ptan' not in key.lower():
            info_parts.append(f"{key} {val}")
    if info_parts:
        r = make_record(practice_name, source_file,
                        'General Practice Information',
                        'What is the general practice information?',
                        '\n'.join(info_parts))
        if r: records.append(r)

    # Payer records
    for p in payers:
        instr = (f"Payer: {p['payer']}. "
                 f"PTAN/Group: {p['ptan'] or 'N/A'}. "
                 f"Credentialed Status: {p['status']}."
                 + (f" Billed as: {p['bill_as']}." if p['bill_as'] else ''))
        r = make_record(practice_name, source_file,
                        'Payer Information',
                        f"What is the credentialing status for {p['payer']}?",
                        instr)
        if r: records.append(r)

    # Holiday record
    if holidays:
        r = make_record(practice_name, source_file,
                        'Holiday List',
                        'What are the practice holidays?',
                        'Practice holidays: ' + ', '.join(holidays))
        if r: records.append(r)

    return records


def parse_provider_info(rows, practice_name, source_file):
    records = []
    if not rows:
        return records
    header = [clean(h).lower() for h in rows[0]]
    for row in rows[1:]:
        if not nonempty(row):
            continue
        provider = cell(row, 0)
        if not provider or provider.lower() in ('provider name', ''):
            continue
        parts = []
        for i, h in enumerate(header):
            v = cell(row, i)
            if v and h:
                parts.append(f"{h.title()}: {v}")
        instr = '; '.join(parts)
        r = make_record(practice_name, source_file,
                        'Provider Information',
                        f'What are the details for provider {provider}?',
                        instr)
        if r: records.append(r)
    return records


def parse_billing_status(rows, practice_name, source_file):
    """
    Parse Billing Status Overview sheet.
    Structure: header row with [Billing status | Purpose | Client | Drchrono | Type | Status]
    Each row = one billing status entry.
    """
    records = []
    if not rows:
        return records

    # Find the header row (contains 'billing status' and 'purpose')
    header_idx = None
    for i, row in enumerate(rows):
        joined = ' '.join(clean(v).lower() for v in row if v.strip())
        if 'billing status' in joined and 'purpose' in joined:
            header_idx = i
            break

    if header_idx is None:
        # Fallback to generic kv parser
        return parse_kv_sheet(rows, practice_name, source_file, 'Billing Status Overview')

    # Collect all status rows
    all_statuses = []
    for row in rows[header_idx + 1:]:
        status_name = cell(row, 0)
        purpose     = cell(row, 1)
        client_resp = cell(row, 2)
        drc_resp    = cell(row, 3)
        status_type = cell(row, 4)
        active_flag = cell(row, 5)

        # Skip section headers, empty rows, and "Table N" rows
        if not status_name:
            continue
        if status_name.lower().startswith('table') or 'rcm function' in status_name.lower():
            continue
        if status_name.lower() in ('billing status', 'description:', ''):
            continue

        # Normalize responsibility — accept '1', 'TRUE', 'true', 'yes', 'x'
        def _is_yes(v):
            return v.strip().lower() in ('1', 'true', 'yes', 'x', '✓')

        # Build instruction
        responsible = []
        if _is_yes(client_resp): responsible.append('Client/Practice')
        if _is_yes(drc_resp):    responsible.append('DrChrono/RCM Team')
        resp_str = ' & '.join(responsible) if responsible else 'RCM Team'

        # Normalize active flag
        active_normalized = 'ACTIVE' if active_flag.lower() in ('active', '1', 'true', 'yes', '') else active_flag.upper()

        instr = (f"Billing Status: {status_name}. "
                 + (f"Purpose: {purpose}. " if purpose else '')
                 + f"Responsible Party: {resp_str}."
                 + (f" Type: {status_type}." if status_type else '')
                 + (f" Status: {active_normalized}." if active_normalized else ''))

        all_statuses.append(status_name)

        r = make_record(
            practice_name, source_file,
            'Billing Status Overview',
            f'What does the billing status "{status_name}" mean?',
            instr,
            status=active_normalized if active_normalized else 'ACTIVE'
        )
        if r: records.append(r)

    # Also add a summary record listing all statuses
    if all_statuses:
        r = make_record(
            practice_name, source_file,
            'Billing Status Overview',
            'What are all the billing statuses?',
            'All billing statuses: ' + ', '.join(all_statuses)
        )
        if r: records.append(r)

    return records


def parse_kv_sheet(rows, practice_name, source_file, category, scope='General'):
    """
    Parse a sheet that is primarily question | answer columns.
    Handles Prebilling Checklist, Workflow Overview, etc.
    """
    records = []
    directive = ''
    description = ''

    for row in rows:
        text = ' '.join(nonempty(row))
        if text.lower().startswith('directive:'):
            directive = text[10:].strip()
        elif text.lower().startswith('description:'):
            description = text[12:].strip()

        # Two-column Q&A rows — no minimum length filter on the question
        if len(nonempty(row)) >= 2:
            col0 = cell(row, 0)
            col1 = cell(row, 1)
            col2 = cell(row, 2) if len(row) > 2 else ''

            # Detect 3-column format: SI.NO | Questions | Comments
            # col0 is a number, col1 is the question, col2 is the answer
            if col0.replace('.', '').isdigit() and col1 and col2:
                q = col1
                a = col2
            elif col0.replace('.', '').isdigit() and col1 and not col2:
                # Issue 8 fix: distinguish row-number (≤3 digits) from real codes
                # (CPT codes are 5 digits, ICD codes are alphanumeric, etc.)
                digits_only = col0.replace('.', '').strip()
                if len(digits_only) <= 3:
                    # Small number = SI.NO row number with no answer — skip
                    continue
                else:
                    # Numeric code (CPT, procedure, etc.) with value in col1
                    q = col0
                    a = col1
            else:
                q = col0
                a = col1

            # Skip known header tokens
            if q.lower() in ('condition', 'directive', 'description', 'question', 'workflow',
                             'area of impact', '', 'new directive', 'date given',
                             'billing status', 'purpose', 'responsible party', 'questions',
                             'si.no', 'sl.no', 's.no'):
                continue
            # Skip rows where q looks like a section header (all caps, short)
            if q.isupper() and len(q) < 40:
                continue
            if a and len(q) >= 3:
                # Valid Q&A pair — include regardless of question length
                instr = f"Q: {q}\nA: {a}"
                r = make_record(practice_name, source_file, category, q, instr, scope=scope)
                if r: records.append(r)
            elif q and not a and len(q) > 30:
                # Long single-column statement
                r = make_record(practice_name, source_file, category, '', q, scope=scope)
                if r: records.append(r)

    if directive:
        r = make_record(practice_name, source_file, category,
                        f'What is the {category} directive?',
                        directive + (': ' + description if description else ''),
                        scope=scope)
        if r: records.append(r)

    return records


def parse_denial_sheet(rows, practice_name, source_file):
    """Parse Denial Guidelines sheet: scenarios + new directives table."""
    records = []
    directive_desc = ''
    in_new_directives = False
    new_dir_header = []

    for i, row in enumerate(rows):
        text = ' '.join(nonempty(row))

        if text.lower().startswith('directive:'):
            directive_desc = text[10:].strip()
        if text.lower().startswith('description:'):
            directive_desc += ' ' + text[12:].strip()

        # Detect "New directive" header row
        if any('new directive' in v.lower() for v in row):
            in_new_directives = True
            new_dir_header = [clean(v).lower() for v in row]
            continue

        if in_new_directives:
            # Parse directive rows: [Directive text | Date | Provided by | Given by | Status | Exp]
            nd = nonempty(row)
            if not nd:
                continue
            directive_text = cell(row, 0)
            date_given     = cell(row, 1)
            provided_by    = cell(row, 2)
            status_val     = cell(row, 4) if len(row) > 4 else 'ACTIVE'
            exp_date       = cell(row, 5) if len(row) > 5 else 'NA'
            if directive_text and len(directive_text) > 5:
                r = make_record(practice_name, source_file,
                                'Denial Guidelines',
                                '',
                                directive_text,
                                status=status_val or 'ACTIVE',
                                effective_date=date_given or 'NA',
                                termination_date=exp_date or 'NA',
                                received_from=provided_by)
                if r: records.append(r)
            continue

        # Scenario rows: question in col 0, answer in col 1 (or after pipe)
        q = cell(row, 0)
        a = cell(row, 1)
        if q and len(q) > 20 and not q.lower().startswith('denial scenario'):
            instr = f"Q: {q}\nA: {a}" if a else q
            r = make_record(practice_name, source_file,
                            'Denial Guidelines', q, instr)
            if r: records.append(r)

    return records


def parse_ar_sheet(rows, practice_name, source_file):
    """Parse AR Guidelines sheet.

    Structure:
      - Section header rows: col0 has a short label (no '?'), col1 empty
      - Directive rows: col0 starts with 'Directive:' (may have col1 text)
      - Q&A rows: col0 = question (contains '?'), col1 = answer
      - New-directive table: col0 = 'New directive' header → structured rows follow
    """
    records = []
    category = 'AR Guidelines'
    current_section = ''
    current_directive = ''
    in_directives = False  # new-directive table at bottom

    def _is_section_header(row):
        """Short label row with no answer col and no question mark."""
        q = cell(row, 0)
        a = cell(row, 1)
        if not q or a:
            return False
        return '?' not in q and not q.lower().startswith('directive:') and len(q) <= 80

    def _is_directive(row):
        q = cell(row, 0)
        return bool(q and q.lower().startswith('directive:'))

    for row in rows:
        text = ' '.join(nonempty(row))

        # New-directive table at bottom of sheet
        if 'new directive' in text.lower() and any(
                'new directive' in v.lower() for v in row):
            in_directives = True
            continue

        if in_directives:
            nd = nonempty(row)
            if not nd:
                continue
            directive_text = cell(row, 0)
            date_given     = cell(row, 1)
            provided_by    = cell(row, 2)
            status_val     = cell(row, 3) if len(row) > 3 else 'ACTIVE'
            exp_date       = cell(row, 4) if len(row) > 4 else 'NA'
            if directive_text and len(directive_text) > 5:
                r = make_record(practice_name, source_file, category,
                                '', directive_text,
                                status=status_val or 'ACTIVE',
                                effective_date=date_given or 'NA',
                                termination_date=exp_date or 'NA',
                                received_from=provided_by)
                if r: records.append(r)
            continue

        # Section header (e.g. "Out of network billing", "Paid not posted")
        if _is_section_header(row):
            current_section = cell(row, 0)
            current_directive = ''
            continue

        # Directive line within a section
        if _is_directive(row):
            current_directive = cell(row, 0)
            # Also capture any inline note in col1
            note = cell(row, 1)
            if note:
                current_directive += f' ({note})'
            continue

        # Q&A row
        q = cell(row, 0)
        a = cell(row, 1)
        if q and len(q) > 20:
            # Build rich instruction block with section + directive context
            parts = []
            if current_section:
                parts.append(f"Section: {current_section}")
            if current_directive:
                parts.append(f"Directive: {current_directive}")
            parts.append(f"Q: {q}")
            parts.append(f"A: {a}" if a else "A: (no answer recorded)")
            instr = '\n'.join(parts)
            r = make_record(practice_name, source_file, category, q, instr)
            if r: records.append(r)

    return records


def parse_payment_posting(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Payment Posting')


def parse_statements(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Statements and Patient Engagement')


def parse_reports(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Reports')


def parse_portal_logins(rows, practice_name, source_file):
    records = []
    if not rows:
        return records
    header = [clean(h).lower() for h in rows[0]] if rows else []
    for row in rows[1:]:
        nd = nonempty(row)
        if not nd:
            continue
        parts = []
        for i, h in enumerate(header):
            v = cell(row, i)
            if v and h:
                parts.append(f"{h.title()}: {v}")
        if parts:
            instr = '; '.join(parts)
            r = make_record(practice_name, source_file,
                            'Portal Logins', 'What are the portal login details?', instr)
            if r: records.append(r)
    return records


def parse_practice_overview(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Practice Overview')


def parse_phone_directory(rows, practice_name, source_file):
    """
    Parse an insurance/payer phone number directory sheet.
    Consolidates all payer-phone pairs into one searchable record
    instead of per-row fragments.
    """
    records = []
    if not rows:
        return records
    header = [clean(h).lower() for h in rows[0]] if rows else []
    lines = []
    for row in rows[1:]:
        nd = nonempty(row)
        if not nd:
            continue
        payer = cell(row, 0)
        phone = cell(row, 1) if len(row) > 1 else ''
        extra = '; '.join(
            f"{header[j].title()}: {cell(row, j)}"
            for j in range(2, len(header))
            if j < len(row) and cell(row, j)
        )
        if payer and phone:
            line = f"{payer}: {phone}"
            if extra:
                line += f" ({extra})"
            lines.append(line)
        elif payer and len(nonempty(row)) >= 2:
            parts = [f"{header[j].title()}: {cell(row,j)}" for j in range(len(header)) if j < len(row) and cell(row,j)]
            lines.append('; '.join(parts))
    if lines:
        instr = "Insurance/Payer Phone Directory:\n" + '\n'.join(lines)
        r = make_record(practice_name, source_file, 'Insurances Phone#',
                        'What are the insurance payer phone numbers?', instr)
        if r:
            records.append(r)
    return records


def parse_portal_info(rows, practice_name, source_file):
    """
    Parse a portal login/credential sheet (Portals Info, Portal Login Info).
    Groups rows into consolidated credential blocks.
    """
    records = []
    if not rows:
        return records
    header = [clean(h).lower() for h in rows[0]] if rows else []
    lines = []
    for row in rows[1:]:
        nd = nonempty(row)
        if not nd:
            continue
        parts = []
        for j, h in enumerate(header):
            v = cell(row, j)
            if v and h:
                parts.append(f"{h.title()}: {v}")
        if parts:
            lines.append('; '.join(parts))
    if lines:
        instr = "Portal Login Credentials:\n" + '\n'.join(lines)
        r = make_record(practice_name, source_file, 'Portal Logins',
                        'What are the portal login credentials?', instr)
        if r:
            records.append(r)
    return records


def parse_workflow_overview(rows, practice_name, source_file):
    """
    Parse Workflow Overview sheets that contain the Standard/Alternative RCM
    Functions table.  Columns:
        0 – RCM Function name (may be blank for continuation rows)
        1 – Action description
        2 – Customer responsibility  (True / 'TRUE' / '1' / non-empty = yes)
        3 – DrChrono responsibility (True / 'TRUE' / '1' / non-empty = yes)

    Each unique RCM function gets:
      • One record per action row (with responsibility tagged in instruction)
      • One consolidated summary record that lists ALL actions for that function
    Also produces a master summary record listing every function + who owns it.
    Falls back to generic kv parser if no responsibility columns detected.
    """
    records = []

    def _is_truthy(v):
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ('true', '1', 'yes', 'x')

    def _responsibility(cust, drc):
        parts = []
        if _is_truthy(cust):
            parts.append('Customer/Practice')
        if _is_truthy(drc):
            parts.append('DrChrono/RCM Team')
        return ' & '.join(parts) if parts else 'DrChrono/RCM Team'

    # Detect whether this sheet really has responsibility columns
    has_resp_cols = any(
        _is_truthy(cell(row, 2)) or _is_truthy(cell(row, 3))
        for row in rows
    )
    if not has_resp_cols:
        return parse_kv_sheet(rows, practice_name, source_file, 'Workflow Overview')

    # Skip table-header / intro rows
    skip_questions = {
        'standard rcm functions', 'alternative rcm functions',
        'rcm functions', 'actions', 'responsibility', 'customer', 'drchrono',
        '', 'standard rcm functions and associated actions',
        'alternative rcm functions and associated actions',
    }

    current_function = None   # name of the current RCM function
    function_actions = {}     # function_name -> list of (action, responsibility)
    master_summary = []       # (function, responsibility) for top-level summary

    for row in rows:
        fn   = cell(row, 0)
        act  = cell(row, 1)
        cust = cell(row, 2) if len(row) > 2 else None
        drc  = cell(row, 3) if len(row) > 3 else None

        # Skip blank rows and header rows
        if not fn and not act:
            continue
        if fn and fn.lower().strip() in skip_questions:
            continue
        if fn and fn.lower().startswith('table ') and not act:
            continue

        # New function row — fn is non-blank
        if fn:
            current_function = fn
            if fn not in function_actions:
                function_actions[fn] = []

        if not current_function or not act:
            continue

        resp = _responsibility(cust, drc)
        function_actions[current_function].append((act, resp))

    # Build per-function records + master list
    for fn, actions in function_actions.items():
        if not actions:
            continue

        # Determine overall responsibility for the function (may be shared)
        resp_set = set(r for _, r in actions)
        overall_resp = ' & '.join(sorted(resp_set))
        master_summary.append((fn, overall_resp))

        # One record per action
        for act, resp in actions:
            instr = (f"RCM Function: {fn}\n"
                     f"Responsibility: {resp}\n"
                     f"Action: {act}")
            q = f"Who is responsible for {fn}?"
            r = make_record(practice_name, source_file,
                            'Workflow Overview', q, instr)
            if r:
                records.append(r)

        # Consolidated summary for this function (all actions)
        all_actions_text = '\n'.join(f"  - [{r}] {a}" for a, r in actions)
        summary_instr = (f"RCM Function: {fn}\n"
                         f"Overall Responsibility: {overall_resp}\n"
                         f"All actions:\n{all_actions_text}")
        r = make_record(practice_name, source_file,
                        'Workflow Overview',
                        f"What are all the actions and responsibilities for {fn}?",
                        summary_instr)
        if r:
            records.append(r)

    # Master summary record: all functions + top-level ownership
    if master_summary:
        lines = '\n'.join(f"  - {fn}: {resp}" for fn, resp in master_summary)
        instr = (f"Standard RCM Functions and responsibilities:\n{lines}")
        r = make_record(practice_name, source_file,
                        'Workflow Overview',
                        'What are all the standard RCM functions and who is responsible?',
                        instr)
        if r:
            records.append(r)

    return records


def parse_coding_guidelines(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Coding Guidelines')


def parse_kv_sheet_billing(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Billing Guidelines')


def parse_prebilling(rows, practice_name, source_file):
    return parse_kv_sheet(rows, practice_name, source_file, 'Prebilling Checklist')


# ─── Sheet dispatcher ────────────────────────────────────────────────────────

SHEET_PARSERS = {
    'general practice information' : parse_general_practice,
    'general practice information ': parse_general_practice,
    'provider information'          : parse_provider_info,
    'billing status overview'       : parse_billing_status,
    'prebilling checklist'          : parse_prebilling,
    'prebilling check list'         : parse_prebilling,
    'denial guidelines'             : parse_denial_sheet,
    'ar guidelines'                 : parse_ar_sheet,
    'payment posting'               : parse_payment_posting,
    'payment post'                  : parse_payment_posting,
    'statements and patient engageme': parse_statements,
    'statements & patient engagement': parse_statements,
    'statements and patient engagement': parse_statements,
    'statement & patient engament'  : parse_statements,   # Ditto Surgical typo
    'statement & patient engagement': parse_statements,
    'patient engagement'            : parse_statements,   # Greenwich Pure Medical
    'reports'                       : parse_reports,
    'portal logins'                 : parse_portal_logins,
    'portal login'                  : parse_portal_logins,
    'portal login info'             : parse_portal_info,  # Greenwich variant
    'portals info'                  : parse_portal_info,  # Greenwich variant
    'insurances phone#'             : parse_phone_directory,
    'insurances phone'              : parse_phone_directory,
    'insurance phone#'              : parse_phone_directory,
    'billing guidelines'            : parse_kv_sheet_billing,
    'practice overview'             : parse_practice_overview,
    'workflow overview'             : parse_workflow_overview,
    'coding guidelines'             : parse_coding_guidelines,
}


# ─── DOCX parser for Everhealth DOCX SOPs ───────────────────────────────────

def parse_everhealth_docx(filepath):
    """
    Parse Everhealth-style DOCX files (Partners in Mental Health, PearlandPsych etc.)
    Handles two document styles:
      1. Form-style: tab-delimited key: value paragraphs + structured tables
      2. Q&A style: question paragraphs (ending with ?) followed by answer paragraphs
    Both styles may appear in the same document.
    """
    import docx as _docx
    import re as _re

    doc           = _docx.Document(filepath)
    practice_name = _practice_name_from_filename(filepath)
    source_file   = os.path.basename(filepath)
    records       = []

    # ── Known heading/section markers ─────────────────────────────────────────
    _SECTION_MARKERS = {
        'group information', 'provider information', 'additional payers',
        'pay to address', 'pay to address & billing office locations',
        'insurance payer information', 'fee schedules', 'websites',
        'eligibility benefit and auth verification',
    }
    _SKIP_CONTENT = {
        'click or tap here to enter text.', 'enter text here',
        'n/a', 'na', 'yes', 'no', 'no.', 'yes.', '',
    }
    _PLACEHOLDER_RE = _re.compile(
        r'^(click or tap|enter text|n/?a\.?|yes\.?|no\.?)$', _re.I
    )

    def _is_section_heading(txt):
        tl = txt.strip().lower().rstrip('.')
        if tl in _SECTION_MARKERS:
            return True
        if txt.strip().startswith('Section'):
            return True
        if 'heading' in txt:
            return True
        return False

    def _is_kv_para(txt):
        """Tab-delimited or colon-separated key:value paragraph."""
        return '\t' in txt or (': ' in txt and not txt.endswith('?'))

    def _parse_kv_para(txt):
        """
        Split a tab/colon paragraph into (field, value) pairs.
        e.g. 'Legal Business Name:\t\t\tPearland Psychiatry' -> [('Legal Business Name', 'Pearland Psychiatry')]
        """
        pairs = []
        # Split on tabs first, then collapse whitespace in each chunk
        parts = [p.strip() for p in txt.split('\t') if p.strip()]
        # Rejoin parts that look like "Key: Value" pairs
        i = 0
        while i < len(parts):
            chunk = parts[i]
            if ':' in chunk:
                idx = chunk.index(':')
                k = chunk[:idx].strip()
                v = chunk[idx+1:].strip()
                if not v and i + 1 < len(parts) and ':' not in parts[i+1]:
                    v = parts[i+1].strip()
                    i += 1
                pairs.append((k, v))
            elif pairs:
                # continuation value for prior key
                pairs[-1] = (pairs[-1][0], (pairs[-1][1] + ' ' + chunk).strip())
            i += 1
        # Filter out empty values and placeholder text
        return [(k, v) for k, v in pairs
                if k and v and not _PLACEHOLDER_RE.match(v)]

    def _is_numbered_update(txt):
        return bool(_re.match(r'^\s*\d+[\.\)]\s', txt))

    def _is_date_line(txt):
        return bool(_re.match(r'^\d{1,2}/\d{1,2}/\d{4}', txt.strip()) or
                    _re.match(r'^(update|date)[- ]*\d', txt.strip(), _re.I))

    # ── Pass 1: collect all non-empty paragraphs with their raw text ───────────
    all_paras = [(p.text, p.style.name) for p in doc.paragraphs]
    paras = [(t.strip(), s) for t, s in all_paras if t.strip()]

    current_category = 'General Practice Information'
    i = 0

    # Accumulate form-style kv pairs into grouped records per section
    kv_section_parts  = []   # list of "Key: Value" strings for current section
    kv_section_cat    = current_category

    def _flush_kv(parts, cat):
        if not parts:
            return None
        instr = '\n'.join(parts)
        _cat_label = cat.lower().replace(' information', '').strip()
        q = f'What is the {_cat_label} information?'
        return make_record(practice_name, source_file, cat, q, instr)

    while i < len(paras):
        txt, style = paras[i]
        tl = txt.lower().strip()

        # ── Heading detection ──────────────────────────────────────────────────
        if (_is_section_heading(txt) or 'heading' in style.lower()):
            # Flush pending kv block
            r = _flush_kv(kv_section_parts, kv_section_cat)
            if r:
                records.append(r)
            kv_section_parts = []

            # Map known heading strings to clean category names
            _heading_map = {
                'group information':                    'General Practice Information',
                'provider information':                 'Provider Information',
                'additional payers':                    'Payer Information',
                'insurance payer information':          'Payer Information',
                'pay to address':                       'General Practice Information',
                'pay to address & billing office locations': 'General Practice Information',
                'fee schedules':                        'Billing Guidelines',
                'websites':                             'Portal Logins',
                'eligibility benefit and auth verification': 'Billing Guidelines',
            }
            tl_stripped = tl.rstrip('.')
            current_category = _heading_map.get(tl_stripped, txt.strip().title())
            kv_section_cat = current_category
            i += 1
            continue

        # ── Section F / Section A style headings ──────────────────────────────
        if _re.match(r'^section\s+[a-z]:', txt, _re.I):
            r = _flush_kv(kv_section_parts, kv_section_cat)
            if r:
                records.append(r)
            kv_section_parts = []
            current_category = txt.strip().title()
            kv_section_cat = current_category
            i += 1
            continue

        # ── Q&A detection: paragraph ends with '?' ────────────────────────────
        if txt.endswith('?'):
            # Flush pending kv block first
            r = _flush_kv(kv_section_parts, kv_section_cat)
            if r:
                records.append(r)
            kv_section_parts = []

            # Collect answer lines until next question/heading
            answer_lines = []
            j = i + 1
            while j < len(paras):
                nxt, nxt_style = paras[j]
                if nxt.endswith('?') or _is_section_heading(nxt) or \
                   _re.match(r'^section\s+[a-z]:', nxt, _re.I):
                    break
                if not _PLACEHOLDER_RE.match(nxt.strip()):
                    answer_lines.append(nxt.strip())
                j += 1

            if answer_lines:
                answer = ' '.join(answer_lines)
                instr = f"Q: {txt}\nA: {answer}"
                r = make_record(practice_name, source_file, current_category, txt, instr)
                if r:
                    records.append(r)
            i = j
            continue

        # ── Portal login block detection: "Payer/Portal Website: X" ──────────
        if _re.match(r'^payer\s*/?\s*portal\s*website\s*:', txt, _re.I):
            r = _flush_kv(kv_section_parts, kv_section_cat)
            if r:
                records.append(r)
            kv_section_parts = []

            # Collect username/password lines following
            portal_parts = [txt.strip()]
            j = i + 1
            while j < len(paras) and j < i + 5:
                nxt, _ = paras[j]
                if _re.match(r'^(username|password|payer|please enter)', nxt, _re.I):
                    if not _PLACEHOLDER_RE.match(nxt.strip()):
                        portal_parts.append(nxt.strip())
                    j += 1
                else:
                    break
            instr = '\n'.join(portal_parts)
            r = make_record(practice_name, source_file, 'Portal Logins', '', instr)
            if r:
                records.append(r)
            i = j
            continue

        # ── Numbered update lines (e.g. "18. can the billing team...") ────────
        if _is_numbered_update(txt) or _is_date_line(txt):
            # Collect the update block
            update_lines = [txt]
            j = i + 1
            while j < len(paras):
                nxt, _ = paras[j]
                if nxt.endswith('?') or _is_section_heading(nxt) or \
                   _is_numbered_update(nxt) or _is_date_line(nxt) or \
                   _re.match(r'^section\s+[a-z]:', nxt, _re.I):
                    break
                update_lines.append(nxt)
                j += 1
            instr = ' '.join(update_lines)
            if len(instr) > 30:
                r = make_record(practice_name, source_file, current_category, '', instr)
                if r:
                    records.append(r)
            i = j
            continue

        # ── Tab-delimited / colon kv paragraph ────────────────────────────────
        if _is_kv_para(txt):
            pairs = _parse_kv_para(txt)
            for k, v in pairs:
                kv_section_parts.append(f"{k}: {v}")
            i += 1
            continue

        # ── Standalone instruction (not a placeholder) ─────────────────────────
        if len(txt) > 30 and not _PLACEHOLDER_RE.match(txt.strip()):
            instr = txt.strip()
            r = make_record(practice_name, source_file, current_category, '', instr)
            if r:
                records.append(r)

        i += 1

    # Flush any remaining kv block
    r = _flush_kv(kv_section_parts, kv_section_cat)
    if r:
        records.append(r)

    # ── Parse tables ───────────────────────────────────────────────────────────
    for ti, table in enumerate(doc.tables):
        if not table.rows:
            continue
        # Detect table type by header row
        header_cells = [clean(c.text) for c in table.rows[0].cells]
        header_str   = ' '.join(h.lower() for h in header_cells if h)

        # 2-column key:value table (Group Info, Provider Info)
        if len(header_cells) == 2 and ':' in header_cells[0]:
            # All rows are key:value, including row 0
            _cat_map = {
                'legal business name':   'General Practice Information',
                'provider name':         'Provider Information',
                'individual tax id':     'Provider Information',
                'individual npi':        'Provider Information',
            }
            parts = []
            cat = 'General Practice Information'
            for row in table.rows:
                cells = [clean(c.text) for c in row.cells]
                k = cells[0].rstrip(':').strip()
                v = cells[1].strip() if len(cells) > 1 else ''
                if not k:
                    continue
                if not v or _PLACEHOLDER_RE.match(v):
                    continue
                for marker, mapped_cat in _cat_map.items():
                    if marker in k.lower():
                        cat = mapped_cat
                        break
                parts.append(f"{k}: {v}")
            if parts:
                instr = '\n'.join(parts)
                _cat_label2 = cat.lower().replace(' information', '').strip()
                q = f'What is the {_cat_label2} information?'
                r = make_record(practice_name, source_file, cat, q, instr)
                if r:
                    records.append(r)

        # Payer table: Payer | Credentialed | Network | Individual/Group
        elif 'payer' in header_str and ('credentialed' in header_str or 'network' in header_str):
            payer_lines = []
            for row in table.rows[1:]:
                cells = [clean(c.text) for c in row.cells]
                if not cells[0] or cells[0].lower() in ('payer', ''):
                    continue
                parts = []
                for j, h in enumerate(header_cells):
                    v = cells[j] if j < len(cells) else ''
                    if v and h and v not in ('_', '-', ''):
                        parts.append(f"{h}: {v}")
                if parts:
                    payer_lines.append('; '.join(parts))
            if payer_lines:
                instr = 'Payer credentialing status:\n' + '\n'.join(payer_lines)
                r = make_record(practice_name, source_file, 'Payer Information',
                                'What payers is the practice credentialed with?', instr)
                if r:
                    records.append(r)

        # Generic table: use header row as field names
        else:
            for row in table.rows[1:]:
                cells = [clean(c.text) for c in row.cells]
                if not any(cells):
                    continue
                parts = []
                for j, h in enumerate(header_cells):
                    v = cells[j] if j < len(cells) else ''
                    if v and h and not _PLACEHOLDER_RE.match(v):
                        parts.append(f"{h}: {v}")
                if parts:
                    instr = '; '.join(parts)
                    if len(instr) > 20:
                        r = make_record(practice_name, source_file, 'General', '', instr)
                        if r:
                            records.append(r)

    return records


# ─── CSV parser ──────────────────────────────────────────────────────────────

def parse_csv(filepath):
    """Parse a CSV SOP file. Treat as generic Q|A or key|value pairs."""
    practice_name = _practice_name_from_filename(filepath)
    source_file   = os.path.basename(filepath)
    records = []

    with open(filepath, encoding='utf-8-sig', errors='replace') as f:
        reader = csv.reader(f)
        rows = [row for row in reader]

    if not rows:
        return records

    # Detect header row
    header_row = rows[0]
    header = [clean(h).lower() for h in header_row]

    # Detect category from filename
    fname_lower = os.path.basename(filepath).lower()
    if 'general practice' in fname_lower:
        category = 'General Practice Information'
    elif 'statement' in fname_lower or 'patient engagement' in fname_lower:
        category = 'Statements and Patient Engagement'
    else:
        category = 'General'

    for row in rows[1:]:
        if not any(clean(c) for c in row):
            continue
        parts = []
        for i, h in enumerate(header):
            v = cell(row, i)
            if v and h and h not in ('', 'nan'):
                parts.append(f"{h.title()}: {v}")
        if parts:
            instr = '\n'.join(parts)
            # Use first cell as question hint if it looks like a question
            q = cell(row, 0)
            question = q if q.endswith('?') else ''
            r = make_record(practice_name, source_file, category, question, instr)
            if r: records.append(r)

    return records


# ─── Main Excel converter ────────────────────────────────────────────────────

def convert_xlsx(filepath):
    practice_name = _practice_name_from_filename(filepath)
    source_file   = os.path.basename(filepath)
    all_records   = []

    sheets = read_xlsx(filepath)

    for sheet_name, rows in sheets.items():
        key = sheet_name.lower().strip()
        parser = None
        for k, fn in SHEET_PARSERS.items():
            # Issue 1 fix: tighter matching — require meaningful overlap length
            # to prevent short keys accidentally matching unrelated sheet names.
            exact       = (k == key)
            key_in_k    = key.startswith(k) and len(k) >= 10   # file sheet starts with dict key
            k_in_key    = k.startswith(key) and len(key) >= 10  # dict key starts with file sheet
            if exact or key_in_k or k_in_key:
                parser = fn
                break

        if parser is None:
            # Generic fallback for unknown sheets
            parser = lambda rows, pn, sf, sn=sheet_name: parse_kv_sheet(
                rows, pn, sf, sn.title())

        try:
            recs = parser(rows, practice_name, source_file)
            all_records.extend(recs)
        except Exception as e:
            print(f"  WARNING: Sheet '{sheet_name}' parse error: {e}")

    return all_records


# ─── Build JSON output ───────────────────────────────────────────────────────

def convert_file(filepath, out_dir):
    ext = os.path.splitext(filepath)[1].lower()
    fname = os.path.basename(filepath)

    if ext in ('.xlsx', '.xls'):
        records = convert_xlsx(filepath)
    elif ext == '.csv':
        records = parse_csv(filepath)
    elif ext == '.docx':
        records = parse_everhealth_docx(filepath)
    else:
        print(f"  Skipping unsupported: {fname}")
        return 0

    if not records:
        print(f"  {fname}: 0 records (skipped)")
        return 0

    # Deduplicate by question + instruction text
    seen, deduped = set(), []
    for r in records:
        key = (r['question'].strip().lower()[:100] + '||' +
               r['instruction'].strip().lower()[:200])
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    out_name = os.path.splitext(fname)[0] + '.json'
    out_path = os.path.join(out_dir, out_name)

    source_file = os.path.basename(filepath)
    payload = {
        'source_file': source_file,
        'practice_name': _practice_name_from_filename(filepath),
        'team': 'Everhealth',
        'converted_on': TODAY,
        'records': deduped,
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"  {fname}: {len(deduped)} records -> {out_name}")
    return len(deduped)


def main():
    data_dir = os.path.join('data', 'Everhealth SOPs')
    out_dir  = 'data_schema'
    os.makedirs(out_dir, exist_ok=True)

    files = []
    for ext in ('*.xlsx', '*.xls', '*.csv', '*.docx'):
        files.extend(Path(data_dir).glob(ext))

    print(f"Converting Everhealth SOP files...")
    total = 0
    for fp in sorted(files):
        if os.path.basename(str(fp)).startswith('~$'):
            continue  # skip Windows temp lock files
        total += convert_file(str(fp), out_dir)

    print(f"\nDone. Total records: {total}")


if __name__ == '__main__':
    main()
