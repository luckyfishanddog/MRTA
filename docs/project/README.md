# MRTA_GA 项目说明

本项目研究双滑轨四悬臂机器人焊接任务的区域分配、焊缝排序、路径优化与实验验证。

## 目录

- `src/`：全部可运行 Python 代码，包括算法、模型、实验入口、分析和维护程序。
- `tests/`：全部自动化测试；`tests/fixtures/` 存放测试工作簿。
- `docs/`：全部项目级 Markdown 文档，以及论文 Word 文件和草稿。
- `config/`：协议、画像、归一化参数、预算和哈希清单。
- `results/`：根目录原有的汇总 CSV、JSON 和分析结果。
- `data/`：实例、预处理数据与冻结输入。
- `experiments/`：独立实验批次及其原始证据。
- `formal_atomic/`：原子模型正式实验和完整性证据。
- `unified_experiments/`：统一实验运行记录。
- `diagnostics/`：专项诊断程序及输出。
- `archive/`：历史备份。
- `references/`：外部参考论文。

完整的逐文件用途见 [FILE_CLASSIFICATION.md](FILE_CLASSIFICATION.md)。

## 常用命令

```powershell
python -m pytest -q
python src/run_unified_experiments.py --help
python src/MRTA_ABMA.py --help
```

旧协议中仍保存整理前的文件名，用于历史追踪。`src/project_paths.py` 提供兼容解析，使旧记录可定位到当前目录。
