import json
import base64
import asyncio
import socks
import os
from datetime import timezone
import pytz
import re
from telethon import TelegramClient, events

from post_extension import send_msg_to_group
from text_formatter import process_markdown_links_and_add_references

def load_config(config_path='config.json'):
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

config = load_config()

api_id = config["api_id"]
api_hash = config["api_hash"]
phone_number = config.get("phone_number", "")
channel_username = config["channel_username"]
proxy_config = config["proxy"]

# 为 Telethon 创建会话目录
sessions_dir = 'sessions'
if not os.path.exists(sessions_dir):
    os.makedirs(sessions_dir)

client = TelegramClient(
    os.path.join(sessions_dir, str(api_id)),
    api_id,
    api_hash,
    proxy={
        'proxy_type': getattr(socks, proxy_config['proxy_type'].upper()),
        'addr': proxy_config['addr'],
        'port': proxy_config['port'],
        'rdns': proxy_config['rdns']
    }
)

async def download_images_as_base64(message):
    base64_list = []
    if message.grouped_id:
        # 如果是相册，多张图共用同一个 grouped_id
        recent_msgs = await client.get_messages(channel_username, limit=20)
        grouped_msgs = [m for m in recent_msgs if m.grouped_id == message.grouped_id]
        for m in grouped_msgs:
            if m.photo:
                image_bytes = await m.download_media(file=bytes)
                b64_str = base64.b64encode(image_bytes).decode('utf-8')
                base64_list.append(b64_str)
    elif message.photo:
        image_bytes = await message.download_media(file=bytes)
        b64_str = base64.b64encode(image_bytes).decode('utf-8')
        base64_list.append(b64_str)
    return base64_list

async def main():
    print("🚀 正在启动 Telegram 频道监听...")
    await client.start(phone_number)
    channel = await client.get_entity(channel_username)

    @client.on(events.NewMessage(chats=channel))
    async def handler(event):
        msg = event.message
        # 优先使用 event.message.text，如果为空，则使用 event.message.message
        text = msg.text if msg.text else (msg.message or "")

        # 立即清理指定的屏蔽词
        removal_strings = config.get("removal_strings", [])
        for r in removal_strings:
            text = text.replace(r, "")

        if config.get("debug"):
            print(f"收到消息：{msg}")

        # 如果是相册消息，需要判断是否是该相册最后一条；若不是，则跳过防止重复
        if msg.grouped_id:
            recent_msgs = await client.get_messages(channel_username, limit=20)
            grouped_msgs = [m for m in recent_msgs if m.grouped_id == msg.grouped_id]
            # 如果当前消息不是这组相册的最大 ID，则不是最后一条，直接 return
            if msg.id != max(m.id for m in grouped_msgs):
                return
            # 取相册内所有消息的文本合并
            group_texts = [m.message for m in grouped_msgs if m.message]
            merged_text = "\n".join(group_texts) if group_texts else ""
            # 再次对合并后的文本进行清理
            for r in removal_strings:
                merged_text = merged_text.replace(r, "")
            text = merged_text
        
        # 清理文本主体和首尾空白
        text = re.sub(r'(\s*\n){3,}', '\n\n', text) # 移除超过2个的连续换行
        text = text.strip()
        
        # 处理Markdown链接并生成引用
        processed_text, references_string = process_markdown_links_and_add_references(text)
        if references_string:
            text = processed_text + "\n\n" + references_string
        else:
            text = processed_text

        # 移除常见的Markdown标记
        # 移除加粗 (**)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        # 移除斜体 (__)
        text = re.sub(r'__(.*?)__', r'\1', text)
        # 移除删除线 (~~)
        text = re.sub(r'~~(.*?)~~', r'\1', text)
        # 移除行内代码 (`)
        text = re.sub(r'`(.*?)`', r'\1', text)

        utc_time = msg.date.replace(tzinfo=timezone.utc)
        china_time = utc_time.astimezone(pytz.timezone("Asia/Shanghai"))
        time_str = china_time.strftime('%Y-%m-%d %H:%M:%S')

        images = await download_images_as_base64(msg)
        if config.get("debug"):
            result = {
                "text": text,
                "time": time_str,
                "images": [img[:60] + '...' for img in images]
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))

        # 同步请求封装到 asyncio.to_thread
        await asyncio.to_thread(send_msg_to_group, text, time_str, images)

    print(f"✅ 正在监听频道：{channel.title}（按 Ctrl+C 退出）")
    await client.run_until_disconnected()

try:
    with client:
        client.loop.run_until_complete(main())
except KeyboardInterrupt:
    print("✅ Bye!")
