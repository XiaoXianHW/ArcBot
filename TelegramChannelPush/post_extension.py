import json
import random
import time
from pathlib import Path
from typing import List, Sequence, Union

import requests

# 路径自适应
SCRIPT_DIR = Path(__file__).parent.resolve()

# ────────────────── 读取配置 ──────────────────
def load_config(path: str = "config.json"):
    config_path = SCRIPT_DIR / path
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()
post_url: str = config.get("napcat_url", "")
post_token: str = config.get("napcat_token", "")
post_group_ids: List[str] = config.get("napcat_group_ids", [])


# ────────────────── 工具函数 ──────────────────
def _normalize_text(text: Union[str, Sequence[str]]) -> str:
    """tuple / list / str → 单行字符串。"""
    if isinstance(text, (list, tuple)):
        text = "\n\n".join(t for t in text if t)
    return text or ""


# ────────────────── 主发送函数 ──────────────────
def send_msg_to_group(text, time_str: str, base64_list: List[str], source_channel: str):
    """
    将整理后的 Telegram 内容推送到 NapCat（QQ 机器人）。
    text 可以是 str，或 (正文, 引用) 的 tuple/list。
    """
    text = _normalize_text(text)
    if text and not text.endswith("\n"):
        text += "\n"

    # 构造消息段
    images_part = [
        {
            "type": "image",
            "data": {"file": f"data:image/jpeg;base64,{b64}"},
        }
        for b64 in base64_list
    ]

    text_part = {
        "type": "text",
        "data": {
            "text": (
                f"{text}\n"
                f"发布时间: {time_str}\n"
                f"信息来源: @{source_channel}\n"
                f"Repost By ArcBot"
            )
        },
    }

    message_list = images_part + [text_part]
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {post_token}",
    }

    # 依次推送到多个群
    for gid in post_group_ids:
        body = {"group_id": gid, "message": message_list}
        try:
            r = requests.post(post_url, headers=headers, json=body)
            r.raise_for_status()
            print(f"已向群 {gid} 发送，状态 {r.status_code}，返回 {r.text}")
            time.sleep(random.uniform(1.0, 3.0))  # 简单限速
        except Exception as exc:
            print(f"发送到群 {gid} 失败：{exc}")
