from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger, AstrBotConfig

from pathlib import Path
import re
from textwrap import dedent

from .data_source import metadata
from ..astrbot_plugin_htmlrender.htmlrender import template_to_pic

PATTERN = re.compile(r"https://(?:e-hentai|exhentai)\.org/g/(?P<id>\d+)/(?P<code>[A-Za-z0-9]+)/?")


class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

    def get_proxy(self):
        proxy_config = self.config.get('network_proxy', {})
        mode = proxy_config.get('proxy_mode', 'system')
        if mode == 'system':
            return None  # httpx 默认使用系统代理
        elif mode == 'custom':
            url = proxy_config.get('proxy_url', '')
            return url if url else None
        return None

    # 获取最新译文数据库
    async def initialize(self): 
        try:
            import asyncio
            proxy = self.get_proxy()
            asyncio.create_task(metadata.ensure_db(proxy=proxy))
        except Exception:
            logger.warning("无法在后台启动翻译数据库更新任务")

    @filter.regex(PATTERN.pattern)
    async def ehentai_metadata(self, event: AstrMessageEvent):
        match = PATTERN.search(event.message_str or "")
        if not match:
            return

        gid = int(match.group("id"))
        code = match.group("code")

        proxy = self.get_proxy()
        data, tags_dict = await metadata.get_metadata(gid, code, proxy=proxy)
        if not data:
            return

        clip_text = dedent(
            f"""
            ├──────────────────────────────────────────────

            ◈ 标题    ┃ {data.get('标题') or '(无)'}
            ◈ 日标    ┃ {data.get('日文标题') or '(无)'}

            ├──────────────────────────────────────────────

            ◈ 时间    ┃ {data.get('时间')}
            ◈ 类型    ┃ {data.get('类型')}
            ◈ 页数    ┃ {data.get('页数')}
            ◈ 评分    ┃ {data.get('评分')}

            ├──────────────────────────────────────────────

            ◈ 磁链    ┃ {data.get('磁链')}
            ◈ 标识    ┃ {data.get('gid')} / {data.get('token')}

            ├──────────────────────────────────────────────
            """
        ).strip()

        qr_api_url = await metadata.create_hastebin_clipboard(clip_text, proxy=proxy)

        template_path = Path(__file__).parent / "resource"
        template_name = "text.html"
        pic = await template_to_pic(
            template_path=str(template_path),
            template_name=template_name,
            templates={"data": data, "tag": tags_dict, "url": qr_api_url},
            pages={
                "viewport": {"width": 850, "height": 300},
                "base_url": template_path.as_uri(),
            },
        )

        logger.info("图片已生成: %s", pic)
        yield event.image_result(pic)