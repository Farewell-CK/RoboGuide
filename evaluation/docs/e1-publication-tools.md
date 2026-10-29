# E1 阶段结果的离线导出与重算

本工具落实 [结果发布规范 v0.1](e1-results-publication-v0.1.md)，不运行任一被测系统。
只读输入为已封存 JSON/JSONL 原档，输出为新修订目录。它不导入 EMOS、Habitat 或
runner，不访问 Provider、提交 Mission 或修改 admission/Pair Validator 的规则。

## 已支持的范围

- `e1_staged_import` 消费 `roboguide.e1.staged-cohort/v0.1`：检查完整原 selector、
  RUN_MANIFEST、最终 STATUS、CASES、原生复用选择、逐臂结果、配对 Manifest 和记账。
- `publication_io` 只允许显式绑定的原档命名空间；拒绝路径逃逸、意外符号链接、
  来源漂移和超限文件。输入单文件至多 64 MiB，输出单文件至多 1 MiB，JSONL 分片
  不丢记录。缺失材料有独立 unavailable 条目，不生成官方 false。
- `e1_publication` 将覆盖、primary/retry/diagnostic、原始调用/usage/耗时、原记录配对
  和官方结果分别重算；包内 SHA256SUMS、自引用、来源引用、去重和统计均独立检查。
- `e1_census` 使用已有历史 locator 查找并重新读取原始结果，清点四类历史覆盖，
  不把 locator 的评估修订覆盖原始值，不合并历史版本的成功率。

这是有明确源格式的第一版导入器，不是任意实验目录的通用猜测器。其他源格式先记录
清点状态和缺口；没有原档或最终关闭记录时，不推测“没跑过”或“已完成”。
更晚的失败选择队列、固定诊断及其他验收不混入本次主实验迁移。

## 可移植离线验证

在仓库 uv 环境中执行，不需要模型凭据、原生依赖、GPU 或原始运行目录：

```bash
uv run python -m roboguide_eval.e1_publication validate \
  evaluation/reports/e1/cohorts/roboguide-e1-staged-batch-20261027T015410Z-1ixj2e2l/revisions/r0001

uv run python -m roboguide_eval.e1_publication validate-census \
  evaluation/reports/e1/inventories/20261030-r0001
```

JSONL 分片文件由每个 `manifest.json` 的 `record_files` 指定；不要假设记录只在
固定的 `runs.jsonl` 中。`evaluation/schemas/e1-result-publication-v0.1.schema.json`
给出 run/artifact 的机器可读形状；跨文件身份、关系、完整清单和统计由校验器负责。
测试会离线检查仓库中已提交的发布包。

校验成功表示发布投影字节一致、引用闭合且汇总可由逐条记录复算。它不表示已重新
运行原始 Verifier，不证明目录外视频/数据库齐全，也不证明独立远端备份存在。
本次原档索引覆盖导出时实际消费的元数据和记账，不宣称逐字节验证了所有视频、
数据库、轨迹、源码和数据集。`local_only` 必须保留。

## 在持有原档的机器重新导出

仅本机绑定文件包含绝对路径，不加入版本控制。示意配置如下，实际 ID 与路径须
来自原档，不从成绩或当前代码生成：

```json
{
  "archives": {
    "original-staged-archive": "/path/to/frozen-batch",
    "original-native-archive": "/path/to/original-native-runs"
  }
}
```

清点另需 `census.dataset_census`、`census.historical_locator` 与
`census.staged_source_id`。这些是本机读取定位，不是公开路径。
批次保存的其他原生引用必须全部有显式命名空间绑定；缺绑定时拒绝而不扫描任意目录。

```bash
uv run python -m roboguide_eval.e1_publication export-staged \
  --bindings /path/to/local-bindings.json \
  --source-id original-staged-archive \
  --exporter-sha FULL_COMMITTED_EXPORTER_SHA \
  --output /path/to/new-stage/r0001

uv run python -m roboguide_eval.e1_publication census \
  --bindings /path/to/local-bindings.json \
  --exporter-sha FULL_COMMITTED_EXPORTER_SHA \
  --output /path/to/new-stage/inventory
```

输出必须位于所有输入原档之外，目录不得预先存在。相同输入和同一导出器应产生
相同字节，不将重新导出时间混入冻结发布身份。运行 SHA 来自原 launch；导出器 SHA
另记，并在实际发布时核对其已提交源码。不能随意填写当前 main。

导出器前后重新核对所有消费来源，保存仅本机可用的 `*-local-source-witness.json`；
该文件包含绝对路径，不复制进公开修订。发布索引只在完整包验证后更新，原档不写入。
统计错误新增修订，真实新运行新增 cohort；原始失败、旧版本和重试均不覆盖。

## 首份结果的使用边界

本批 primary 保留原 RG 运行及原生历史复用；13 次重试和工程 smoke 另列。
旧初态比较是 scalar-only，配对通过不证明严格“只改变组织层”。本批存在已披露的
公开 HSSD 目标身份缺陷、模型与论文差异和 Local How 差异。因此支持有限集合的
完整系统描述与缺陷分析，不支持论文原表精确复现、组织层单一因果或新版性能结论。
