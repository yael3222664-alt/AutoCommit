"""Export extracted records to CSV / JSON / Excel (one row per commitment number).

The קופה issues one commitment (התחייבות) per approved billing item, so a single
page can carry several commitment numbers. The commitment number is the row key
in the export: each commitment gets its own row and the patient/sample
details (name, ID, sender, central number, received date) repeat on every row.
Records with no commitment number still produce one row, with an empty
commitment cell, so they are not silently dropped from the export.
"""

import csv
import io
import json
from collections.abc import Iterable

from openpyxl import Workbook

from ..schemas import RecordResponse

HEADERS = [
    "מספר התחייבות",
    "סוג מספר",
    "שם פרטי",
    "שם משפחה",
    "מספר זהות",
    "מספר מרכזי",
    "תאריך קבלה",
    "גורם שולח",
    "מספר עמוד",
    "מזהה רשומה",
]

# Mirrors COMMITMENT_LABELS in the frontend groundedFields.
COMMITMENT_LABEL_TYPES = {
    "commitment": "טופס 17",
    "reference": "אסמכתא",
    "order": "מספר דרישה",
    "sample": "דגימה",
}
DEFAULT_COMMITMENT_LABEL = "מספר התחייבות"


def record_rows(records: Iterable[RecordResponse]) -> list[list[str]]:
    """One row per commitment number; patient details repeat on every row."""
    rows = []
    for record in records:
        patient = [
            record.first_name.value or "",
            record.last_name.value or "",
            record.id_number.value or "",
            ", ".join(item.value for item in record.central_numbers),
            record.received_date.value or "",
            record.sender.value or "",
            str(record.page_number),
            record.id,
        ]
        commitments = record.commitment_numbers or [None]
        for item in commitments:
            rows.append([
                item.value if item else "",
                COMMITMENT_LABEL_TYPES.get(item.label_type or "", DEFAULT_COMMITMENT_LABEL) if item else "",
                *patient,
            ])
    return rows


def records_to_csv(records: Iterable[RecordResponse]) -> bytes:
    """UTF-8-sig CSV (opens correctly in Excel) with one row per commitment number."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    writer.writerows(record_rows(records))
    return buffer.getvalue().encode("utf-8-sig")


def records_to_json(records: Iterable[RecordResponse]) -> bytes:
    """JSON array of row objects (same one-row-per-commitment shape as the CSV)."""
    rows = [dict(zip(HEADERS, row)) for row in record_rows(records)]
    return json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")


def records_to_xlsx(records: Iterable[RecordResponse]) -> bytes:
    """Real .xlsx workbook, one row per commitment number."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "records"
    sheet.append(HEADERS)
    for row in record_rows(records):
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


EXPORT_FORMATS = {
    "csv": ("autocommit-export.csv", "text/csv; charset=utf-8", records_to_csv),
    "json": ("autocommit-export.json", "application/json; charset=utf-8", records_to_json),
    "xlsx": (
        "autocommit-export.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        records_to_xlsx,
    ),
}
