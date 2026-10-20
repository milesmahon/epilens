"""Workbooks for import tests"""

import io

import openpyxl


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def contact_sheet() -> dict[str, list[list]]:
    head = ["Chan #s", "Contact", "Tissue type", "MNI X,Y,Z_1", "MNI X,Y,Z_2", "MNI X,Y,Z_3",
            "Native X,Y,Z_1", "Native X,Y,Z_2", "Native X,Y,Z_3"]
    rows, ch = [head], 0

    def add(label, x, y, z):
        nonlocal ch
        ch += 1
        rows.append([ch, label, "Gray", x, y, z, x + 100, y + 100, z + 100])

    rhh = [26 + 3.5 * i for i in range(4)] + [26 + 3.5 * 3 + 30 + 3.5 * i for i in range(4)]   # tip first
    for k, x in enumerate(reversed(rhh), 1):
        add(f"RHH{k}", x, -20, -12)
    for k in range(1, 7):
        add(f"LA{k}", -24 - 4 * (6 - k), -4, -20)
    for k in range(1, 7):
        add(f"Grid{k}", -60, -20 + 10 * ((k - 1) % 3), 10 * ((k - 1) // 3))
    return {"Sheet1": rows, "micros": [["Electrode", "Chans"]]}
