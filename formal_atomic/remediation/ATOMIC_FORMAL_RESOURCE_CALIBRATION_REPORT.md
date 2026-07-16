# EPRK-MA / HGA-Atomic 资源校准报告

- 选定 equal-primary 预算：`20000`
- 是否因任一 20000-budget 运行超过 300 秒而全局回退：`False`
- 选定预算的校准运行数：`54`
- 校准仅决定资源限制，不修改算法参数。
- 真实实例使用自身校准；合成实例使用同 family 的 n60/replicate1 校准时间。

| reference | median max wall (s) | raw T (s) | frozen T (s) |
|---|---:|---:|---:|
| real_w30 | 0.852366 | 17.047314 | 30 |
| real_w45 | 1.202508 | 24.050160 | 30 |
| real_w60 | 1.948781 | 38.975630 | 40 |
| uniform_n60_r1 | 1.750867 | 35.017342 | 40 |
| clustered_n60_r1 | 1.592599 | 31.851972 | 40 |
| boundary_dense_n60_r1 | 1.621742 | 32.434838 | 40 |
| long_weld_rich_n60_r1 | 5.057000 | 101.139990 | 110 |
| zero_travel_chain_n60_r1 | 1.486095 | 29.721904 | 30 |
| load_skewed_n60_r1 | 1.488550 | 29.771000 | 30 |
