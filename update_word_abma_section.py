"""Insert/update the ABMA method section in the project DOCX using stdlib OOXML."""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree as ET


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ET.register_namespace("w", W)


def q(name: str) -> str:
    return f"{{{W}}}{name}"


def text_of(paragraph: ET.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(q("t"))).strip()


def paragraph(text: str, template: ET.Element | None = None) -> ET.Element:
    result = ET.Element(q("p"))
    if template is not None:
        ppr = template.find(q("pPr"))
        if ppr is not None:
            result.append(copy.deepcopy(ppr))
    run = ET.SubElement(result, q("r"))
    node = ET.SubElement(run, q("t"))
    node.text = text
    return result


def write_docx(path: Path, members: dict[str, bytes], root: ET.Element) -> None:
    members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    fd, temporary = tempfile.mkstemp(suffix=".docx", dir=str(path.parent))
    os.close(fd)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as target:
            for name, data in members.items():
                target.writestr(name, data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def update(path: Path) -> None:
    with ZipFile(path, "r") as source:
        members = {item.filename: source.read(item.filename) for item in source.infolist()}
    root = ET.fromstring(members["word/document.xml"])
    body = root.find(q("body"))
    if body is None:
        raise RuntimeError("DOCX document body is missing")
    children = list(body)
    authoritative_heading = "3.2 自适应双层模因算法（ABMA）"
    authoritative_existing = next(
        (node for node in children if text_of(node) == authoritative_heading), None
    )
    if authoritative_existing is not None:
        start = children.index(authoritative_existing)
        end = next(
            (index for index in range(start + 1, len(children))
             if text_of(children[index]) == "计算结果与比较"),
            len(children) - 1,
        )
        heading_template = authoritative_existing
        body_template = children[start + 1] if start + 1 < end else None
        for node in children[start:end]:
            body.remove(node)
        method_content = [
            (authoritative_heading, heading_template),
            ("ABMA以SHADE型差分进化搜索上、下半区的连续分界变量x_up与x_low。给定边界后，严格按照完整区域包含与边界切分规则生成四个机器人任务集；内层以四机器人系统统一加权目标作为接受准则，使用ALNS与VND优化开放路线，并以精确双状态动态规划确定每条固定顺序路线的焊接方向。", body_template),
            ("增量修复以父焊缝标识和规范化几何端点作为任务身份。几何未变且仍属于同一机器人的任务保持相对顺序，新增、迁移、拆分或合并任务采用确定性regret-2插入。任务几何序列完全一致时继承父代的精确路线评价缓存；部分几何一致时仅对能够一一映射的完整待评价路线复用精确缓存。该复用不改变候选、接受判定或随机数流。", body_template),
            ("多保真内层采用可恢复的low、mid、high三级状态。当前开发候选的ALNS/VND预算分别为10/0、30/1和60/3；晋级时连续保留当前解、历史最优、算子权重、温度、累计迭代、路线缓存和由求解器种子与分区键确定的独立内层随机状态。low和mid仅用于筛选，只有完成high认证的候选才能进入种群、外部档案、全局最优和最终输出。", body_template),
            ("晋级策略使用可审计的单侧保守误差界。每个同时获得low、mid、high结果的候选记录三档目标值、low到high与mid到high的改进潜力、受影响任务比例、保留任务比例、边界变化量和代数；Q_low与Q_mid取有效审计样本改进潜力的95%最近秩分位数，至少积累50个有效样本后才允许拒绝。只有low-Q_low或mid-Q_mid严格劣于目标high值时才能筛除；暖机期、代内暂优、近目标、小边界高保留、大边界不确定、高受影响比例和低种群边界多样性候选强制晋级，并每20个决策执行确定性反事实审计。", body_template),
            ("开发基准与正式科学实验严格隔离。开发种子42和43的增量多保真候选加速比分别为1.113和1.370，seed 43目标退化为2.320%，未满足中位加速至少2倍且逐种子退化不超过2%的门槛。因此该配置未冻结，验证种子45—47、三算法预检和16000次评价Pilot均未运行，不能把本节开发配置表述为正式最终候选。", body_template),
        ]
        for offset, (value, template) in enumerate(method_content):
            body.insert(start + offset, paragraph(value, template))

        children = list(body)
        experiment_heading = next(
            (node for node in children if text_of(node) == "4.1实验方案与参考方法"), None
        )
        if experiment_heading is not None:
            position = children.index(experiment_heading) + 1
            experiment_text = (
                "正式实验算法集合仅包括三种：传统基线GA+ACO、主控算法Paper-Aligned-HGA-XCut-Control和主算法ABMA。"
                "三者共享冻结实例、理论理想点—公共确定性基线范围归一化、完整四机器人系统外层评价预算和求解器种子。"
                "DE+LKH仅作为历史探索保留源文件与历史数据，明确排除于最终论文实验、统一汇总、排序和统计检验。"
            )
            body.insert(position, paragraph(experiment_text, body_template))
        write_docx(path, members, root)
        return
    existing = next((node for node in children if text_of(node) == "3.2 自适应双层模因算法（ABMA）"), None)
    if existing is not None:
        return
    insert_at = next(
        (index for index, node in enumerate(children) if text_of(node) == "计算结果与比较"),
        len(children) - 1,
    )
    heading_template = next((node for node in children if text_of(node) == "3.1焊接区域划分"), None)
    body_template = next((node for node in children if text_of(node).startswith("式(21)表明")), None)
    content = [
        ("3.2 自适应双层模因算法（ABMA）", heading_template),
        ("本文采用自适应双层模因算法联合优化上下半区的左右分界位置与四台机器人的焊缝执行序列。外层使用 SHADE 型差分进化搜索连续变量 x_up 与 x_low；给定边界后，严格按照第2节的完整区域包含与边界切分规则生成四个任务集合。内层以四机器人系统加权目标作为接受准则，使用 ALNS 与 VND 优化开放路线，并对每条固定顺序路线采用精确双状态动态规划确定焊接方向。", body_template),
        ("为降低相邻边界候选的重复计算，优化实现使用边界驱动的增量路线继承。子焊缝以父焊缝标识与规范化几何端点共同匹配，不依赖可能随切分改变的子段编号。仅边界发生变化的半区进入修复；几何未变且仍归属同一机器人的任务保持原相对顺序，新增、迁移、拆分或合并产生的任务采用确定性 regret-2 插入。未受影响半区直接复用已认证路线统计。", body_template),
        ("内层搜索采用可恢复的 low、mid、high 三档预算，分别执行15、50、100次 ALNS 和0、1、3轮 VND。low 只搜索边界受影响机器人，mid 扩展至受影响机器人及当前最大、最小负载机器人，high 搜索全部机器人。搜索状态在晋级时连续保留当前解、历史最优、算子权重、温度、累计迭代、路线缓存和独立内层随机数状态，因此晋级不是重新随机启动。", body_template),
        ("低保真或中保真结果只用于筛选，只有完成 high 认证的候选才允许进入种群、外部档案、全局最优和最终输出。外层差分进化随机流与内层路线搜索随机流相互独立，分区缓存同时记录已达到的最高保真度及可恢复状态。实现保留 legacy、仅增量、仅多保真和增量加多保真四个隔离配置；性能候选标识为 ABMA-Incremental-MultiFidelity-v1，但对外 solver_name 保持为 ABMA。", body_template),
        ("上述性能机制不改变运动学时间模型、焊缝切分与归属、开放路径假设、方向动态规划、三项目标、统一归一化或外层完整系统候选评价计数。开发阶段性能基准与正式科学实验相互隔离，只有在运行时间、目标退化和误拒审计同时满足预设门槛后，才可考虑进入后续正式协议。", body_template),
    ]
    for offset, (value, template) in enumerate(content):
        body.insert(insert_at + offset, paragraph(value, template))
    members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    fd, temporary = tempfile.mkstemp(suffix=".docx", dir=str(path.parent))
    os.close(fd)
    try:
        with ZipFile(temporary, "w", ZIP_DEFLATED) as target:
            for name, data in members.items():
                target.writestr(name, data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


if __name__ == "__main__":
    candidates = sorted(Path(".").glob("*.docx"), key=lambda item: item.stat().st_mtime, reverse=True)
    target = next(item for item in candidates if item.name != "新建 DOCX 文档.docx")
    update(target)
    print(target.resolve())
