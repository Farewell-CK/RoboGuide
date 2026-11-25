# robonix-src 本地修复补丁

`robonix-src/` 是上游 RoboNix SDK 的本地检出（512 MB，含 `target/` 与各包 `rbnx-build/venv`），
按本仓库约定**不入库**。但这两处修复是让 RoboGuide 能驱动本场景的必要条件，
因此以补丁形式随场景入库。

## 怎么打

```bash
# 在仓库根目录执行（路径相对 robonix-src/）
patch -p0 -d . < scenarios/mujoco-push-v0.1/patches/robonix_facade.patch
patch -p0 -d . < scenarios/mujoco-push-v0.1/patches/driver.patch
```

补丁头里的路径就是 `robonix-src/examples/...`，所以 `-p0` + 仓库根目录即可。

## 校验和

| 文件 | sha256 |
|---|---|
| `robonix_facade.patch` | `42c0b8bb183778940113757c798f3ed7f3a02acbbe8750bcaa5ba4256ff7ce2c` |
| `driver.patch` | `78407445ae13d30dfa10f871ac13268d3f0efb7462e90af2ad54b8a56aa17b70` |

## robonix_facade.patch（facade，语义目的地/物体名解析）

| 修复 | 现象（改之前） |
|---|---|
| 坐标容忍括号与全角逗号 | `"(0.60, 1.40)"` 被当地名去问 scene → 报"没有这个注解" |
| 地名候选归一 | 模型写 `target point E` / `目标点 A`（插空格、翻英文）→ 无法解析 |
| 物体别名归一（读场景资产 `object-aliases.json`） | 用户说"箱子/纸盒"直接透传给驱动 → 驱动报"不在部署地图里" |
| 描述式匹配（形状/颜色/可搬，唯一命中才用） | "那个橙色的方块"无法落到 `obs2` |
| 认不出时返回候选清单 | 只报"不在部署地图里"，上游无法自纠 |
| `_scene_entity` 未知名字判空修正 | 返回空 dict 被当成"存在"，导致"认不出"分支永远不生效 |

## driver.patch（mujoco_arm 的 arm_driver）

| 修复 | 现象（改之前） |
|---|---|
| `half` 兜底改三维 + 捕获 `IndexError` | 任一"无机器人几何"的实体会让地图先验构建抛 `IndexError` → MCP 层报 `list index out of range` |
| 松手落点记账 `_remember_drop`（只读自身末端位姿） | 失败后物体落在别处却不记录 → 下一轮按初始位去抓 → "抓前确认"失败 |
| 失败文案明确"物体不在原位"并给出落点坐标 | 原文"已把物体放回当下位置"有歧义 |
| 持物回转时忽略夹爪包络内的命中 | 手里那件物体被当成"未识别障碍" → 每个回转方向都判"被挡" |

## 已知遗留

- 放置后的"回波独立确认"偶尔返回 3.0 m（无回波），回执会写"未获独立确认"；动作本身成功。
- `robonix-src` 中长期方案是抽成 `integrations/mujoco-facade/` 正式包，届时可删除本补丁。
