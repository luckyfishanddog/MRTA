#!/usr/bin/env python3
"""Apply the protocol-2.5 wording update without launching Microsoft Word."""

from __future__ import annotations

import copy
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from project_paths import resolve_project_path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = resolve_project_path("双滑轨四悬臂机器人任务分配及焊缝排序.docx")
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W}
ET.register_namespace("w", W)


ABMA_PARAGRAPHS = [
    "本文正式采用冻结算法ABMA-Legacy-Exact-Fast-v1。其外层保持原始SHADE搜索语义，联合搜索上、下半区的连续分界变量x_up与x_low；给定边界后，严格按照完整区域包含与边界切分规则生成四台机器人的任务集合。内层对每个完整四机器人候选执行80次ALNS与3次VND，并始终以四机器人系统统一加权目标作为接受准则；对每条固定顺序路线，使用精确双状态动态规划确定焊接方向。",
    "legacy_exact_fast仅实施结构性等价加速，包括不可变量预计算、精确路线签名缓存、精确方向动态规划缓存、四机器人系统指标的等价增量聚合和热点路径序列化削减。上述实现不改变随机数流及抽样顺序、候选生成、接受规则、ALNS/VND预算、完整系统目标评价预算和精确方向解码，因此算法搜索语义与legacy参考实现保持一致。冻结画像为abma_final_profile.json，SHA-256为631b2d675e7531d04a3c019cc8013158223944074da3e42d489a5745addee1d3。",
    "在开发种子42—44和独立验证种子45—47上，正式候选均须与legacy参考实现逐种子核对最终边界、目标值、机器人序列、方向、候选与接受哈希、随机状态哈希以及去除计时字段后的检查点轨迹；只有精确等价、完整消耗统一评价预算且满足预设加速门槛时，才允许进入三算法预算预检。该验证只用于确认实现等价性和运行可行性，不用于调参，也不产生算法优劣排名。",
    "增量修复与low/mid/high多保真筛选曾作为开发探索方案进行测试，但未满足冻结门禁，现仅作为被拒绝的历史变体保留以支持复现，不属于正式算法定义、论文贡献或正式实验配置。",
]

OFFICIAL_PARAGRAPH = (
    "正式实验算法集合仅包括三种：传统基线GA+ACO、主控算法Paper-Aligned-HGA-XCut-Control和冻结算法"
    "ABMA-Legacy-Exact-Fast-v1（legacy_exact_fast，ALNS/VND=80/3，精确方向动态规划）。三者共享冻结实例、"
    "理论理想点—公共确定性基线范围归一化、完整四机器人系统外层评价预算和求解器种子。DE+LKH仅作为历史探索"
    "保留源文件与历史数据，明确排除于最终论文实验、统一汇总、排序和统计检验。"
)


def paragraph_text(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS)).strip()


def set_paragraph_text(paragraph: ET.Element, text: str) -> None:
    properties = paragraph.find("w:pPr", NS)
    for child in list(paragraph):
        if child is not properties:
            paragraph.remove(child)
    run = ET.SubElement(paragraph, f"{{{W}}}r")
    node = ET.SubElement(run, f"{{{W}}}t")
    node.set(f"{{{XML}}}space", "preserve")
    node.text = text


def main() -> None:
    with zipfile.ZipFile(DOCUMENT, "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}
    root = ET.fromstring(entries["word/document.xml"])
    body = root.find("w:body", NS)
    if body is None:
        raise RuntimeError("DOCX document body is missing")
    paragraphs = [node for node in list(body) if node.tag == f"{{{W}}}p"]
    texts = [paragraph_text(node) for node in paragraphs]
    start = next(index for index, text in enumerate(texts) if text.startswith("3.2 自适应双层模因算法（ABMA）"))
    end = next(index for index in range(start + 1, len(texts)) if texts[index] == "计算结果与比较")
    template = paragraphs[start + 1]
    insertion_position = list(body).index(paragraphs[start]) + 1
    for paragraph in paragraphs[start + 1:end]:
        body.remove(paragraph)
    for offset, text in enumerate(ABMA_PARAGRAPHS):
        paragraph = copy.deepcopy(template)
        set_paragraph_text(paragraph, text)
        body.insert(insertion_position + offset, paragraph)

    official = [node for node in list(body) if node.tag == f"{{{W}}}p" and paragraph_text(node).startswith("正式实验算法集合仅包括三种：")]
    if not official:
        raise RuntimeError("Formal algorithm paragraph is missing")
    set_paragraph_text(official[0], OFFICIAL_PARAGRAPH)
    for duplicate in official[1:]:
        body.remove(duplicate)

    entries["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix="paper_protocol25_", suffix=".docx", dir=DOCUMENT.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name, payload in entries.items():
                target.writestr(name, payload)
        os.replace(temporary, DOCUMENT)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
