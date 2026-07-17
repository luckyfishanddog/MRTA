# 固定预切分任务表示模型规范

## 1. 身份与用途

- 算法版本：`ABMA-Fixed-PreSplit-Development-v1`
- 科学模型版本：`fixed_presplit_no_cross_region_zero_connected_travel_v1`
- 变体：`fixed_presplit_reference`、`fixed_presplit_exact_fast`
- 状态：仅开发验证，不属于统一正式实验协议，不授权正式 270 次实验。

本模型以 Lee、Kim、Nam 的船厂龙门焊接机器人调度方法论文为长焊缝预切分依据，尤其采用其 `lmax=5000 mm`、`lmin=1000 mm` 和 `6600 mm → floor(6600/1000)=6` 个等长 `1100 mm` 子段的规则。项目任务明确覆盖论文中的 setup/post 合并假设：本版本 setup 与 post-processing 均固定为 0。

方法来源文件：

- `Novel method for welding gantry robot scheduling at shipyards.pdf`，SHA-256 `aa4da202fa77f72945fe71ba14abc28b9f72e29bae537a4c023d6ebb7db73109`
- `1-s2.0-S0957417425009212-main.pdf`，SHA-256 `594095514a3ee1dd737db5cab1e6c4737b017c83104a82ef386fa59a8ace5b8a`（仅用于未来 HGA adapter 设计，本轮未修改或运行 HGA）

## 2. 几何预处理

预处理在优化器启动前一次性完成，优化过程中绝不改变固定焊缝几何。

1. 先按 `y=6 m` 精确切分跨上下半区的原始焊缝。
2. 对每个 y 子段计算水平跨度 `s_x=|x_2-x_1|`。
3. 仅当 `s_x > lmax=5 m` 时长焊缝切分；`s_x=5 m` 不切分。
4. 子段数 `k=floor(s_x/lmin)`，其中 `lmin=1 m`。
5. 沿原线段参数等分为 k 段，不产生短尾段；每段欧氏长度、焊接时间按同一参数比例守恒。
6. 每个固定焊缝记录原父焊缝、稳定 ID、端点、欧氏长度、水平跨度、焊接时间、y/长焊缝切分原因。

稳定 ID 由父焊缝身份、所属半区与规范化端点生成，与 seed、x 边界、机器人编号、输入端点方向无关。

## 3. 离散合法边界

上下半区分别构建边界集合 `B_up`、`B_low`，来源为平台端点、固定焊缝 x 端点和相邻端点安全区间中点。以下候选被排除：平台外、重复值、严格穿过任一固定焊缝内部、导致任一侧为空。若没有合法内部边界，预处理显式失败。

SHADE 仍搜索连续变量 `u_up,u_low∈[0,1]`，解码为：

```text
index = round(u * (len(B) - 1))
x = B[index]
```

partition cache 以离散 `(index_up,index_low)` 为键。固定焊缝整体分配，禁止任何 x 边界再次切分几何。竖直焊缝或点焊恰位于边界时确定性分到左侧。

## 4. 路线与零空走

路线为开放路线，方向由精确两状态动态规划解码。任意两个相邻固定焊缝，只要前一有向终点与后一有向起点的三维欧氏距离不超过 `GEOMETRY_EPS=1e-9 m`，则该转移：

- 空走时间严格为 0；
- 空走距离严格为 0；
- 不执行抬升、平移、下降；
- 不要求二者属于同一父焊缝。

其他转移继续使用冻结的三阶段运动模型。

## 5. reference 与 exact-fast 等价边界

两变体完全共享冻结 SHADE、80 次 ALNS、3 次 VND、接受规则、随机数流和精确方向 DP。`exact-fast` 只增加不改变数值的实现层快路：

- 实例级有向 pair 精确缓存；
- evaluator 局部整数转移表；
- 稳定 ID 排名的无碰撞完整 bytes 路线键；
- 最多 1,000,000 条压缩完整路线 stats 精确 LRU；
- 最多 4,000,000 条正反开放路线共享的精确 travel scalar LRU；
- 需要完整方向 flags 时仍执行精确 DP，不能用反向路线的 flags 代替。

缓存淘汰只导致精确重算，不改变返回值。budget=100 的三规模逐字段检查已验证 fitness、边界、路线、方向、完整 robot stats、候选/接受序列和 RNG 哈希一致。

## 6. 独立归一化

归一化文件为 `fixed_presplit_normalization.json`。理论理想点采用总纯焊接时间四等分、零负载差、零空走；确定性 baseline 在合法离散边界上按半区工作量平衡选边界，再做 cheapest insertion、2-opt 和精确方向 DP。归一化与旧动态切分协议互不混用。

## 7. 禁止事项

- 不修改冻结 ABMA/HGA/core/旧 normalization/旧协议/正式 manifest。
- 不运行 DE+LKH、HGA、正式 270 次、seed 42–71 或保留 seed 75–77。
- 不因为性能门禁未通过而降低 80 ALNS/3 VND。

