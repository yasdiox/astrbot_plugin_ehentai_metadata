import json
import datetime
import time
from pathlib import Path
from astrbot.api import logger
import urllib.parse
import httpx
from typing import Tuple, Dict, Any
from collections import defaultdict

API_URL = "https://api.e-hentai.org/api.php"
RESOURCE_DIR = Path(__file__).parent / "resource"
DB_TEXT = RESOURCE_DIR / "db.text.json"

# remote release asset (latest) - use text JSON
REMOTE_DB_URL = (
    "https://github.com/EhTagTranslation/Database/releases/latest/download/db.text.json"
)

INDEX_MAP = {
    "language": 2,
    "parody": 3,
    "character": 4,
    "group": 5,
    "artist": 6,
    "cosplayer": 7,
    "male": 8,
    "female": 9,
    "mixed": 10,
    "other": 11,
}

class metadata:
    @staticmethod
    async def get_metadata(gid: int, code: str, proxy: str | None = None) -> Tuple[Dict[str, Any] | None, Dict[str, list]]:
        # ensure local DB exists/updated
        await metadata.ensure_db(proxy=proxy)

        payload = {"method": "gdata", "gidlist": [[gid, code]], "namespace": 1}

        async with httpx.AsyncClient(proxy=proxy) as client:
            try:
                response = await client.post(API_URL, json=payload, timeout=10.0)
            except Exception as e:
                logger.error("获取画廊信息失败，网络错误：%s", e)
                return None, {}

        if response.status_code != 200:
            logger.error("获取画廊信息失败，状态码：%s", response.status_code)
            return None, {}

        try:
            result = response.json()
        except json.JSONDecodeError:
            logger.error("响应内容不是有效的 JSON 格式。")
            return None, {}

        # 载入本地翻译数据库（db.text.json）
        if not DB_TEXT.exists():
            logger.error("翻译数据库缺失：%s", DB_TEXT)
            return None, {}

        try:
            with DB_TEXT.open(encoding="utf-8") as f:
                json_data = json.load(f)
        except Exception as e:
            logger.error("读取翻译数据库失败：%s", e)
            return None, {}

        meta = result.get("gmetadata", [])
        if not meta:
            logger.error("响应中没有 gmetadata")
            return None, {}

        entry = meta[0]

        # 磁链处理
        torrents = entry.get("torrents", [])
        magnet_links = []
        for t in torrents:
            h = t.get("hash")
            name = t.get("name")
            size = t.get("fsize")
            if h and name:
                magnet_links.append(f"magnet:?xt=urn:btih:{h}&dn={name}&xl={size}")
        magnet_link = "(无)" if not magnet_links else "\n".join(magnet_links)

        # tags 解析
        tags_dict: Dict[str, list] = defaultdict(list)
        for tag in entry.get("tags", []):
            parts = tag.split(":", 1)
            if len(parts) != 2:
                continue
            t_type, t_value = parts
            zh_type = (
                json_data.get("data", [])[0].get("data", {}).get(t_type, {}).get("name")
                if json_data.get("data") else t_type
            )
            idx = INDEX_MAP.get(t_type)
            zh_value = (
                json_data.get("data", [])[idx].get("data", {}).get(t_value, {}).get("name")
                if idx is not None and idx < len(json_data.get("data", []))
                else t_value
            )
            tags_dict[zh_type].append(zh_value)

        # 构造返回数据
        posted = entry.get("posted")
        try:
            posted_ts = int(posted) if posted is not None else 0
            posted_dt = datetime.datetime.fromtimestamp(posted_ts, datetime.timezone.utc).astimezone()
            posted_str = posted_dt.strftime("%Y/%m/%d %H:%M")
        except Exception:
            posted_str = "(未知)"

        out = {
            "页数": entry.get("filecount"),
            "类型": entry.get("category"),
            "图片": entry.get("thumb"),
            "日文标题": entry.get("title_jpn"),
            "磁链": magnet_link,
            "时间": posted_str,
            "评分": entry.get("rating"),
            "标题": entry.get("title"),
            "gid": entry.get("gid"),
            "token": entry.get("token"),
        }

        logger.info("成功获取画廊信息：%s / %s", out.get("gid"), out.get("标题"))
        return out, dict(tags_dict)

    @staticmethod
    async def create_hastebin_clipboard(content: str, proxy: str | None = None) -> str | None:
        """
        将内容上传至 paste.rs 并生成二维码链接
        """
        url = "https://paste.rs"
        
        # 确保代理格式正确，如果是空字符串则设为 None
        active_proxy = proxy if proxy and proxy.strip() else None
        
        # 1. 构造与 curl 行为一致的 Headers
        headers = {
            "Content-Type": "text/plain",
            "User-Agent": "Mozilla/5.0"  # 模拟浏览器以增加兼容性
        }

        async with httpx.AsyncClient(proxy=active_proxy, timeout=10.0, follow_redirects=True) as client:
            try:
                # 2. 直接发送 content (httpx 会根据 headers 处理编码)
                response = await client.post(url, content=content, headers=headers)
                
                # 3. 严格校验状态码 (paste.rs 成功通常返回 201 或 200)
                if not (200 <= response.status_code < 300):
                    logger.error("上传失败，服务器返回状态码：%s", response.status_code)
                    # 打印前 100 个字符方便排查是否又是那个 HTML 错误页
                    logger.debug("错误响应内容：%s", response.text[:100])
                    return None

                # 4. 获取并清洗返回的 URL
                paste_url = response.text.strip()
                if not paste_url.startswith("http"):
                    logger.error("返回的内容不是有效的 URL：%s", paste_url)
                    return None

                logger.info("成功创建剪贴板：%s", paste_url)

                # 5. 生成二维码 API 链接
                encoded_url = urllib.parse.quote(paste_url, safe="")
                qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?data={encoded_url}&size=200x200"
                
                logger.info("二维码图片链接：%s", qr_api_url)
                return qr_api_url

            except httpx.RequestError as e:
                logger.error("上传失败，网络异常或代理配置错误: %s", e)
                return None
            except Exception as e:
                logger.error("发生未知错误: %s", e)
                return None

    @staticmethod
    async def ensure_db(proxy: str | None = None, force_update: bool = False) -> None:
        """确保 resource 下存在最新的 db.text.json

        - 若文件不存在或已超过 24 小时（或 force_update=True），则从 RELEASES 下载最新 db.text.json。
        - 下载成功后保存为 `db.text.json`。
        """

        target = DB_TEXT
        # 判断是否需要更新
        if target.exists() and not force_update:
            mtime = target.stat().st_mtime
            # 24 小时
            if (time.time() - mtime) < 24 * 3600:
                return

        async with httpx.AsyncClient(
            proxy=proxy,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "*/*",
                "Accept-Encoding": "gzip, deflate, br"
            }
        ) as client:
            try:
                resp = await client.get(REMOTE_DB_URL, timeout=20.0)
            except Exception as e:
                logger.error("下载翻译数据库失败：%s", e)
                return

        if resp.status_code != 200:
            logger.error("下载翻译数据库失败，状态码：%s", resp.status_code)
            return

        try:
            target.write_bytes(resp.content)
            logger.info("已更新翻译数据库：%s", target)
        except Exception as e:
            logger.error("写入翻译数据库失败：%s", e)
            

        
