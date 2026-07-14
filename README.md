# astrbot_plugin_maimaidx

[![python3](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

基于 [AstrBot](https://astrbot.app) 框架的街机音游 **舞萌DX** 查分插件

移植自 [maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX) 项目（原基于 HoshinoBot/NoneBot）

## 功能特性

- 🎵 查询歌曲信息、定数、BPM、曲师、谱师
- 📊 查询玩家成绩、Best 50、AP 50、牌子进度
- 🔀 多查分器数据源：支持 **水鱼查分器（Diving-Fish）** 与 **落雪查分器（Lxns-Network）** 自由切换
- 🧩 曲库合并：启动时将水鱼曲目与落雪曲目合并（补全白谱 / 宴会場等），渲染层仍使用兼容的 `Music` 模型
- 🎯 成绩统一：水鱼 / 落雪成绩先归一为 `PlayedResult`，再桥接为现有绘图模型
- 🏷️ 别名合并：柚子别名 + 落雪别名 + 本地别名启动时合并
- 🎮 猜歌游戏功能
- 🏪 机厅排卡功能
- 🏷️ 别名管理和推送
- 📈 定数表和完成表查询
- 📋 排行榜查询

## 安装方法

### 1. 克隆项目

```bash
git clone https://github.com/ZhiheZier/astrbot_plugin_maimaidx.git
```

### 2. 下载静态资源

下载静态资源文件，解压后将 `static` 文件夹复制到插件根目录并覆盖。

> 目录约定：JSON 数据文件位于 `static/data/`，字体文件位于 `static/font/`，图片素材位于 `static/mai/`。

**① 完整资源包（CN1.55，必需）**

解压后将其中的 `mai` 文件夹放入插件的 `static` 目录：

- [Cloudreve私人云盘](https://cloud.yuzuchan.moe/f/34s7/Resource%20CN1.55.7z)
- [onedrive](https://yuzuai-my.sharepoint.com/:u:/g/personal/yuzu_yuzuchan_moe/IQBGKHie6MAaTZy3rME7Q-ruAVKgXDCKROqz5e25KtMeeVY?e=53eC6a)
- [openlist](https://share.yuzuchan.moe/d/downloads/Resource%20CN1.55.7z?sign=4wMRn_9n6YZiEVV2vELKCEOj9zsgxScnmgtjsEL3C6g=:0)

**② rating 数字增量更新包（CN1.56，可选，覆盖新版底分素材）**

解压后覆盖 `static/mai/pic` 目录：

- [Cloudreve私人云盘](https://cloud.yuzuchan.moe/f/Jvhl/Resource%20CN1.56%20UPDATE.7z)
- [onedrive](https://yuzuai-my.sharepoint.com/:u:/g/personal/yuzu_yuzuchan_moe/IQDS_RzM66klSqvHtUhfFPTfAfpJcbGlIbL7Q6eSPxM4CA?e=xRPo7b)
- [openlist](https://share.yuzuchan.moe/d/downloads/Resource%20CN1.56%20UPDATE.7z?sign=p6h2Q9f3u87vRO8yU6ZSvCoagq0BE-xnX4wlhM55s_U=:0)

> 美术声明：请勿删除绘图设计中的署名。

### 3. 安装依赖

**重要：AstrBot 不会自动安装插件依赖，需要手动安装。**

安装 Python 依赖：

```bash
cd astrbot_plugin_maimaidx
pip install -r requirements.txt
```

安装 Chromium（用于图片生成）：

```bash
python -m playwright install --with-deps chromium
```

**注意**：在 Windows 上需要使用 `python -m playwright` 而不是直接使用 `playwright` 命令。

安装字体（Linux 系统，Windows 可跳过）：

```bash
apt install fonts-wqy-microhei
```

### 4. 配置插件

#### 4.1 插件配置（通过 AstrBot 配置界面）

所有配置均在 AstrBot 的插件配置界面中完成：

**基础**
- `bot_name`: 机器人名称，用于在消息中显示（如：今日运势功能），默认为 "Bot"
- `enable_reply`: 是否在多数指令回复中添加“引用消息”（Reply），默认为开启

**水鱼查分器（Diving-Fish）**
- `maimaidxtoken`: 水鱼查分器开发者 token（由于水鱼修改了请求鉴权，未填写时仅可使用 `b50` 指令）
- `maimaidxproberproxy`: 是否使用中转访问水鱼查分器（适用于境外服务器），默认关闭

**别名 / 素材 / 性能**
- `maimaidxaliaspush`: 是否开启别名推送，默认开启
- `maimaidxaliasproxy`: 是否使用中转访问柚子别名服务器（适用于境内服务器），默认关闭
- `maimaidxaliaswhitelist`: 别名推送是否采用白名单（默认关闭）。开启后仅向已执行「开启别名推送」的群广播；关闭时为「全群推送 + disable 黑名单」
- `saveinmem`: 是否将部分图片保存在内存中，默认开启（`false` 可节省内存，但生成稍慢）
- `assets_online`: 是否在线获取素材，默认开启（有本地 icon/plate 素材时可设为 `false`）

**落雪查分器（Lxns-Network，可选，用于支持第二数据源）**
- `lxns_dev_token`: 落雪查分器开发者 Token。填写后用户即可用「数据源 落雪」按 QQ 号查询（需用户提前在落雪绑定 QQ 号，并在「隐私设置」中允许第三方读取成绩）。支持 b50 / ap50 / 单曲成绩 / 完成表 / 进度 等功能
- `lx_client_id`: 落雪 OAuth 应用的 client_id（可选，OAuth 权限范围请选择前三项，不含「读取个人 API 秘钥」）
- `lx_client_secret`: 落雪 OAuth 应用的 client_secret（可选）
- `lx_redirect_uri`: 落雪 OAuth 回调地址（可选，需与落雪 OAuth 应用登记的回调地址一致）

> 说明：仅配置 `lxns_dev_token` 即可让用户按 QQ 号查询落雪成绩；若额外配置 OAuth 应用（`lx_client_id` 等），用户可通过「绑定落雪」进行授权，获取含**精确达成率**的完整成绩，从而支持「分数列表」「我要上分」等功能。

### 5. 配置超级管理员

在 AstrBot 主配置文件中设置管理员ID列表（字段名为 `admins_id`），用于执行更新数据等管理命令。

**注意**：管理员ID配置在 AstrBot 的主配置文件中，不在插件配置中。

### 6. 启用插件

将插件目录放置在 AstrBot 的插件目录下，重启 AstrBot 即可。

## 主要命令

### 基础查询
- `查歌 <关键词>` / `search <关键词>` - 搜索歌曲
- `定数查歌 <定数>` - 按定数搜索
- `bpm查歌 <bpm>` - 按 BPM 搜索
- `曲师查歌 <曲师名>` - 按曲师搜索
- `谱师查歌 <谱师名>` - 按谱师搜索
- `id <歌曲id>` - 查询指定歌曲信息
- `是什么歌 <别名>` - 通过别名查询歌曲

### 成绩查询
- `b50 <QQ号>` - 查询 Best 50
- `ap50 <QQ号>` - 查询 AP Best 50（**仅落雪数据源**支持）
- `分数线 <难度+id> <分数>` - 查询分数线
- `牌子进度 <QQ号>` - 查询牌子进度
- `牌子条件` - 查看各牌子的完成条件说明图
- `查看排名` - 查看排行榜（水鱼查分器）

### 数据源 / 落雪查分器
- `数据源` - 查看当前数据源
- `数据源 水鱼` / `数据源 落雪` - 切换查分数据源（也可用 `数据源 0` / `数据源 1`）
- `绑定落雪` / `lxbind` - 引导进行落雪 OAuth 授权（需管理员配置 OAuth 应用）
- `授权码 XXXX-XXXX-XXXX` / `code XXXX-XXXX-XXXX` - 使用落雪返回的授权码完成绑定
- `主题` / `主题 <序号>` - 查看 / 切换成绩图主题（`0`：prism_plus，`1`：circle）

### 猜歌游戏
- `猜歌` - 开始猜歌
- `猜歌提示` - 获取提示
- `猜歌重置` - 重置游戏

### 机厅功能
- `帮助maimaiDX排卡` - 查看机厅帮助
- `添加机厅 <店名> <地址> <id>` - 添加机厅
- `查找机厅 <关键词>` - 查找机厅
- `订阅机厅 <店名>` - 订阅机厅
- `机厅几人` - 查看已订阅机厅排卡人数

### 别名管理
- `添加别名 <歌曲id> <别名>` - 添加别名
- `当前投票` - 查看当前别名投票
- `开启别名推送` / `关闭别名推送` - 开启/关闭别名推送

### 管理命令（需要超级管理员权限）
- `更新maimai数据` - 更新歌曲数据
- `更新定数表` - 更新定数表
- `更新完成表` - 更新完成表
- `更新别名库` - 更新别名库

## 迁移说明

本插件从 HoshinoBot/NoneBot 框架迁移到 AstrBot 框架。

### 主要变更

- ✅ 所有命令已迁移到 astrbot 框架
- ✅ 移除了对 `hoshino` 包的依赖
- ✅ 使用 astrbot 的权限管理系统（`admins_id`）
- ✅ 支持主动消息发送（猜歌提示、别名推送等）
- ✅ 支持手动配置 bot 名称（通过插件配置界面）
- ✅ 新增多查分器数据源：水鱼查分器 / 落雪查分器（每用户可独立切换）
- ✅ 新增 `ap50`、`数据源`、`主题`、`绑定落雪`、`授权码` 等指令

## 注意事项

1. **首次使用**：首次使用需要执行 `更新定数表` 和 `更新完成表` 命令生成相关数据
2. **资源文件**：必须下载并配置静态资源文件，否则部分功能无法使用
3. **别名推送**：如果关闭别名推送，将不会实时更新别名库
4. **内存配置**：`saveinmem` 设置为 `false` 可节省内存，但 Best 50 图片生成会稍慢

## 许可证

MIT License

## 致谢

- 原项目：[maimaiDX](https://github.com/Yuri-YuzuChaN/maimaiDX)
- 水鱼查分器：[mai-bot](https://github.com/Diving-Fish/mai-bot) / [Diving-Fish](https://www.diving-fish.com/maimaidx/prober/)
- 落雪查分器：[Lxns-Network](https://maimai.lxns.net/)
- 框架：[AstrBot](https://astrbot.app)

## 支持

如有问题，请提交 Issue 或查看 [AstrBot 帮助文档](https://astrbot.app)
