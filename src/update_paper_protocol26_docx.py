#!/usr/bin/env python3
"""Update the paper's experiment-design section using OpenXML only."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
DOCX = resolve_project_path("双滑轨四悬臂机器人任务分配及焊缝排序.docx")
BACKUP = ROOT / "archive" / "stage_reports" / "双滑轨四悬臂机器人任务分配及焊缝排序_before_protocol_2.6.docx"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
ET.register_namespace("w", W)


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()


def set_paragraph_text(paragraph: ET.Element, text: str) -> None:
    properties = paragraph.find(f"{{{W}}}pPr")
    for child in list(paragraph):
        if child is not properties:
            paragraph.remove(child)
    run = ET.SubElement(paragraph, f"{{{W}}}r")
    node = ET.SubElement(run, f"{{{W}}}t")
    node.text = text


def new_paragraph_like(source: ET.Element, text: str) -> ET.Element:
    paragraph = ET.Element(f"{{{W}}}p")
    properties = source.find(f"{{{W}}}pPr")
    if properties is not None:
        paragraph.append(deepcopy(properties))
    set_paragraph_text(paragraph, text)
    return paragraph


def main() -> None:
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if not BACKUP.exists():
        shutil.copy2(DOCX, BACKUP)
    with zipfile.ZipFile(DOCX, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    root = ET.fromstring(members["word/document.xml"])
    body = root.find(".//w:body", NS)
    if body is None:
        raise RuntimeError("Word document has no body")
    target = next((p for p in body.findall("w:p", NS)
                   if paragraph_text(p).startswith("正式实验算法集合仅包括三种")), None)
    if target is None:
        raise RuntimeError("cannot find the protocol 2.5 experiment-design paragraph")
    paragraphs = [
        "正式实验算法集合仅包括三种：传统基线GA+ACO、主控算法Paper-Aligned-HGA-XCut-Control和冻结算法ABMA-Legacy-Exact-Fast-v1。ABMA仅采用legacy_exact_fast配置，ALNS/VND固定为80/3并使用精确方向动态规划；low/mid/high多保真方案不属于正式算法。三者共享冻结实例、理论理想点—公共确定性基线范围归一化、完整四机器人系统目标评价预算和成对求解器种子。DE+LKH明确排除于正式实验、统一汇总、排序和统计检验。",
        "正式实验设计覆盖w30、w45和w60三个冻结实例，每个实例对三种算法使用相同的30个成对种子42—71，共270个原始优化运行。正式运行前先冻结协议SHA-256、实例与归一化哈希、ABMA画像哈希、命令哈希、输出目录、运行顺序和恢复键；当前仅生成清单与干运行命令，尚未获得用户正式执行批准。",
        "统一评价预算保留两个待用户选择的方案：方案A在全部实例上采用11000次完整系统目标评价；方案B在w30和w45采用16000次、在w60采用11000次，同一实例内三算法预算严格一致。全局和逐实例超时方案按ceil_to_300(max(21600,1.5×实测最大wall time+300))确定，本次校准两者数值均为21600 s。正式算法关闭早停，必须以objective_budget结束并精确消耗所选预算。",
        "碰撞检查在求解器输出冻结后独立执行，不计入algorithm_time或solver_wall_clock_time，不改变原始适应度、完工时间、边界、路线和方向。论文分别报告原始指标与碰撞调整指标；后者包括collision_adjusted_makespan、added_waiting_time、conflict_count和unresolved_conflict_count。审计失败或仍有未解决冲突时必须显式披露，不得宣称碰撞处理成功。",
        "每个实例分别报告适应度、原始完工时间、负载不均衡、总空走距离、碰撞调整指标和运行时间的均值、样本标准差、中位数、最小值、最大值、四分位距与成功率。对三算法成对种子的原始适应度执行Friedman总体检验，并对三组算法对执行双侧配对Wilcoxon符号秩检验和Holm校正；同时报告秩二列相关效应量、胜/平/负计数，以及固定种子20260715、10000次重采样的配对中位差百分位bootstrap 95%置信区间。失败运行不替换种子，推断检验仅使用完整成功配对并披露配对数。",
        "恢复机制只跳过协议、科学源文件和命令哈希均匹配的成功运行；timeout、failed和invalid attempt均保留且不得纳入成功统计。预算预检、规模校准和碰撞审计均属于Pilot，只验证执行可行性与后处理策略，不用于算法调参、正式排名或ABMA优越性结论。",
        "敏感性分析在独立输出目录中设计并执行，分别考察目标权重、负载尺度下限、公共基线构造和碰撞分离策略，不修改主实验冻结归一化或污染正式实验数据。",
    ]
    inserted_prefixes = tuple(text[:18] for text in paragraphs[1:])
    for paragraph in list(body.findall("w:p", NS)):
        if paragraph is not target and paragraph_text(paragraph).startswith(inserted_prefixes):
            body.remove(paragraph)
    set_paragraph_text(target, paragraphs[0])
    insertion_index = list(body).index(target) + 1
    for text in paragraphs[1:]:
        body.insert(insertion_index, new_paragraph_like(target, text))
        insertion_index += 1
    members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    fd, temporary = tempfile.mkstemp(prefix="protocol26_", suffix=".docx", dir=str(DOCX.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target_zip:
            for name, data in members.items():
                target_zip.writestr(name, data)
        with zipfile.ZipFile(temporary, "r") as check:
            if check.testzip() is not None:
                raise RuntimeError("updated DOCX failed ZIP integrity")
            ET.fromstring(check.read("word/document.xml"))
        os.replace(temporary, DOCX)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    main()
