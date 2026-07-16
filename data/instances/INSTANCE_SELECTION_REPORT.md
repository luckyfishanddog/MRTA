# 冻结焊缝实例候选选择报告

> 本报告只使用输入几何指标；未运行或引用任何 MRTA 求解器结果。

## 生成协议

- 运行命令：`python generate_instance_candidates.py --source-excel "D:\新建文件夹\小组立qcs\C_lines-change.xlsx" --target-weld-counts 30,45,60 --instance-seeds 41,42,43,44,45,46,47,48,49,50 --group-count-min 1 --group-count-max 60 --output-dir "data/instances"`
- 精确焊缝数量与合法性是硬门槛；未精确命中的候选不会参与评分。
- 几何总分：`0.45 × coverage_rank + 0.35 × balance_rank + 0.20 × boundary_interaction`。
- coverage_rank 为包围盒、x/y 覆盖、中点离散度、4×4 网格占用率五项归一化排名均值。
- balance_rank 为四象限长度 CV、上下不均衡、左右不均衡三项反向归一化排名均值。
- boundary_interaction 偏好约 10% 焊缝穿过参考边界，零交互得 0 分。

## 精确候选汇总

| target | seed | groups requested/placed | hash (12) | coverage | quadrant CV | crossings y/x | score |
|---:|---:|---:|---|---:|---:|---:|---:|
| 30 | 41 | 25/25 | `2fab0e3e5664` | 0.921 | 0.338 | 1/2 | 0.9125 |
| 30 | 45 | 19/19 | `49dff2f47fb5` | 0.883 | 0.477 | 2/1 | 0.6204 |
| 30 | 42 | 8/8 | `6a9fc20efd2f` | 0.778 | 0.162 | 0/2 | 0.5600 |
| 30 | 47 | 14/14 | `76fd0cb412af` | 0.747 | 0.514 | 2/0 | 0.3871 |
| 30 | 50 | 11/11 | `c158ca8b4940` | 0.783 | 0.863 | 5/1 | 0.1867 |
| 45 | 45 | 28/28 | `d90616eb356e` | 0.886 | 0.163 | 3/1 | 0.7134 |
| 45 | 41 | 36/36 | `3b50059c27eb` | 0.935 | 0.227 | 3/4 | 0.6032 |
| 45 | 47 | 25/25 | `e0bd040402c5` | 0.909 | 0.308 | 4/1 | 0.5751 |
| 45 | 48 | 17/17 | `60b8e0f4e692` | 0.906 | 0.404 | 2/3 | 0.5751 |
| 45 | 43 | 25/25 | `fcbf22496b8d` | 0.843 | 0.354 | 6/1 | 0.4339 |
| 45 | 50 | 22/22 | `51834bd015bd` | 0.865 | 0.586 | 5/2 | 0.2992 |
| 60 | 41 | 49/49 | `e7b7409b555e` | 0.949 | 0.389 | 3/6 | 0.7317 |
| 60 | 48 | 22/22 | `bc58ce74c83a` | 0.906 | 0.301 | 2/3 | 0.6408 |
| 60 | 43 | 36/36 | `b605f3994f60` | 0.843 | 0.364 | 6/2 | 0.5488 |
| 60 | 50 | 35/35 | `216eb97f6788` | 0.866 | 0.519 | 7/3 | 0.3050 |
| 60 | 46 | 25/25 | `a6fb2988f40f` | 0.866 | 0.422 | 2/11 | 0.2404 |

## 选定实例

### w30

- instance seed：`41`
- requested/placed groups：`25/25`
- actual weld count：`30`
- instance hash：`2fab0e3e566477294710c7f5296eae3f19fffdaf771928347daa371758bb2a62`
- total weld length：`47.762422 m`
- bbox/x/y/grid coverage：`0.921 / 0.966 / 0.954 / 0.812`
- quadrant CV：`0.338`
- crossings y=6 / x=10 / both：`1 / 2 / 0`
- geometry score：`0.9125`

### w45

- instance seed：`41`
- requested/placed groups：`36/36`
- actual weld count：`45`
- instance hash：`3b50059c27eb07cf6d40c8c36c0507257890c74fc956278aeedb8318f04e3c38`
- total weld length：`70.580090 m`
- bbox/x/y/grid coverage：`0.935 / 0.966 / 0.968 / 1.000`
- quadrant CV：`0.227`
- crossings y=6 / x=10 / both：`3 / 4 / 0`
- geometry score：`0.6032`

### w60

- instance seed：`41`
- requested/placed groups：`49/49`
- actual weld count：`60`
- instance hash：`e7b7409b555e581a0ceea770b7e2c511bacdced8f3172021729319bd352f76d2`
- total weld length：`101.237425 m`
- bbox/x/y/grid coverage：`0.949 / 0.980 / 0.968 / 1.000`
- quadrant CV：`0.389`
- crossings y=6 / x=10 / both：`3 / 6 / 0`
- geometry score：`0.7317`

## 公共 seed 结论

推荐公共 instance seed：`41`。它在 30/45/60 三个规模上均精确合法，并按三规模平均几何得分排名最高。
各规模独立最高分 seed 为：w30=41、w45=45、w60=41。
`selected/` 默认采用公共 seed 方案，以保持 30/45/60 生成逻辑一致；独立最优文件仍保留在 `candidates/` 中用于敏感性分析。
