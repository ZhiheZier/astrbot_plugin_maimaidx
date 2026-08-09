## v1.3.1

> ⚠️ **v1.3.1 更新说明**：static 目录迁移到了 AstrBot 持久化数据目录，**更新后需要重新下载资源包并放入新位置**，详情见下方安装步骤。旧数据（群组配置、用户偏好等）会自动迁移。

### 新功能
- 合并 PR #9: 群级排卡功能开关 (`开启排卡`/`关闭排卡`)
- 合并 PR #10: 水鱼数据源支持 AP50/AP+50/理论b50/一星~五星b50/拟合b50
- 新增宴会場谱面详情 (`draw_music_banquet_info`)
- 所有正则指令支持可选 `/` 前缀
- 新增帮助命令别名 `helpmaimai`/`helpmaimaiDX`/`helpmaimaidx`

### 修复
- 背景图 aurora_bg/rainbow_bottom_bg 移除强制 resize，使用原始尺寸
- 移植 PRiSM PLUS 渐变 + frosted_card + separator.png
- 定数表/完成表网格改为 divmod + START_Y 累积对齐原项目
- 使用原项目 border_*.png/border_table_base.png 封面边框
- 完成表补回进度条/统计面板/百分比
- 谱面详情 draw_music_info 完整对齐原项目布局
- 游玩详情 ra_dx.png 补回 resize
- 别名合并 set→dict.fromkeys 保序
- mai_search get_songs except 范围扩大
- PushAliasStatus 兼容小写字段名
- ap+50 支持 QQ 参数
- dxScore 保持 int 型对齐原项目

### 其他
- 统一管理员提示语
- 搜索功能（查歌/定数查歌/bpm查歌/曲师查歌/谱师查歌等）添加引用回复
- 定数查歌支持 7+ 等格式（7.6~7.9）
- 上分推荐新版本范围只取最新版本
- static/ 迁移到 AstrBot 持久化数据目录，重装不丢失
  - _update_submodules 同步路径（含字体）到所有已导入子模块
  - main.py 通过 sys.modules 动态引用路径
  - README 资源包路径更新
  - disabled_groups/enabled_arcade_groups/group_guess_switch/user_data 存于持久化根目录
