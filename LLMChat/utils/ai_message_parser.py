import re
import asyncio
import aiohttp
import urllib.parse
import time
from typing import List, Optional, Dict, Any
from napcat.message_types import MessageSegment
from utils.notebook import notebook
from utils.reminder import reminder_manager
from utils.files import load_conversation_history
from utils.music_handler import fetch_music_data

async def parse_ai_message_to_segments(text: str, current_msg_id: Optional[int] = None, chat_id: Optional[str] = None, chat_type: str = "private") -> List[MessageSegment]:
    """
    解析AI输出，将结构化标记转为MessageSegment。
    支持：
      - [reply] 或 [reply:消息ID]：回复消息
      - [@qq:QQ号] 或 [CQ:at,qq=QQ号]：@某人
      - [music:歌曲名] 或 [music:歌曲名-歌手]：自动搜索并发送音乐卡片 (并行处理)
      - [note:内容] 或 [note:内容:context]：静默记录笔记（不会发送任何消息）
        如果带有:context参数，会自动保存最近5条对话作为上下文
      - [reminder:时间戳:原因] 或 [reminder:时间戳:原因:context]：创建定时提醒
        如果带有:context参数，会自动保存最近5条对话作为上下文
      - [poke:QQ号]：群聊中戳一戳某人（仅限群聊）
    其余内容作为text消息段。
    """
    print(f"[Debug] AI Parser: Received raw text: {text}")
    
    # 如果消息只包含[reply]标记，直接返回空列表
    if text.strip() == "[reply]":
        return []
        
    segments_placeholders: List[Optional[MessageSegment]] = []
    pattern = re.compile(
        r"(?P<reply>\[reply(?::(?P<reply_id>\d+))?])"
        r"|(?P<at1>\[@qq:(?P<at_qq1>\d+)])"
        r"|(?P<at2>\[CQ:at,qq=(?P<at_qq2>\d+)])"
        r"|(?P<music>\[music:(?P<music_query>[^\]]+)])"
        r"|(?P<note>\[note:(?P<note_content>[^:\]]+)(?::(?P<note_context>context))?\])"
        r"|(?P<reminder>\[reminder:(?P<reminder_time>\d+):(?P<reminder_reason>[^:\]]+)(?::(?P<reminder_context>context))?\])"
        r"|(?P<poke>\[poke:(?P<poke_qq>\d+)])"
    )

    def get_recent_context(chat_id: str, chat_type: str) -> Optional[str]:
        """获取最近5条对话作为上下文"""
        if not chat_id:
            return None
            
        try:
            history = load_conversation_history(chat_id, chat_type)
            if not history:
                return None
                
            # 过滤掉系统消息，只保留用户和助手的对话
            dialog = [msg for msg in history if msg["role"] != "system"]
            # 获取最后5条消息
            recent = dialog[-5:] if len(dialog) > 5 else dialog
            
            context = []
            for msg in recent:
                role = "用户" if msg["role"] == "user" else "AI"
                content = msg["content"]
                context.append(f"{role}: {content}")
                
            return "\n".join(context)
        except Exception as e:
            print(f"获取对话上下文失败: {e}")
            return None

    # 预处理：移除所有note标记和reminder标记
    cleaned_text = pattern.sub(lambda m: '' if m.group('note') or m.group('reminder') else m.group(0), text)
    
    # 检查是否需要添加reply
    should_reply = False
    reply_id = None
    
    # 查找第一个reply标记
    reply_match = pattern.search(cleaned_text)
    if reply_match and reply_match.group("reply"):
        should_reply = True
        reply_id = reply_match.group("reply_id")
        # 移除reply标记
        cleaned_text = pattern.sub(lambda m: '' if m.group('reply') else m.group(0), cleaned_text)
    
    # 重新获取匹配，这次是在清理后的文本上
    matches = list(pattern.finditer(cleaned_text))
    last_idx = 0
    music_tasks = []
    music_indices = {}

    async with aiohttp.ClientSession() as session:
        for i, m in enumerate(matches):
            if m.start() > last_idx:
                seg_text = cleaned_text[last_idx:m.start()].strip()
                if seg_text:
                    segments_placeholders.append({"type": "text", "data": {"text": seg_text}})

            if m.group("at1"):
                qq = m.group("at_qq1")
                segments_placeholders.append({"type": "at", "data": {"qq": qq}})
            elif m.group("at2"):
                qq = m.group("at_qq2")
                segments_placeholders.append({"type": "at", "data": {"qq": qq}})
            elif m.group("music"):
                query = m.group("music_query").strip()
                if query:
                    placeholder_index = len(segments_placeholders)
                    segments_placeholders.append(None)
                    task_index = len(music_tasks)
                    music_tasks.append(fetch_music_data(session, query))
                    music_indices[task_index] = placeholder_index
                else:
                    segments_placeholders.append({"type": "text", "data": {"text": "[music:] 标签内容为空"}})
            elif m.group("poke"):
                if chat_type != "group":
                    segments_placeholders.append({"type": "text", "data": {"text": "[poke] 标签仅支持在群聊中使用"}})
                else:
                    segments_placeholders.append({"type": "poke", "data": {}})

            last_idx = m.end()

        if last_idx < len(cleaned_text):
            seg_text = cleaned_text[last_idx:].strip()
            if seg_text:
                segments_placeholders.append({"type": "text", "data": {"text": seg_text}})

        # 处理note标记（静默）
        for m in pattern.finditer(text):
            if m.group("note"):
                note_content = m.group("note_content").strip()
                if note_content:
                    context = None
                    if m.group("note_context") == "context" and chat_id:
                        context = get_recent_context(chat_id, chat_type)
                    notebook.add_note(note_content, context)
                    print(f"[Debug] Note added: {note_content}")
            elif m.group("reminder"):
                trigger_time = int(m.group("reminder_time"))
                reason = m.group("reminder_reason").strip()
                if trigger_time and reason and chat_id:
                    context = None
                    if m.group("reminder_context") == "context":
                        context = get_recent_context(chat_id, chat_type)
                    if reminder_manager.add_reminder(trigger_time, reason, chat_id, chat_type, context):
                        print(f"[Debug] Reminder added: {reason} at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(trigger_time))}")

        if music_tasks:
            music_results = await asyncio.gather(*music_tasks, return_exceptions=True)
            for i, result in enumerate(music_results):
                placeholder_index = music_indices[i]
                if isinstance(result, Exception):
                    print(f"音乐任务执行时捕获异常: {result}")
                    segments_placeholders[placeholder_index] = {"type": "text", "data": {"text": "处理音乐请求时发生内部错误，请上报管理员喵"}}
                else:
                    segments_placeholders[placeholder_index] = result

    final_segments: List[MessageSegment] = [seg for seg in segments_placeholders if seg is not None]
    
    # 如果有实际内容且需要回复，添加reply segment
    if final_segments and should_reply:
        reply_segment: MessageSegment = {"type": "reply", "data": {}}
        if reply_id:
            reply_segment["data"]["id"] = int(reply_id)
        elif current_msg_id is not None:
            reply_segment["data"]["id"] = int(current_msg_id)
        final_segments.insert(0, reply_segment)
    
    # 如果没有任何内容，返回空列表
    if not final_segments:
        return []

    return final_segments