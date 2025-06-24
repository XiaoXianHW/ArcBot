import asyncio
import base64
import copy
import json
import logging
import re
from datetime import timezone
from typing import List, Any
from pathlib import Path

import pytz
import socks
from telethon import TelegramClient, events
from telethon.tl.types import MessageMediaPoll, PeerChannel

from llm_handler import load_llm_filter
from post_extension import load_config, send_msg_to_group
from text_formatter import process_markdown_links_and_add_references

# ────────────────── 配置 ──────────────────
config = load_config()

# 路径自适应
SCRIPT_DIR = Path(__file__).parent.resolve()
SESSION_PATH = SCRIPT_DIR / "sessions"
SESSION_PATH.mkdir(exist_ok=True) # 自动创建 sessions 目录

api_id            = config["api_id"]
api_hash          = config["api_hash"]
phone_number      = config["phone_number"]
channel_usernames = config["channel_usernames"]
proxy_cfg         = config.get("proxy")

# 初始化 LLM 过滤器
llm_filter = load_llm_filter()

client = TelegramClient(
    str(SESSION_PATH / str(api_id)),
    api_id,
    api_hash,
    proxy={
        "proxy_type": getattr(socks, proxy_cfg["proxy_type"].upper()),
        "addr": proxy_cfg["addr"],
        "port": proxy_cfg["port"],
        "rdns": proxy_cfg["rdns"],
    } if proxy_cfg else None,
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG if config.get("debug") else logging.INFO
)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ────────────────── 图片下载为 base64 ──────────────────
async def download_images_as_base64(message, current_channel_username: str) -> List[str]:
    imgs: List[str] = []

    async def _dl(m):
        if m.photo:
            try:
                b = await m.download_media(file=bytes)
                imgs.append(base64.b64encode(b).decode())
            except Exception as e:
                logger.error(f"下载图片失败: {e}")

    if message.grouped_id:
        try:
            # 明确从当前消息所在的频道获取
            recent_messages = await client.get_messages(current_channel_username, limit=20)
            grouped = [m for m in recent_messages if m.grouped_id == message.grouped_id]
            for m in grouped:
                await _dl(m)
        except Exception as e:
            logger.error(f"处理相册消息失败: {e}")
    else:
        await _dl(message)

    return imgs

# ────────────────── keep-alive ──────────────────
async def keep_alive():
    """
    每 60 秒做一次极轻量 get_me() —— 如果底层 socket 被掐断，
    Telethon 会立即抛异常并自动重连。
    """
    while True:
        try:
            await client.get_me()
        except Exception as e:
            logger.warning(f"⚠️  keep-alive: connection issue, attempting to reconnect... Details: {e}")
        await asyncio.sleep(60)

# ────────────────── 主逻辑 ──────────────────
async def main():
    logger.info("🚀 机器人启动中...")
    await client.start(phone_number)
    
    # 解析并获取所有频道的实体
    try:
        channel_entities = await asyncio.gather(
            *[client.get_entity(username) for username in channel_usernames]
        )
        logger.info(f"✅ 成功解析 {len(channel_entities)} 个频道.")
    except Exception as e:
        logger.error(f"❌ 解析频道实体失败，请检查 'channel_usernames' 配置: {e}")
        return

    @client.on(events.NewMessage(chats=channel_entities))
    async def handler(event):
        msg = event.message
        channel_peer = msg.peer_id
        
        # 获取当前频道的用户名
        current_channel_entity = next((c for c in channel_entities if isinstance(channel_peer, PeerChannel) and c.id == channel_peer.channel_id), None)
        if not current_channel_entity:
            return 
        current_channel_username = current_channel_entity.username
        
        logger.info(f"📬 从频道 @{current_channel_username} 收到新消息 [ID: {msg.id}]")

        # 跳过投票和空消息
        if getattr(msg, "poll", None) or isinstance(msg.media, MessageMediaPoll) or not (msg.message or msg.photo):
            logger.info(f"➡️ 消息 [ID: {msg.id}] 为投票或空消息，已跳过。")
            return

        # ───── 相册处理：仅在最后一条时汇总文本 + entities ─────
        raw_text = ""
        active_entities = []
        is_album_leader = True

        if msg.grouped_id:
            try:
                recent = await client.get_messages(current_channel_username, limit=20)
                grouped = [m for m in recent if m.grouped_id == msg.grouped_id]

                if msg.id != max(m.id for m in grouped):
                    is_album_leader = False
                    logger.info(f"➡️ 消息 [ID: {msg.id}] 是相册的一部分但非最后一条，等待聚合处理。")
                    return

                logger.info(f"🔄 正在处理相册 [Group ID: {msg.grouped_id}]，共 {len(grouped)} 条消息。")
                grouped.sort(key=lambda m: m.id)
                segments, merged_entities, shift = [], [], 0
                for m in grouped:
                    txt = m.message or ""
                    segments.append(txt)
                    if m.entities:
                        for ent in m.entities:
                            ent_cp = copy.copy(ent)
                            if hasattr(ent_cp, "offset"): ent_cp.offset += shift
                            merged_entities.append(ent_cp)
                    shift += len(txt) + len("\n") if txt else 0
                raw_text, active_entities = "\n".join(segments), merged_entities
            except Exception as e:
                logger.error(f"处理相册 [Group ID: {msg.grouped_id}] 出错: {e}")
                return
        else:
            raw_text = msg.message or ""
            active_entities = msg.entities or []
        
        raw_text = raw_text.strip()
        if not raw_text and not msg.photo:
            logger.info(f"➡️ 消息 [ID: {msg.id}] 文本和图片均为空，已跳过。")
            return

        # 🧠 为AI分析准备"干净"的文本副本，移除URL和特殊字符以规避安全策略
        text_for_llm = re.sub(r'https?://\S+', '', raw_text)
        text_for_llm = re.sub(r'[@#\[\]\(\)\{\}]', '', text_for_llm)

        # 调用AI进行筛选
        should, reason = await asyncio.to_thread(llm_filter.should_forward, text_for_llm)
        if not should:
            logger.info(f"🚫 AI决策：已过滤消息 [ID: {msg.id}]。原因: {reason}")
            return
        
        logger.info(f"✅ AI决策：消息 [ID: {msg.id}] 通过筛选。原因: {reason}")

        # AI决策通过后，为其生成并添加摘要到历史记录
        await asyncio.to_thread(llm_filter.generate_and_add_summary, text_for_llm)

        # 文本格式化与清理 (使用原始raw_text以保证链接格式正确)
        removal_strings = config.get("removal_strings", [])
        raw_text_cleaned_tail = re.sub(r"(\s*\n+\s*\S*\s*\n*\s*)$", "", raw_text).rstrip()
        body, refs = process_markdown_links_and_add_references(
            raw_text_cleaned_tail, entities=active_entities, removal_strings=removal_strings
        )
        text_for_send = f"{body}\n\n{refs}" if refs else body

        # 时间处理
        utc_dt = msg.date.replace(tzinfo=timezone.utc)
        cn_dt  = utc_dt.astimezone(pytz.timezone("Asia/Shanghai"))
        time_str = cn_dt.strftime("%Y-%m-%d %H:%M:%S")

        # 图片处理
        images = await download_images_as_base64(msg, current_channel_username)

        # 推送到 NapCat
        logger.info(f"🚀 准备向NapCat推送消息 [ID: {msg.id}]...")
        await asyncio.to_thread(send_msg_to_group, text_for_send, time_str, images, current_channel_username)

    logger.info(f"✅ 正在监听 {len(channel_entities)} 个频道: {', '.join([f'@{c.username}' for c in channel_entities])}（Ctrl+C 退出）")
    await client.run_until_disconnected()

# ────────────────── 入口 ──────────────────
if __name__ == "__main__":
    try:
        with client:
            client.loop.create_task(keep_alive())
            client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("✅ 程序已手动退出。Bye!")
    except Exception as e:
        logger.critical(f"❌ 程序因意外错误而终止: {e}")
