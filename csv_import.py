# -*- coding: utf-8 -*-
"""
csv_import.py — 清单导入与识别模块
--------------------------------
功能：
1. 自动识别清单文件编码（UTF-8 / GBK 常见于本系统导出的 CSV）；
2. 自动识别表头列（条码 / 文件位置），也兼容“无表头”的两列格式；
3. 逐行校验：条码是否为空、文件夹是否存在、文件夹内文件数；
4. 返回结构化任务列表与解析报告。

支持的清单格式示例（与“导出清单_日期.csv”一致）：
    条码,文件位置
    110000044416,E:\\桌面\\110000044416
    110000044417,E:\\桌面\\110000044417
"""
import csv
import io
import os
from dataclasses import dataclass, field
from typing import List, Optional

# 可能的列名关键字（左：内容含义, 右：关键字列表）
BARCODE_KEYS = ["条码", "条形码", "编号", "衣物条码", "barcode", "code"]
PATH_KEYS = ["文件位置", "文件夹", "路径", "位置", "存储位置", "folder", "path"]

ENCODINGS = ["utf-8-sig", "gbk", "utf-8", "gb18030", "big5"]


def _try_decode(raw: bytes):
    """依次尝试各编码，返回 (text, encoding) 或 (None, None)。"""
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return None, None


def _find_col(header, keys):
    """在表头中找包含任一关键字的列下标。"""
    for i, name in enumerate(header):
        low = (name or "").strip().lower()
        for k in keys:
            if k.lower() in low:
                return i
    return None


def _normalize_path(p: str) -> str:
    """去掉首尾引号/空白，统一斜杠风格（保留原始盘符路径）。"""
    p = (p or "").strip().strip('"').strip("'")
    return p


@dataclass
class TaskRecord:
    index: int = 0                  # 序号（从 1 开始）
    barcode: str = ""               # 条码
    folder: str = ""                # 文件位置（文件夹路径）
    folder_exists: bool = False     # 文件夹是否存在
    file_count: int = 0             # 文件夹内文件数量（不含子目录）
    status: str = "待执行"           # 待执行 / 进行中 / 成功 / 失败 / 跳过
    note: str = ""                  # 备注（校验问题或执行结果）
    duration: float = 0.0           # 执行耗时（秒）

    @property
    def display_files(self):
        return str(self.file_count) if self.folder_exists else "-"


@dataclass
class ParseReport:
    file_path: str = ""
    encoding: str = ""
    header_used: bool = False
    barcode_col: int = -1
    path_col: int = -1
    total_rows: int = 0
    valid_rows: int = 0
    issues: List[str] = field(default_factory=list)

    def summary(self) -> str:
        s = [f"文件：{os.path.basename(self.file_path)}",
             f"编码：{self.encoding}",
             f"表头列：{'自动识别' if self.header_used else '按前两列解析'}",
             f"数据行：{self.total_rows}  条码有效行：{self.valid_rows}"]
        if self.issues:
            s.append(f"提示：{len(self.issues)} 条")
        return " ; ".join(s)


def import_list(file_path: str, check_folder: bool = True) -> tuple:
    """
    导入清单。返回 (tasks: List[TaskRecord], report: ParseReport)。
    抛出 ValueError 表示文件无法解析（不可恢复错误）。
    """
    report = ParseReport(file_path=file_path)

    with open(file_path, "rb") as f:
        raw = f.read()
    text, enc = _try_decode(raw)
    if text is None:
        raise ValueError("无法识别文件编码（支持 UTF-8 / GBK 等文本 CSV）。")
    report.encoding = enc

    # 去掉 BOM 残留和空行，按 CSV 解析（兼容逗号/制表符/分号）
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    rows = [r for r in rows if any((c or "").strip() for c in r)]
    if not rows:
        raise ValueError("清单文件为空。")

    # 找表头
    bcol = pcol = None
    header = rows[0]
    if len(header) >= 2:
        bcol = _find_col(header, BARCODE_KEYS)
        pcol = _find_col(header, PATH_KEYS)
    if bcol is not None and pcol is not None:
        report.header_used = True
        data_rows = rows[1:]
    else:
        # 无表头（或表头识别失败）：默认第 1 列条码、第 2 列路径
        report.header_used = False
        bcol, pcol = 0, 1
        data_rows = rows
        report.issues.append("未识别到标准表头，按“第1列=条码、第2列=文件位置”解析。")
    report.barcode_col, report.path_col = bcol, pcol

    tasks: List[TaskRecord] = []
    idx = 0
    for rno, row in enumerate(data_rows, start=1):
        if len(row) <= max(bcol, pcol):
            row = row + [""] * (max(bcol, pcol) + 1 - len(row))
        barcode = (row[bcol] or "").strip()
        folder = _normalize_path(row[pcol] if pcol < len(row) else "")
        if not barcode and not folder:
            continue  # 全空行
        idx += 1
        rec = TaskRecord(index=idx, barcode=barcode, folder=folder)

        if not barcode:
            rec.status = "跳过"
            rec.note = "条码为空"
            report.issues.append(f"第{rno}行：条码为空，已跳过。")
        elif not folder:
            rec.status = "跳过"
            rec.note = "文件夹位置为空"
            report.issues.append(f"第{rno}行：{barcode} 文件夹位置为空，已跳过。")
        else:
            if check_folder:
                try:
                    exists = os.path.isdir(folder)
                except OSError:
                    exists = False
                rec.folder_exists = exists
                if exists:
                    try:
                        rec.file_count = sum(
                            1 for n in os.listdir(folder)
                            if os.path.isfile(os.path.join(folder, n)))
                    except OSError as e:
                        rec.file_count = -1
                        report.issues.append(f"{barcode}：读取文件夹失败（{e}）")
                    if rec.file_count == 0:
                        rec.note = "文件夹内没有文件"
                        report.issues.append(f"{barcode}：文件夹为空。")
                else:
                    rec.note = "文件夹不存在"
                    report.issues.append(f"{barcode}：文件夹不存在（{folder}）")
            report.valid_rows += 1 if rec.status == "待执行" else 0

        tasks.append(rec)

    report.total_rows = len(tasks)
    if report.valid_rows == 0 and tasks:
        report.valid_rows = sum(1 for t in tasks if t.status == "待执行")
    return tasks, report


if __name__ == "__main__":
    # 简单自测
    import sys
    if len(sys.argv) > 1:
        ts, rp = import_list(sys.argv[1])
        print(rp.summary())
        for t in ts:
            print(t.index, t.barcode, t.folder, t.folder_exists, t.file_count, t.status, t.note)
