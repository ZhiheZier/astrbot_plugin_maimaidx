from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.star.filter.event_message_type import EventMessageType
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pathlib import Path
import asyncio

from .command.mai_alias import ws_alias_server
from .libraries.maimai_best_50 import ScoreBaseImage
from .libraries.maimaidx_api_data import maiApi
from .libraries.maimaidx_music import mai
from . import Root, log, loga, ratingdir, platedir, plate_to_dx_version, platecn

@register("astrbot_plugin_maimaidx", "Yuri-YuzuChaN", "maimaiDX 查分插件", "1.0.0")
class MaimaiDXPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        # 从配置中获取超级管理员列表
        self.superusers = self.config.get("superusers", [])

    async def initialize(self):
        """插件初始化，加载数据并设置定时任务"""
        if maiApi.config.maimaidxproberproxy:
            log.info('正在使用代理服务器访问查分器')
        if maiApi.config.maimaidxaliasproxy:
            log.info('正在使用代理服务器访问别名服务器')
        maiApi.load_token_proxy()
        if maiApi.config.maimaidxaliaspush:
            log.info('别名推送为「开启」状态')
            # 获取 bot client 用于别名推送
            bot_client = None
            try:
                # 尝试从 context 获取 bot client
                adapters = self.context.get_adapters()
                if adapters:
                    adapter = list(adapters.values())[0]
                    bot_client = adapter.get_client() if hasattr(adapter, 'get_client') else None
            except Exception as e:
                log.warning(f'获取 bot client 失败: {e}')
            asyncio.ensure_future(ws_alias_server(bot_client))
        else:
            log.info('别名推送为「关闭」状态')
        log.info('正在获取maimai所有曲目信息')
        await mai.get_music()
        log.info('正在获取maimai牌子数据')
        await mai.get_plate_json()
        log.info('正在获取maimai所有曲目别名信息')
        await mai.get_music_alias()
        mai.guess()
        log.info('maimai数据获取完成')
        
        # 初始化机厅数据
        try:
            from .libraries.maimaidx_arcade import arcade
            loga.info('正在获取maimai所有机厅信息')
            await arcade.getArcade()
            loga.info('maimai机厅数据获取完成')
            
            # 添加机厅数据每日更新定时任务（每天凌晨3点）
            self.scheduler.add_job(
                self._arcade_daily_update,
                trigger=CronTrigger(hour=3),
                name="arcade_daily_update",
                misfire_grace_time=300
            )
        except ImportError:
            loga.warning('机厅功能未迁移，跳过机厅数据初始化')
        except Exception as e:
            loga.error(f'机厅数据初始化失败: {e}')
        
        if maiApi.config.saveinmem:
            ScoreBaseImage._load_image()
            log.info('已将图片保存在内存中')
        
        if not list(ratingdir.iterdir()):
            log.warning(
                '注意！注意！检测到定数表文件夹为空！'
                '可能导致「定数表」「完成表」指令无法使用，'
                '请及时私聊BOT使用指令「更新定数表」进行生成。'
            )
        plate_list = [name for name in list(plate_to_dx_version.keys())[1:]]
        platedir_list = [f.name.split('.')[0] for f in platedir.iterdir()]
        cn_list = [name for name in list(platecn.keys())]
        notin = set(plate_list) - set(platedir_list) - set(cn_list)
        if notin:
            anyname = '，'.join(notin)
            log.warning(
                f'注意！注意！未检测到牌子文件夹中的牌子：{anyname}，'
                '可能导致这些牌子的「完成表」指令无法使用，'
                '请及时私聊BOT使用指令「更新完成表」进行生成。'
            )
        
        # 设置定时任务：每天凌晨4点更新数据
        self.scheduler.add_job(
            self._daily_update,
            trigger=CronTrigger(hour=4),
            name="maimai_daily_update",
            misfire_grace_time=300
        )
        log.info('maimaiDX插件初始化完成')

    async def _daily_update(self):
        """定时任务：每日更新数据"""
        try:
            await mai.get_music()
            mai.guess()
            log.info('maimaiDX数据更新完毕')
        except Exception as e:
            log.error(f'定时更新数据失败: {e}')
    
    async def _arcade_daily_update(self):
        """机厅数据每日更新 - 每天凌晨3点执行"""
        from .command.mai_arcade import arcade_daily_update
        await arcade_daily_update()

    # 注册命令处理函数
    @filter.command("更新maimai数据")
    async def update_data(self, event: AstrMessageEvent):
        """更新maimai数据"""
        from .command.mai_base import update_data_handler
        async for result in update_data_handler(event, self.superusers):
            yield result

    @filter.command(["帮助maimaiDX", "帮助maimaidx"])
    async def maimaidxhelp(self, event: AstrMessageEvent):
        """帮助maimaiDX"""
        from .command.mai_base import maimaidxhelp_handler
        async for result in maimaidxhelp_handler(event):
            yield result

    @filter.command(["项目地址maimaiDX", "项目地址maimaidx"])
    async def maimaidxrepo(self, event: AstrMessageEvent):
        """项目地址"""
        from .command.mai_base import maimaidxrepo_handler
        async for result in maimaidxrepo_handler(event):
            yield result

    @filter.command(["今日mai", "今日舞萌", "今日运势"])
    async def mai_today(self, event: AstrMessageEvent):
        """今日运势"""
        from .command.mai_base import mai_today_handler
        async for result in mai_today_handler(event):
            yield result

    @filter.regex(r'.*mai.*什么(.+)?')
    async def mai_what(self, event: AstrMessageEvent):
        """mai什么"""
        from .command.mai_base import mai_what_handler
        async for result in mai_what_handler(event):
            yield result

    @filter.regex(r'^[来随给]个((?:dx|sd|标准))?([绿黄红紫白]?)([0-9]+\+?)$')
    async def random_song(self, event: AstrMessageEvent):
        """随机歌曲"""
        from .command.mai_base import random_song_handler
        async for result in random_song_handler(event):
            yield result

    @filter.command(["查看排名", "查看排行"])
    async def rating_ranking(self, event: AstrMessageEvent):
        """查看排名"""
        from .command.mai_base import rating_ranking_handler
        async for result in rating_ranking_handler(event):
            yield result

    @filter.command("我的排名")
    async def my_rating_ranking(self, event: AstrMessageEvent):
        """我的排名"""
        from .command.mai_base import my_rating_ranking_handler
        async for result in my_rating_ranking_handler(event):
            yield result

    # 成绩相关命令
    @filter.command(["b50", "B50"])
    async def best50(self, event: AstrMessageEvent):
        """查询最佳50首"""
        from .command.mai_score import best50_handler
        async for result in best50_handler(event):
            yield result

    @filter.command(["minfo", "Minfo", "MINFO", "info", "Info", "INFO"])
    async def minfo(self, event: AstrMessageEvent):
        """查询谱面信息"""
        from .command.mai_score import minfo_handler
        async for result in minfo_handler(event):
            yield result

    @filter.command(["ginfo", "Ginfo", "GINFO"])
    async def ginfo(self, event: AstrMessageEvent):
        """查询全局统计信息"""
        from .command.mai_score import ginfo_handler
        async for result in ginfo_handler(event):
            yield result

    @filter.command("分数线")
    async def score(self, event: AstrMessageEvent):
        """查询分数线"""
        from .command.mai_score import score_handler
        async for result in score_handler(event):
            yield result

    # 搜索相关命令
    @filter.command(["查歌", "search"])
    async def search_music(self, event: AstrMessageEvent):
        """搜索歌曲"""
        from .command.mai_search import search_music_handler
        async for result in search_music_handler(event):
            yield result

    @filter.command(["定数查歌", "search base"])
    async def search_base(self, event: AstrMessageEvent):
        """按定数搜索"""
        from .command.mai_search import search_base_handler
        async for result in search_base_handler(event):
            yield result

    @filter.command(["bpm查歌", "search bpm"])
    async def search_bpm(self, event: AstrMessageEvent):
        """按BPM搜索"""
        from .command.mai_search import search_bpm_handler
        async for result in search_bpm_handler(event):
            yield result

    @filter.command(["曲师查歌", "search artist"])
    async def search_artist(self, event: AstrMessageEvent):
        """按曲师搜索"""
        from .command.mai_search import search_artist_handler
        async for result in search_artist_handler(event):
            yield result

    @filter.command(["谱师查歌", "search charter"])
    async def search_charter(self, event: AstrMessageEvent):
        """按谱师搜索"""
        from .command.mai_search import search_charter_handler
        async for result in search_charter_handler(event):
            yield result

    @filter.command(["是什么歌", "是啥歌"])
    async def search_alias_song(self, event: AstrMessageEvent):
        """通过别名搜索"""
        from .command.mai_search import search_alias_song_handler
        async for result in search_alias_song_handler(event):
            yield result

    @filter.regex(r'^id\s?([0-9]+)$')
    async def query_chart(self, event: AstrMessageEvent):
        """通过ID查询"""
        from .command.mai_search import query_chart_handler
        async for result in query_chart_handler(event):
            yield result

    # 猜歌相关命令
    @filter.command("猜歌")
    async def guess_music_start(self, event: AstrMessageEvent):
        """开始猜歌"""
        from .command.mai_guess import guess_music_start_handler
        async for result in guess_music_start_handler(event):
            yield result

    @filter.command("猜曲绘")
    async def guess_pic(self, event: AstrMessageEvent):
        """开始猜曲绘"""
        from .command.mai_guess import guess_pic_handler
        async for result in guess_pic_handler(event):
            yield result

    @filter.command("重置猜歌")
    async def reset_guess(self, event: AstrMessageEvent):
        """重置猜歌"""
        from .command.mai_guess import reset_guess_handler
        async for result in reset_guess_handler(event):
            yield result

    @filter.command(["开启mai猜歌", "关闭mai猜歌"])
    async def guess_on_off(self, event: AstrMessageEvent):
        """开启/关闭猜歌功能"""
        from .command.mai_guess import guess_on_off_handler
        async for result in guess_on_off_handler(event):
            yield result

    # 猜歌答案监听 - 只监听群消息，不阻塞其他命令
    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def guess_music_solve(self, event: AstrMessageEvent):
        """处理猜歌答案"""
        from .command.mai_guess import guess_music_solve_handler
        async for result in guess_music_solve_handler(event):
            yield result

    # 表格相关命令
    @filter.command("更新定数表")
    async def update_table(self, event: AstrMessageEvent):
        """更新定数表"""
        from .command.mai_table import update_table_handler
        async for result in update_table_handler(event, self.superusers):
            yield result

    @filter.command("更新完成表")
    async def update_plate(self, event: AstrMessageEvent):
        """更新完成表"""
        from .command.mai_table import update_plate_handler
        async for result in update_plate_handler(event, self.superusers):
            yield result

    @filter.command("定数表")
    async def rating_table(self, event: AstrMessageEvent):
        """查询定数表"""
        from .command.mai_table import rating_table_handler
        async for result in rating_table_handler(event):
            yield result

    @filter.command("完成表")
    async def table_pfm(self, event: AstrMessageEvent):
        """查询完成表"""
        from .command.mai_table import table_pfm_handler
        async for result in table_pfm_handler(event):
            yield result

    @filter.regex(r'^我要在?([0-9]+\+?)?[上加\+]([0-9]+)?分\s?(.+)?')
    async def rise_score(self, event: AstrMessageEvent):
        """查询上分数据"""
        from .command.mai_table import rise_score_handler
        async for result in rise_score_handler(event):
            yield result

    @filter.regex(r'^([真超檄橙暁晓桃櫻樱紫菫堇白雪輝辉舞霸熊華华爽煌星宙祭祝双宴镜])([極极将舞神者]舞?)进度\s?(.+)?')
    async def plate_process(self, event: AstrMessageEvent):
        """查询牌子进度"""
        from .command.mai_table import plate_process_handler
        async for result in plate_process_handler(event):
            yield result

    @filter.regex(r'^([0-9]+\+?)\s?([abcdsfxp\+]+)\s?([\u4e00-\u9fa5]+)?进度\s?([0-9]+)?\s?(.+)?')
    async def level_process(self, event: AstrMessageEvent):
        """查询等级进度"""
        from .command.mai_table import level_process_handler
        async for result in level_process_handler(event):
            yield result

    @filter.regex(r'^([0-9]+\.?[0-9]?\+?)分数列表\s?([0-9]+)?\s?(.+)?')
    async def level_achievement_list(self, event: AstrMessageEvent):
        """查询分数列表"""
        from .command.mai_table import level_achievement_list_handler
        async for result in level_achievement_list_handler(event):
            yield result

    # 别名相关命令
    @filter.command("更新别名库")
    async def update_alias(self, event: AstrMessageEvent):
        """更新别名库"""
        from .command.mai_alias import update_alias_handler
        async for result in update_alias_handler(event, self.superusers):
            yield result

    @filter.command("全局开启别名推送")
    async def alias_switch_on(self, event: AstrMessageEvent):
        """全局开启别名推送"""
        from .command.mai_alias import alias_switch_on_handler
        async for result in alias_switch_on_handler(event, self.superusers):
            yield result

    @filter.command("全局关闭别名推送")
    async def alias_switch_off(self, event: AstrMessageEvent):
        """全局关闭别名推送"""
        from .command.mai_alias import alias_switch_off_handler
        async for result in alias_switch_off_handler(event, self.superusers):
            yield result

    @filter.command(["添加本地别名", "添加本地别称"])
    async def alias_local_apply(self, event: AstrMessageEvent):
        """添加本地别名"""
        from .command.mai_alias import alias_local_apply_handler
        async for result in alias_local_apply_handler(event):
            yield result

    @filter.command(["添加别名", "增加别名", "增添别名", "添加别称"])
    async def alias_apply(self, event: AstrMessageEvent):
        """添加别名"""
        from .command.mai_alias import alias_apply_handler
        async for result in alias_apply_handler(event):
            yield result

    @filter.command(["同意别名", "同意别称"])
    async def alias_agree(self, event: AstrMessageEvent):
        """同意别名"""
        from .command.mai_alias import alias_agree_handler
        async for result in alias_agree_handler(event):
            yield result

    @filter.command(["当前投票", "当前别名投票", "当前别称投票"])
    async def alias_status(self, event: AstrMessageEvent):
        """查询当前投票"""
        from .command.mai_alias import alias_status_handler
        async for result in alias_status_handler(event):
            yield result

    @filter.command(["开启别名推送", "关闭别名推送", "开启别称推送", "关闭别称推送"])
    async def alias_switch(self, event: AstrMessageEvent):
        """开启/关闭别名推送"""
        from .command.mai_alias import alias_switch_handler
        async for result in alias_switch_handler(event):
            yield result

    @filter.regex(r'^(id)?\s?(.+)\s?有什么别[名称]$')
    async def alias_song(self, event: AstrMessageEvent):
        """查询歌曲别名"""
        from .command.mai_alias import alias_song_handler
        async for result in alias_song_handler(event):
            yield result

    # 机厅相关命令
    @filter.command(["帮助maimaiDX排卡", "帮助maimaidx排卡"])
    async def arcade_help(self, event: AstrMessageEvent):
        """帮助maimaiDX排卡"""
        from .command.mai_arcade import arcade_help_handler
        async for result in arcade_help_handler(event):
            yield result

    @filter.command(["添加机厅", "新增机厅"])
    async def add_arcade(self, event: AstrMessageEvent):
        """添加机厅"""
        from .command.mai_arcade import add_arcade_handler
        async for result in add_arcade_handler(event, self.superusers):
            yield result

    @filter.command(["删除机厅", "移除机厅"])
    async def delete_arcade(self, event: AstrMessageEvent):
        """删除机厅"""
        from .command.mai_arcade import delete_arcade_handler
        async for result in delete_arcade_handler(event, self.superusers):
            yield result

    @filter.command(["添加机厅别名", "删除机厅别名"])
    async def arcade_alias(self, event: AstrMessageEvent):
        """添加/删除机厅别名"""
        from .command.mai_arcade import arcade_alias_handler
        async for result in arcade_alias_handler(event, self.superusers):
            yield result

    @filter.command(["修改机厅", "编辑机厅"])
    async def modify_arcade(self, event: AstrMessageEvent):
        """修改机厅"""
        from .command.mai_arcade import modify_arcade_handler
        async for result in modify_arcade_handler(event):
            yield result

    @filter.regex(r'^(订阅机厅|取消订阅机厅|取消订阅)\s(.+)')
    async def subscribe_arcade(self, event: AstrMessageEvent):
        """订阅/取消订阅机厅"""
        from .command.mai_arcade import subscribe_arcade_handler
        async for result in subscribe_arcade_handler(event):
            yield result

    @filter.command(["查看订阅", "查看订阅机厅"])
    async def check_subscribe(self, event: AstrMessageEvent):
        """查看订阅"""
        from .command.mai_arcade import check_subscribe_handler
        async for result in check_subscribe_handler(event):
            yield result

    @filter.command(["查找机厅", "查询机厅", "机厅查找", "机厅查询", "搜素机厅", "机厅搜素"])
    async def search_arcade(self, event: AstrMessageEvent):
        """查找机厅"""
        from .command.mai_arcade import search_arcade_handler
        async for result in search_arcade_handler(event):
            yield result

    @filter.regex(r'^(.+)?\s?(设置|设定|＝|=|增加|添加|加|＋|\+|减少|降低|减|－|-)\s?([0-9]+|＋|\+|－|-)(人|卡)?$')
    async def arcade_person(self, event: AstrMessageEvent):
        """操作排卡人数"""
        from .command.mai_arcade import arcade_person_handler
        async for result in arcade_person_handler(event):
            yield result

    @filter.command(["机厅几人", "jtj"])
    async def arcade_query_multiple(self, event: AstrMessageEvent):
        """机厅几人 - 查看已订阅机厅排卡人数"""
        from .command.mai_arcade import arcade_query_multiple_handler
        async for result in arcade_query_multiple_handler(event):
            yield result

    @filter.regex(r'(.+)?(有多少人|有几人|有几卡|多少人|多少卡|几人|jr|几卡)$')
    async def arcade_query_person(self, event: AstrMessageEvent):
        """查询排卡人数"""
        from .command.mai_arcade import arcade_query_person_handler
        async for result in arcade_query_person_handler(event):
            yield result

    async def terminate(self):
        """插件销毁时停止定时任务"""
        if self.scheduler.running:
            self.scheduler.shutdown()
        log.info('maimaiDX插件已卸载')
