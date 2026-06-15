#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BIN档命名工具
根据命名文档查找表，自动匹配机种名，更新日期和校验和（累加和取低16位），生成规范的BIN文件名。
支持手动修改：屏参名、软体版本、校验和前辍码、其他信息。
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import re
from datetime import datetime
from pathlib import Path

# ============================================================
# 校验和计算：累加所有字节，取低16位
# ============================================================
def calc_checksum(data: bytes) -> str:
    total = sum(data)
    return f"{total & 0xFFFF:04X}"


# ============================================================
# 查找表解析
# ============================================================
def parse_lookup_table(filepath: str) -> list:
    entries = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\r\n').strip()
            if not line:
                continue
            m = re.match(r'^(.+\.bin)\s+([01])\s*$', line)
            if not m:
                continue
            template = m.group(1).strip()
            flag = m.group(2)
            if flag not in ('0', '1'):
                continue
            seq = str(len(entries) + 1)
            entries.append({'seq': seq, 'template': template, 'flag': flag})
    return entries


# ============================================================
# 字段定位函数
# ============================================================
def find_date_field(template: str):
    """日期字段: _YYYYMMDDX_"""
    m = re.search(r'_(\d{8}[A-Z])_', template)
    if m:
        return m.group(1), m.start(1), m.end(1)
    m = re.search(r'_(\d{8}[A-Z])\.bin$', template)
    if m:
        return m.group(1), m.start(1), m.end(1)
    return None, -1, -1


def find_checksum_field(template: str):
    """校验和字段: .bin前最后一个_分隔的4位十六进制"""
    m = re.search(r'_([0-9A-Fa-f]{4})\.bin$', template)
    if m:
        return m.group(1), m.start(1), m.end(1)
    return None, -1, -1


def find_panel_name(template: str) -> tuple[str, int, int]:
    """检测屏参名（取最后一个候选）"""
    stem = template.replace('.bin', '')
    segments = stem.split('_')

    exclude_patterns = [
        r'^\d{3,4}MA$', r'^\d{3}mA$', r'^FHD\d*$', r'^QHD\d*$',
        r'^HD$', r'^FHD$', r'^UHD$', r'^FW\d+$', r'^MT\d+',
        r'^MP\d+', r'^V\d+\.\d+$', r'^[0-9A-Fa-f]{4}$',
        r'^\d{8}[A-Z]$', r'^0x[A-Fa-f0-9]+$', r'^F0x[0-9A-Fa-f]+$',
        r'^BD$', r'^GV\d$', r'^DA\d$', r'^V\+H$', r'^2H1DP$',
        r'^LVDSD$', r'^HF$', r'^Min\d+ma$', r'^Max\d+ma$',
    ]

    def is_excluded(seg: str) -> bool:
        for pat in exclude_patterns:
            if re.match(pat, seg, re.IGNORECASE):
                return True
        return False

    panel_core_pattern = r'^(?:Panel)?([A-Z]{1,3})(\d{3})([A-Za-z0-9.]*)$'
    candidates = []

    for i, seg in enumerate(segments):
        if is_excluded(seg):
            continue
        m = re.match(panel_core_pattern, seg)
        if not m:
            continue
        start_idx, end_idx = i, i
        if i + 1 < len(segments):
            next_seg = segments[i + 1]
            if re.match(r'^(\d+|[A-Z]\d+[A-Z]?)$', next_seg):
                if not is_excluded(next_seg):
                    end_idx = i + 1
        panel_name = '_'.join(segments[start_idx:end_idx + 1])
        search_start = 0
        for j in range(start_idx):
            idx = stem.find(segments[j], search_start)
            if idx >= 0:
                search_start = idx + len(segments[j]) + 1
        candidates.append((panel_name, search_start, search_start + len(panel_name)))

    if candidates:
        return candidates[-1]  # 取最后一个（屏参名在靠后位置）
    return "", -1, -1


def find_fw_version(template: str) -> tuple[str, int, int]:
    """检测软体版本: FW008, FW013, V1.00, V1.0, V100 等（取最后一个候选）"""
    stem = template.replace('.bin', '')
    segments = stem.split('_')

    fw_pattern = r'^(?:FW|V)\d+(?:\.\d+)?$'
    candidates = []

    for i, seg in enumerate(segments):
        if re.match(fw_pattern, seg, re.IGNORECASE):
            # 排除 V+H 这类
            if seg.upper() == 'V+H':
                continue
            search_start = 0
            for j in range(i):
                idx = stem.find(segments[j], search_start)
                if idx >= 0:
                    search_start = idx + len(segments[j]) + 1
            candidates.append((seg, search_start, search_start + len(seg)))

    if candidates:
        return candidates[-1]  # 取最后一个
    return "", -1, -1


def find_checksum_prefixes(template: str) -> tuple[list[str], int, int]:
    """
    检测校验和前面的前辍码 (如 0xAC, F0x33, 0x12)
    返回: ([prefix1, prefix2, ...], replace_start, replace_end)
    replace_start 指向第一个前缀的开始，replace_end 指向校验和的开始
    """
    stem = template.replace('.bin', '')
    segments = stem.split('_')

    if len(segments) < 2:
        return [], -1, -1

    # 最后一段是校验和，向前找前辍码
    cs_seg = segments[-1]
    if not re.match(r'^[0-9A-Fa-f]{4}$', cs_seg):
        return [], -1, -1

    prefixes = []
    prefix_indices = []

    # 从校验和前面开始往前找
    for i in range(len(segments) - 2, -1, -1):
        seg = segments[i]
        if re.match(r'^(?:F)?0x[0-9A-Fa-f]+$', seg):
            prefixes.insert(0, seg)
            prefix_indices.insert(0, i)
        else:
            break  # 遇到非前辍码就停止

    if not prefixes:
        return [], -1, -1

    # 计算位置
    search_start = 0
    for j in range(prefix_indices[0]):
        idx = stem.find(segments[j], search_start)
        if idx >= 0:
            search_start = idx + len(segments[j]) + 1

    replace_start = search_start
    # 结束位置：校验和字段的开始
    cs_search_start = 0
    for j in range(len(segments) - 1):
        idx = stem.find(segments[j], cs_search_start)
        if idx >= 0:
            cs_search_start = idx + len(segments[j]) + 1

    return prefixes, replace_start, cs_search_start


# ============================================================
# 机种名匹配
# ============================================================
def match_template(bin_filepath: str, entries: list) -> dict | None:
    bin_stem = Path(bin_filepath).stem
    bin_parts = [p.upper() for p in bin_stem.split('_') if len(p) >= 2]

    best_entry, best_score = None, 0
    for entry in entries:
        template_stem = Path(entry['template']).stem
        template_upper = template_stem.upper()
        template_parts = set(template_upper.split('_'))

        common = [p for p in bin_parts if p in template_parts and len(p) >= 3]
        score = len(common) * 10

        if bin_stem.upper() in template_upper:
            score += 50

        for part in bin_parts:
            if len(part) >= 5 and part in template_upper:
                score += 20

        if score > best_score:
            best_score, best_entry = score, entry

    return best_entry


# ============================================================
# 生成新文件名
# ============================================================
def generate_filename(template: str, flag: str, bin_filepath: str,
                      new_panel_name: str = "",
                      new_fw_version: str = "",
                      new_prefixes: list[str] | None = None,
                      other_info: str = "") -> tuple:
    """
    参数:
        template: 文件名模板
        flag: 日期更新标志
        bin_filepath: BIN文件路径
        new_panel_name: 新屏参名
        new_fw_version: 新软体版本
        new_prefixes: 校验和前辍码列表 ['0xAC', 'F0x33'] 或 []
        other_info: 其他信息，插入到日期前面
    返回: (new_filename, date_info, checksum_info, panel_info, fw_info, prefix_info, other_info_str)
    """
    new_template = template
    panel_info = ""
    fw_info = ""
    prefix_info = ""
    other_info_str = ""

    # 0. 屏参名替换
    if new_panel_name:
        old_panel, p_start, p_end = find_panel_name(new_template)
        if old_panel and p_start >= 0 and new_panel_name != old_panel:
            new_template = new_template[:p_start] + new_panel_name + new_template[p_end:]
            panel_info = f"屏参名: {old_panel} -> {new_panel_name}"
        elif old_panel:
            panel_info = f"屏参名未变: {old_panel}"
    else:
        old_panel, _, _ = find_panel_name(new_template)
        if old_panel:
            panel_info = f"屏参名: {old_panel} (未修改)"

    # 1. 其他信息插入（在日期前面）
    if other_info:
        date_str, d_start, d_end = find_date_field(new_template)
        if date_str:
            # 在日期前插入 _other_info
            new_template = new_template[:d_start] + other_info + "_" + new_template[d_start:]
            other_info_str = f"其他信息: 已添加 \"{other_info}\""
        else:
            other_info_str = "未找到日期字段，无法添加其他信息"

    # 2. 日期处理
    date_str, d_start, d_end = find_date_field(new_template)
    if not date_str:
        raise ValueError("无法在模板中找到日期字段（格式: _YYYYMMDDX_）")

    date_info = ""
    if flag == '1':
        mtime = os.path.getmtime(bin_filepath)
        dt = datetime.fromtimestamp(mtime)
        new_date = dt.strftime('%Y%m%d') + date_str[8]
        new_template = new_template[:d_start] + new_date + new_template[d_end:]
        date_info = f"日期已更新: {date_str} -> {new_date}"
    else:
        date_info = f"日期保持不变: {date_str}"

    # 3. 软体版本替换
    if new_fw_version:
        old_fw, f_start, f_end = find_fw_version(new_template)
        if old_fw and f_start >= 0 and new_fw_version != old_fw:
            new_template = new_template[:f_start] + new_fw_version + new_template[f_end:]
            fw_info = f"软体版本: {old_fw} -> {new_fw_version}"
        elif old_fw:
            fw_info = f"软体版本未变: {old_fw}"
        else:
            # 模板中没有软体版本，在日期后插入
            date_str2, _, d_end2 = find_date_field(new_template)
            if date_str2:
                new_template = new_template[:d_end2] + "_" + new_fw_version + new_template[d_end2:]
                fw_info = f"软体版本: 新增 {new_fw_version}"
    else:
        old_fw, _, _ = find_fw_version(new_template)
        if old_fw:
            fw_info = f"软体版本: {old_fw} (未修改)"

    # 4. 校验和前辍码替换
    if new_prefixes is not None:
        old_prefixes, pfx_start, pfx_end = find_checksum_prefixes(new_template)
        old_pfx_display = "_" + "_".join(old_prefixes) + "_" if old_prefixes else "(无)"
        new_pfx_str = "_".join(new_prefixes) + "_" if new_prefixes else ""
        new_pfx_display = "_" + "_".join(new_prefixes) + "_" if new_prefixes else "(无)"

        if pfx_start >= 0:
            # 替换原有前缀: pfx_start指向第一个前缀的首字符，前面已有_
            new_template = new_template[:pfx_start] + new_pfx_str + new_template[pfx_end:]
        elif new_prefixes:
            # 原来没有前缀，在校验和前面插入: cs_start指向校验和首字符，前面已有_
            cs_str, cs_start, cs_end = find_checksum_field(new_template)
            if cs_str:
                new_template = new_template[:cs_start] + new_pfx_str + new_template[cs_start:]

        if old_pfx_display != new_pfx_display:
            prefix_info = f"前辍码: {old_pfx_display} -> {new_pfx_display}"
        else:
            prefix_info = f"前辍码未变: {new_pfx_display}"

    # 5. 校验和计算
    cs_str, c_start, c_end = find_checksum_field(new_template)
    if not cs_str:
        raise ValueError("无法在模板中找到校验和字段（格式: _XXXX.bin）")

    with open(bin_filepath, 'rb') as f:
        bin_data = f.read()
    new_checksum = calc_checksum(bin_data)
    new_template = new_template[:c_start] + new_checksum + new_template[c_end:]
    checksum_info = f"累加和: {cs_str} -> {new_checksum} (文件大小: {len(bin_data):,} bytes)"

    return new_template, date_info, checksum_info, panel_info, fw_info, prefix_info, other_info_str


# ============================================================
# GUI 应用
# ============================================================
class BinRenameApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("BIN档命名工具")
        self.root.geometry("860x860")
        self.root.resizable(True, True)
        self.root.minsize(740, 720)

        self.bin_filepath: str = ""
        self.entries: list = []
        self.selected_entry: dict | None = None
        self.generated_filename: str = ""
        self.original_panel_name: str = ""
        self.original_fw_version: str = ""
        self.original_prefixes: list[str] = []

        self.lookup_path = Path(__file__).parent / "命名文档查找表.txt"

        self.style = ttk.Style()
        self.style.theme_use('clam')

        self._build_ui()
        self._load_lookup_table()

    # ----- 界面构建 -----
    def _build_ui(self):
        main_frame = ttk.Frame(self.root, padding="12")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 标题
        ttk.Label(main_frame, text="BIN 档 命 名 工 具",
                  font=("Microsoft YaHei", 16, "bold")).pack(pady=(0, 10))

        # ① 文件选择
        file_frame = ttk.LabelFrame(main_frame, text="① 选择 BIN 文件", padding="8")
        file_frame.pack(fill=tk.X, pady=(0, 8))

        file_row = ttk.Frame(file_frame)
        file_row.pack(fill=tk.X)
        self.file_path_var = tk.StringVar()
        ttk.Entry(file_row, textvariable=self.file_path_var, font=("Consolas", 10)).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Button(file_row, text="浏览...", command=self._browse_file, width=10).pack(side=tk.RIGHT)

        self.file_info_var = tk.StringVar(value="尚未选择文件")
        ttk.Label(file_frame, textvariable=self.file_info_var,
                  font=("Microsoft YaHei", 9), foreground="#555").pack(anchor=tk.W, pady=(6, 0))

        # ② 模板选择
        template_frame = ttk.LabelFrame(main_frame, text="② 匹配模板", padding="8")
        template_frame.pack(fill=tk.X, pady=(0, 8))

        tmpl_row = ttk.Frame(template_frame)
        tmpl_row.pack(fill=tk.X)
        ttk.Label(tmpl_row, text="模板:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 8))
        self.template_combo = ttk.Combobox(tmpl_row, font=("Consolas", 9), state="readonly")
        self.template_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.template_combo.bind('<<ComboboxSelected>>', self._on_template_select)

        self.flag_var = tk.StringVar(value="")
        ttk.Label(tmpl_row, textvariable=self.flag_var,
                  font=("Microsoft YaHei", 9, "bold"), foreground="#D2691E", width=22).pack(side=tk.RIGHT)

        self.match_info_var = tk.StringVar(value="")
        ttk.Label(template_frame, textvariable=self.match_info_var,
                  font=("Microsoft YaHei", 9), foreground="#228B22").pack(anchor=tk.W, pady=(4, 0))

        # ③ 日期设置
        date_frame = ttk.LabelFrame(main_frame, text="③ 日期设置", padding="8")
        date_frame.pack(fill=tk.X, pady=(0, 8))

        date_row = ttk.Frame(date_frame)
        date_row.pack(fill=tk.X)
        self.date_update_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(date_row, text="更新日期为 BIN 文件的修改日期",
                        variable=self.date_update_var, command=self._refresh_preview).pack(side=tk.LEFT)
        self.date_detail_var = tk.StringVar(value="")
        ttk.Label(date_row, textvariable=self.date_detail_var,
                  font=("Consolas", 10), foreground="#1E90FF").pack(side=tk.RIGHT)

        # ④ 屏参名
        panel_frame = ttk.LabelFrame(main_frame, text="④ 屏参名（可手动修改）", padding="8")
        panel_frame.pack(fill=tk.X, pady=(0, 8))

        panel_row = ttk.Frame(panel_frame)
        panel_row.pack(fill=tk.X)
        ttk.Label(panel_row, text="屏参名:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 8))
        self.panel_name_var = tk.StringVar(value="")
        self.panel_entry = ttk.Entry(panel_row, textvariable=self.panel_name_var,
                                     font=("Consolas", 11))
        self.panel_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.panel_entry.bind('<KeyRelease>', self._on_field_edit)
        self.panel_info_var = tk.StringVar(value="")
        ttk.Label(panel_row, textvariable=self.panel_info_var,
                  font=("Microsoft YaHei", 9), foreground="#8B008B").pack(side=tk.RIGHT)
        ttk.Button(panel_row, text="恢复", command=self._reset_panel_name, width=6).pack(side=tk.RIGHT, padx=(0, 4))

        # ⑤ 软体版本
        fw_frame = ttk.LabelFrame(main_frame, text="⑤ 软体版本（可手动修改，如 FW008 / V1.00）", padding="8")
        fw_frame.pack(fill=tk.X, pady=(0, 8))

        fw_row = ttk.Frame(fw_frame)
        fw_row.pack(fill=tk.X)
        ttk.Label(fw_row, text="版本:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 8))
        self.fw_version_var = tk.StringVar(value="")
        self.fw_entry = ttk.Entry(fw_row, textvariable=self.fw_version_var,
                                  font=("Consolas", 11), width=20)
        self.fw_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.fw_entry.bind('<KeyRelease>', self._on_field_edit)
        self.fw_info_var = tk.StringVar(value="")
        ttk.Label(fw_row, textvariable=self.fw_info_var,
                  font=("Microsoft YaHei", 9), foreground="#8B008B").pack(side=tk.RIGHT)
        ttk.Button(fw_row, text="恢复", command=self._reset_fw_version, width=6).pack(side=tk.RIGHT, padx=(0, 4))

        # ⑥ 校验和前辍码
        pfx_frame = ttk.LabelFrame(main_frame, text="⑥ 校验和前辍码（可手动修改，如 0xAC / F0x33）", padding="8")
        pfx_frame.pack(fill=tk.X, pady=(0, 8))

        pfx_row = ttk.Frame(pfx_frame)
        pfx_row.pack(fill=tk.X)
        ttk.Label(pfx_row, text="前缀1:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 4))
        self.prefix1_var = tk.StringVar(value="")
        self.pfx1_entry = ttk.Entry(pfx_row, textvariable=self.prefix1_var,
                                    font=("Consolas", 11), width=10)
        self.pfx1_entry.pack(side=tk.LEFT, padx=(0, 12))
        self.pfx1_entry.bind('<KeyRelease>', self._on_field_edit)

        ttk.Label(pfx_row, text="前缀2:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 4))
        self.prefix2_var = tk.StringVar(value="")
        self.pfx2_entry = ttk.Entry(pfx_row, textvariable=self.prefix2_var,
                                    font=("Consolas", 11), width=10)
        self.pfx2_entry.pack(side=tk.LEFT, padx=(0, 12))
        self.pfx2_entry.bind('<KeyRelease>', self._on_field_edit)

        self.pfx_info_var = tk.StringVar(value="")
        ttk.Label(pfx_row, textvariable=self.pfx_info_var,
                  font=("Microsoft YaHei", 9), foreground="#8B008B").pack(side=tk.RIGHT)
        ttk.Button(pfx_row, text="恢复", command=self._reset_prefixes, width=6).pack(side=tk.RIGHT, padx=(0, 4))

        # ⑦ 其他信息
        other_frame = ttk.LabelFrame(main_frame, text="⑦ 其他信息（特殊情况添加到日期前，留空则忽略）", padding="8")
        other_frame.pack(fill=tk.X, pady=(0, 8))

        other_row = ttk.Frame(other_frame)
        other_row.pack(fill=tk.X)
        ttk.Label(other_row, text="信息:", font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=(0, 8))
        self.other_info_var = tk.StringVar(value="")
        self.other_entry = ttk.Entry(other_row, textvariable=self.other_info_var,
                                     font=("Consolas", 11))
        self.other_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.other_entry.bind('<KeyRelease>', self._on_field_edit)
        self.other_status_var = tk.StringVar(value="")
        ttk.Label(other_row, textvariable=self.other_status_var,
                  font=("Microsoft YaHei", 9), foreground="#8B008B").pack(side=tk.RIGHT)
        ttk.Button(other_row, text="清除", command=self._clear_other_info, width=6).pack(side=tk.RIGHT, padx=(0, 4))

        # ⑧ 预览
        preview_frame = ttk.LabelFrame(main_frame, text="⑧ 新文件名预览", padding="8")
        preview_frame.pack(fill=tk.X, pady=(0, 8))

        self.preview_var = tk.StringVar(value="请先选择 BIN 文件并匹配模板...")
        tk.Label(preview_frame, textvariable=self.preview_var,
                 font=("Consolas", 11, "bold"), foreground="#2E8B57",
                 wraplength=800, anchor=tk.W, justify=tk.LEFT).pack(fill=tk.X)

        # ⑨ 校验和
        checksum_frame = ttk.LabelFrame(main_frame, text="⑨ 校验和（累加取低16位）", padding="8")
        checksum_frame.pack(fill=tk.X, pady=(0, 8))

        cs_row = ttk.Frame(checksum_frame)
        cs_row.pack(fill=tk.X)
        self.checksum_var = tk.StringVar(value="等待计算...")
        ttk.Label(cs_row, textvariable=self.checksum_var,
                  font=("Consolas", 12, "bold"), foreground="#8B0000").pack(side=tk.LEFT)
        ttk.Button(cs_row, text="重新计算", command=self._recalc_checksum, width=10).pack(side=tk.RIGHT)

        # 操作按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(4, 0))
        ttk.Button(btn_frame, text="重命名文件", command=self._rename_file, width=16).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(btn_frame, text="复制文件名", command=self._copy_filename, width=16).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(btn_frame, text="仅计算校验和", command=self._calc_only, width=16).pack(side=tk.LEFT)

        # 日志
        log_frame = ttk.Frame(main_frame)
        log_frame.pack(fill=tk.X, pady=(8, 0))
        self.log_var = tk.StringVar(value="就绪。")
        self.log_label = tk.Label(log_frame, textvariable=self.log_var,
                                  font=("Microsoft YaHei", 11, "bold"),
                                  foreground="#888", anchor=tk.W, justify=tk.LEFT)
        self.log_label.pack(fill=tk.X)

    # ----- 查找表加载 -----
    def _load_lookup_table(self):
        if not self.lookup_path.exists():
            messagebox.showwarning("找不到查找表", f"请将查找表放置在:\n{self.lookup_path}")
            self.entries = []
        else:
            self.entries = parse_lookup_table(str(self.lookup_path))
        template_names = [f"[{e['seq']}] {e['template'][:80]}..." for e in self.entries]
        self.template_combo['values'] = template_names
        if template_names:
            self.template_combo.current(0)

    # ----- 日志辅助 -----
    def _set_log(self, msg: str):
        self.log_var.set(msg)
        if msg.startswith("✓ PASS") or msg.startswith("✓"):
            self.log_label.config(foreground="#228B22")
        elif msg.startswith("✗ FAIL") or msg.startswith("✗"):
            self.log_label.config(foreground="#CC0000")
        elif msg.startswith("⊘"):
            self.log_label.config(foreground="#888")
        else:
            self.log_label.config(foreground="#555")

    # ----- 浏览文件 -----
    def _browse_file(self):
        filepath = filedialog.askopenfilename(
            title="选择 BIN 文件",
            filetypes=[("BIN 文件", "*.bin"), ("所有文件", "*.*")],
        )
        if not filepath:
            return
        self.bin_filepath = filepath
        self.file_path_var.set(filepath)
        self._update_file_info()
        self._auto_match()
        self._refresh_preview()

    # ----- 文件信息 -----
    def _update_file_info(self):
        if not self.bin_filepath:
            self.file_info_var.set("尚未选择文件")
            return
        path = Path(self.bin_filepath)
        if path.exists():
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)
            size_kb = stat.st_size / 1024
            size_str = f"{size_kb / 1024:.2f} MB" if size_kb > 1024 else f"{size_kb:.1f} KB"
            self.file_info_var.set(
                f"文件名: {path.name}  |  修改日期: {mtime.strftime('%Y-%m-%d %H:%M:%S')}  |  大小: {size_str}")
            self.root.title(f"BIN档命名工具 - {path.name}")
        else:
            self.file_info_var.set("文件不存在！")

    # ----- 自动匹配 -----
    def _auto_match(self):
        if not self.bin_filepath or not self.entries:
            self.match_info_var.set("")
            return
        matched = match_template(self.bin_filepath, self.entries)
        if matched:
            idx = self.entries.index(matched)
            self.template_combo.current(idx)
            self.selected_entry = matched
            self.match_info_var.set(f"✓ 自动匹配: 第{matched['seq']}条模板")
        else:
            self.match_info_var.set("⚠ 未能自动匹配，请手动选择")
            if self.entries:
                self.template_combo.current(0)
                self.selected_entry = self.entries[0]
        self._on_template_select()

    # ----- 模板选择 -----
    def _on_template_select(self, event=None):
        idx = self.template_combo.current()
        if 0 <= idx < len(self.entries):
            self.selected_entry = self.entries[idx]
            tpl = self.selected_entry['template']
            flag = self.selected_entry['flag']

            self.flag_var.set(f"日期标志: {flag} {'(需更新)' if flag == '1' else '(不变)'}")
            self.date_update_var.set(flag == '1')

            # 屏参名
            pn, _, _ = find_panel_name(tpl)
            self.original_panel_name = pn
            self.panel_name_var.set(pn)
            self.panel_info_var.set("已检测" if pn else "未检测到")

            # 软体版本
            fw, _, _ = find_fw_version(tpl)
            self.original_fw_version = fw
            self.fw_version_var.set(fw)
            self.fw_info_var.set("已检测" if fw else "未检测到")

            # 校验和前辍码
            prefixes, _, _ = find_checksum_prefixes(tpl)
            self.original_prefixes = prefixes.copy()
            self.prefix1_var.set(prefixes[0] if len(prefixes) > 0 else "")
            self.prefix2_var.set(prefixes[1] if len(prefixes) > 1 else "")
            self.pfx_info_var.set(f"已检测: {prefixes}" if prefixes else "无")

            # 其他信息
            self.other_info_var.set("")
            self.other_status_var.set("")

            self._refresh_preview()

    # ----- 各字段编辑回调 -----
    def _on_field_edit(self, event=None):
        self._refresh_preview()

    def _reset_panel_name(self):
        self.panel_name_var.set(self.original_panel_name)
        self.panel_info_var.set("已恢复默认")
        self._refresh_preview()

    def _reset_fw_version(self):
        self.fw_version_var.set(self.original_fw_version)
        self.fw_info_var.set("已恢复默认")
        self._refresh_preview()

    def _reset_prefixes(self):
        self.prefix1_var.set(self.original_prefixes[0] if len(self.original_prefixes) > 0 else "")
        self.prefix2_var.set(self.original_prefixes[1] if len(self.original_prefixes) > 1 else "")
        self.pfx_info_var.set(f"已恢复: {self.original_prefixes}" if self.original_prefixes else "已恢复: 无")
        self._refresh_preview()

    def _clear_other_info(self):
        self.other_info_var.set("")
        self.other_status_var.set("")
        self._refresh_preview()

    # ----- 刷新预览 -----
    def _refresh_preview(self):
        if not self.bin_filepath or not self.selected_entry:
            self.preview_var.set("请先选择 BIN 文件并匹配模板...")
            return

        template = self.selected_entry['template']
        effective_flag = '1' if self.date_update_var.get() else '0'
        new_panel = self.panel_name_var.get().strip()
        new_fw = self.fw_version_var.get().strip()
        p1 = self.prefix1_var.get().strip()
        p2 = self.prefix2_var.get().strip()
        new_prefixes = [x for x in [p1, p2] if x]
        other_info = self.other_info_var.get().strip()

        try:
            new_name, date_info, cs_info, panel_info, fw_info, prefix_info, other_str = generate_filename(
                template, effective_flag, self.bin_filepath, new_panel, new_fw, new_prefixes, other_info
            )
            self.generated_filename = new_name
            self.preview_var.set(new_name)
            self.date_detail_var.set(date_info)
            self.checksum_var.set(cs_info)

            # 更新各区域状态
            if new_panel != self.original_panel_name:
                self.panel_info_var.set("已修改")
            else:
                self.panel_info_var.set("已检测" if self.original_panel_name else "")

            if new_fw != self.original_fw_version:
                self.fw_info_var.set("已修改")
            else:
                self.fw_info_var.set("已检测" if self.original_fw_version else "")

            cur_pfx = [x for x in [p1, p2] if x]
            if cur_pfx != self.original_prefixes:
                self.pfx_info_var.set(f"已修改: {cur_pfx}" if cur_pfx else "已修改: 无")
            else:
                self.pfx_info_var.set(f"已检测: {cur_pfx}" if cur_pfx else "无")

            if other_info:
                self.other_status_var.set(f"将添加: \"{other_info}\"")
            else:
                self.other_status_var.set("")

            parts = [p for p in [panel_info, fw_info, prefix_info, other_str] if p]
            self._set_log(f"✓ 预览已生成 | {'; '.join(parts)}")
        except Exception as e:
            self.preview_var.set(f"生成失败: {e}")
            self._set_log(f"✗ 错误: {e}")

    # ----- 重新计算校验和 -----
    def _recalc_checksum(self):
        if not self.bin_filepath:
            messagebox.showinfo("提示", "请先选择 BIN 文件。")
            return
        try:
            with open(self.bin_filepath, 'rb') as f:
                data = f.read()
            cs = calc_checksum(data)
            self.checksum_var.set(f"累加和: {cs}  (文件大小: {len(data):,} bytes)")
            self._set_log(f"校验和已重新计算: {cs}")
            self._refresh_preview()
        except Exception as e:
            self.checksum_var.set(f"计算失败: {e}")

    def _calc_only(self):
        if not self.bin_filepath:
            self._set_log("✗ FAIL - 请先选择 BIN 文件")
            return
        self._recalc_checksum()

    # ----- 重命名 -----
    def _rename_file(self):
        if not self.bin_filepath or not self.generated_filename:
            self._set_log("✗ FAIL - 请先选择 BIN 文件并生成预览")
            return

        src = Path(self.bin_filepath)
        dst = src.parent / self.generated_filename

        if dst.exists() and not dst.samefile(src):
            if not messagebox.askyesno("文件已存在", f"目标文件已存在:\n{dst.name}\n\n是否覆盖？"):
                self._set_log("⊘ 取消 - 用户取消覆盖")
                return

        try:
            os.rename(src, dst)
            self.bin_filepath = str(dst)
            self.file_path_var.set(str(dst))
            self._update_file_info()
            self._set_log(f"✓ PASS - 已重命名为: {dst.name}")
        except Exception as e:
            self._set_log(f"✗ FAIL - 重命名失败: {e}")

    # ----- 复制 -----
    def _copy_filename(self):
        if not self.generated_filename:
            messagebox.showwarning("提示", "请先生成文件名预览。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.generated_filename)
        self.root.update()
        self._set_log(f"✓ 文件名已复制到剪贴板")


# ============================================================
# 主入口
# ============================================================
def main():
    root = tk.Tk()
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
    BinRenameApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
