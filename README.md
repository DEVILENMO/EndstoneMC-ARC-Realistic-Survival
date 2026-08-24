# ARC Realistic Survival - 真实生存插件
[![Codacy Grade](https://app.codacy.com/project/badge/Grade/035827370d734c539602adbeca85f6d4)](https://app.codacy.com/gh/DEVILENMO/EndstoneMC-ARC-Realistic-Survival/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)


一个为 Endstone 服务器打造的真实生存插件，添加口渴值系统、营养学系统、物品效果等功能，让生存体验更加真实有趣。

## ✨ 功能特性

### 🚰 口渴值系统
- **动态口渴值**：玩家拥有 0-100 的口渴值，需要定期补充水分
- **自动衰减**：口渴值会随时间自动降低
- **移动加速消耗**：玩家移动时口渴值消耗速度会增加
- **数据持久化**：玩家口渴值会自动保存到数据库
- **实时提示**：通过弹窗显示当前口渴值

### 🍎 营养学系统
- **四种营养素**：维生素 A、维生素 C、铁、蛋白质，各自独立 0-100 数值
- **缺素病症**：长期偏食触发夜盲症、坏血病、贫血、肌无力
- **症状分级**：健康 / 轻症 / 中症 / 重症，仅在等级变化时 Toast 提示
- **食物绑定**：每种食物可配置四种营养素加成（SQLite `nutrition_items` 表）
- **原生 API**：通过 Endstone `Effect` 与 `AttributeModifier` 实现减益，不污染实体 NBT

### 🍺 物品效果系统
- **自定义物品效果**：通过配置文件自定义任意物品的效果
- **口渴值变化**：消耗物品可以增加或减少口渴值
- **药水效果**：支持给予玩家药水效果（速度、力量等）
- **效果持续时间**：可配置效果持续时间

### ⚙️ 配置管理
- **游戏内配置面板**：使用 `/ars` 命令打开可视化配置界面
- **热重载**：使用 `/ars reload` 命令无需重启服务器即可重载配置
- **灵活配置**：支持配置衰减速度、移动倍率、初始值等参数

## 📋 系统要求

- **Python 版本**：推荐 Python 3.13+
- **Endstone API**：0.10+
- **依赖插件**：
  - （可选）`arc_core` 或 `umoney` - 用于经济系统集成

## 📦 安装方法

### 方法一：使用预编译的 wheel 文件

1. 下载最新的 `.whl` 文件
2. 将文件放入服务器的 `plugins` 目录
3. 重启服务器

### 方法二：从源码构建

```bash
# 克隆仓库
git clone https://github.com/DEVILENMO/EndStone-ARC-RealisticSurvival.git
cd EndStone-ARC-RealisticSurvival

# 构建插件
pip install build
python -m build

# 安装到服务器
cp dist/*.whl /path/to/server/plugins/
```

## 🔧 配置说明

### 主配置文件 (settings.yml)

插件首次运行会在 `ARCRealisticSurvival/settings.yml` 自动生成配置文件：

```yaml
thirst_tick_seconds: 10          # 口渴值衰减间隔（秒）
thirst_decay_per_tick: 1         # 每次衰减的口渴值
thirst_moving_multiplier: 2.0    # 移动时的衰减倍率
thirst_initial: 100              # 玩家初始口渴值
nutrition_tick_seconds: 300      # 营养衰减间隔（秒）
nutrition_decay_per_tick: 1      # 每次衰减的营养值
nutrition_initial: 100           # 玩家初始营养值
nutrition_warn_cooldown_seconds: 300  # 症状提示冷却（秒）
```

### 营养与缺素病

| 营养素 | 缺素病 | 主要症状 |
|--------|--------|----------|
| 维生素 A | 夜盲症 | 夜间随机短时黑暗 |
| 维生素 C | 坏血病 | 周期性掉血 + 虚弱 |
| 铁 | 贫血 | 最大生命下降 + 更易饥饿 |
| 蛋白质 | 肌无力 | 攻击下降 + 挖掘变慢 |

症状阈值：健康 ≥60，轻症 30-59，中症 10-29，重症 <10。

食物营养配置存储在 SQLite 表 `nutrition_items`（首次启动自动播种 30 种原版食物）。

### 物品效果配置 (thirst_items)

在 `ARCRealisticSurvival/thirst_items.txt` 中配置物品效果：

```text
# 格式：物品ID|口渴值变动|效果名称|持续时间（秒）
# 效果名称和持续时间可选

# 示例：
COOKED_BEEF|-10                    # 熟牛肉减少10点口渴值
WATER_BOTTLE|50                    # 水瓶增加50点口渴值
COLA|50|SPEED|30                   # 可乐增加50口渴值并给予30秒速度效果
ENERGY_DRINK|40|STRENGTH|60        # 能量饮料增加40口渴值并给予60秒力量效果
```

### 语言文件

插件支持多语言，语言文件位于 `ARCRealisticSurvival/` 目录：
- `CN.txt` - 简体中文
- `EN.txt` - English

## 🎮 使用方法

### 命令列表

| 命令 | 权限 | 描述 |
|------|------|------|
| `/ars` | `arc_realistic_survival.command.config` | 打开配置面板 |
| `/ars reload` | `arc_realistic_survival.command.config` | 重载配置 |
| `/ars nutrition` | 无 | 打开营养学面板（查看四条营养值与食物表） |
| `/ars nutriset <玩家> <营养素> <0-100>` | OP | 调试：设置玩家指定营养素 |

### 权限节点

- `arc_realistic_survival.command.config` - 允许使用配置命令（默认：仅OP）

### 游戏机制

1. **口渴值衰减**：
   - 静止状态：每 10 秒（默认）减少 1 点口渴值
   - 移动状态：衰减速度翻倍

2. **补充口渴值**：
   - 消耗配置文件中设置的物品
   - 口渴值会实时显示在屏幕上

3. **物品效果**：
   - 消耗特定物品可获得临时增益效果
   - 支持所有原版药水效果（通过 Endstone 原生 `Effect` API）

4. **营养学**：
   - 默认每 5 分钟四种营养素各 -1
   - 进食匹配 `nutrition_items` 的食物可补充对应营养
   - 使用 `/ars nutrition` 查看当前状态与食物营养表

## 🗄️ 数据存储

插件使用 SQLite 数据库存储玩家数据：

- **数据库位置**：`ARCRealisticSurvival/ars_survival.db`
- **存储内容**：
  - 玩家口渴值
  - 玩家四种营养素数值
  - 口渴/营养物品配置表
  - 最后更新时间
  - 玩家名称

数据会在以下情况自动保存：
- 玩家退出服务器
- 定时任务循环
- 服务器关闭

## 🔌 与其他插件集成

### 经济系统

插件支持以下经济插件：
- **ARC Core** (优先)
- **UMoney**

如果安装了以上任一插件，可实现经济系统相关功能。

## 🛠️ 开发

### 项目结构

```
src/endstone_arc_realistic_survival/
├── __init__.py              # 插件入口
├── arc_realistic_survival.py # 主插件逻辑
├── NutritionManager.py      # 营养学系统
├── DatabaseManager.py       # 数据库管理器
├── LanguageManager.py       # 语言管理器
└── SettingManager.py        # 设置管理器
```

### 构建插件

```bash
# 安装依赖
pip install -r requirements.txt

# 构建
python -m build
```

## 📝 更新日志

### v0.2.0
- 新增营养学系统（维生素 A/C、铁、蛋白质）
- 四种缺素病：夜盲症、坏血病、贫血、肌无力
- `/ars nutrition` 营养面板与 `/ars nutriset` 调试命令
- 修复 thirst_items 药水效果未生效的问题（改用原生 Effect API）
- 首次启动自动播种 30 种原版食物营养配置

### v0.1.0
- 初始版本发布
- 实现口渴值系统
- 实现物品效果系统
- 支持游戏内配置
- 支持热重载

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

本项目采用 [LICENSE](LICENSE) 中规定的许可证。

## 👤 作者

**DEVILENMO**
- Email: DEVILENMO@gmail.com
- GitHub: [@DEVILENMO](https://github.com/DEVILENMO)

## 🔗 相关链接

- [Endstone 官方文档](https://endstone.dev)
- [项目主页](https://github.com/DEVILENMO/EndstoneMC-ARC-Button-Shop-Plugin)

---

如有问题或建议，请通过 GitHub Issues 联系我们！
