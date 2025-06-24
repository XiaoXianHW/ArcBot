import asyncio
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
import re

import socks
from telethon import TelegramClient

from llm_handler import LLMFilter

# ------------------- 配置 -------------------
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
SESSION_PATH = SCRIPT_DIR / "sessions"

# ------------------- 主逻辑 -------------------
async def generate_prompts():
    """交互式地为用户生成基础指令和偏好提示。"""
    logger.info("🚀 Starting Intelligent Prompt Generator...")
    
    # 1. 加载配置
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"❌ 配置文件 'config.json' 未找到。")
        return

    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    phone_number = config.get("phone_number")
    channel_usernames = config.get("channel_usernames", [])
    proxy_cfg = config.get("proxy")

    if not all([api_id, api_hash, phone_number, channel_usernames]):
        logger.error("❌ 配置文件不完整，缺少 api_id, api_hash, phone_number 或 channel_usernames。")
        return

    llm_filter = LLMFilter(config)
    if not llm_filter.enabled:
        logger.error("❌ LLM filter 在 config.json 中被禁用，无法进行AI分析。")
        return

    # 2. 连接 Telegram
    SESSION_PATH.mkdir(exist_ok=True)  # 自动创建 sessions 目录
    client = TelegramClient(
        str(SESSION_PATH / str(api_id)), api_id, api_hash,
        proxy={
            "proxy_type": getattr(socks, proxy_cfg["proxy_type"].upper()),
            "addr": proxy_cfg["addr"],
            "port": proxy_cfg["port"],
            "rdns": proxy_cfg["rdns"],
        } if proxy_cfg else None,
    )
    
    all_messages_text_parts = []
    total_limit = 20000
    limit_per_channel = total_limit // len(channel_usernames) if channel_usernames else total_limit

    async with client:
        logger.info(f"正在连接 Telegram 并从 {len(channel_usernames)} 个频道获取最近的消息...")
        for channel_username in channel_usernames:
            channel_text = ""
            try:
                logger.info(f"  -> 正在从 @{channel_username} 获取消息...")
                messages = await client.get_messages(channel_username, limit=100)
                for msg in messages:
                    if msg and msg.text:
                        clean_text = re.sub(r'https?://\S+', '', msg.text)
                        clean_text = re.sub(r'[@#\[\]\(\)\{\}]', '', clean_text)
                        channel_text += clean_text + "\n\n"
                
                # 对每个频道的文本独立进行截断
                all_messages_text_parts.append(channel_text[:limit_per_channel])

            except Exception as e:
                logger.error(f"  -> 从 @{channel_username} 获取消息失败: {e}")
                continue
    
    all_messages_text = "".join(all_messages_text_parts)

    if not all_messages_text.strip():
        logger.error("❌ 未能从任何频道获取到有效文本消息，无法进行分析。")
        return

    logger.info("✅ 消息获取完毕，正在请求AI进行深度主题分析（这可能需要一些时间）...")

    # 3. AI 深度主题分析 (专家级Prompt v2)
    analysis_prompt = (
        "你是一位顶级的科技新闻分析师，任务是为一位挑剔的主编制定内容筛选规则。请分析以下新闻文本集合。\n\n"
        "第一步：思考过程 (Chain of Thought)\n"
        "请先在脑海中（不需要在最终输出中展示）思考并识别出文本中的主要内容模式。哪些是反复出现的硬核技术话题？哪些是低价值的营销信息或快讯？哪些是边缘话题？\n\n"
        "第二步：提取核心主题\n"
        "1.  **正面主题 (Positive Themes)**: 识别并列出 5-7个最核心的、具有深度和价值的**硬核技术主题**。例如：`AI算法突破`, `操作系统内核更新`, `新型芯片架构`, `开源项目重大发布`。\n"
        "2.  **负面主题 (Negative Themes)**: 识别并列出 5-7个应该被**主动过滤**的低价值或无关主题。例如：`公司常规财报`, `高管人事变动`, `产品市场推广`, `无实质内容的大会预告`。\n\n"
        "一些提示。比如一个好的规则会像这样：\"筛选标准：内容应聚焦于AI、芯片、操作系统、开源项目、编程语言、网络安全等硬核技术领域。\" (注意: 最终的核心指令只会包含正面主题，负面主题仅用于后续的用户偏好选择)"
        "第三步：归纳与泛化 (Summarize and Generalize)\n"
        "现在，回顾你在第二步中列出的【正面主题】和【负面主题】。将它们分别归纳和提炼成 **3-5个更宏观、更具代表性的上位概念**。例如，如果具体主题是'AI算法突破'和'AI硬件发布'，你应该将它们归纳为'人工智能'。目标是生成一个既能覆盖当前内容，又对未来新话题有包容性的分类。\n\n"
        "**【重要禁令】**: 在最终输出的JSON中，`generalized_positive` 和 `generalized_negative` 的值必须是你**自己归纳总结出的宏观类别**，禁止直接使用第二步中识别出的具体主题，也禁止复述我在示例中给出的任何词汇。\n\n"
        "第四步：输出结果\n"
        "你的最终回答**必须**是一个JSON对象，严格遵循以下格式，不要添加任何额外的解释：\n"
        "```json\n"
        "{\n"
        "  \"generalized_positive\": [\"宏观主题1\", \"宏观主题2\", ...],\n"
        "  \"generalized_negative\": [\"宏观主题A\", \"宏观主题B\", ...]\n"
        "}\n"
        "```"
        f"\n\n【待分析的文本】:\n---\n{all_messages_text}\n---"
    )
    
    analysis_payload = {
        "messages": [{"role": "user", "content": analysis_prompt}],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }

    if config.get("debug"):
        logger.info("--- DEBUG: Full analysis prompt being sent to AI ---")
        # 将完整的prompt写入一个文件，方便查看
        debug_prompt_path = SCRIPT_DIR / "debug_prompt.txt"
        with open(debug_prompt_path, "w", encoding="utf-8") as f:
            f.write(analysis_prompt)
        logger.info(f"完整的分析指令已保存到: {debug_prompt_path}")
        logger.info("--- END DEBUG ---")

    response_data = {}
    try:
        # 明确指定任务类型为 "analysis_and_refinement"
        response = llm_filter._call_llm("analysis_and_refinement", analysis_payload)
        content = response["choices"][0]["message"]["content"]
        response_data = json.loads(content)
        # 更新键以匹配新的Prompt
        positive_themes = response_data.get("generalized_positive", [])
        negative_themes = response_data.get("generalized_negative", [])
    except (Exception, KeyError, IndexError, json.JSONDecodeError) as e:
        logger.error(f"❌ AI深度分析失败: {e}\n   LLM原始返回: {response.get('error', response)}")
        return

    if not positive_themes:
        logger.error("❌ AI未能分析出有效的主题。")
        return

    # 4. 生成并确认 Base Prompt (V2 - 更灵活的指令)
    base_prompt_template = (
        "你是一位专业的科技新闻编辑，负责筛选高质量、信息密度大的前沿技术新闻进行转发。"
        "你的核心任务是识别并转发与以下领域高度相关的、具有深度价值的硬核技术新闻：{positive}。"
        "对于不在此列表但你判断同样具有高技术价值的新闻，也应考虑转发。"
        "你需要参考最近已转发的新闻摘要，如果新消息与它们内容高度相似或重复，则判定为重复，不予转发。"
    )
    
    new_base_prompt = base_prompt_template.format(
        positive='、'.join(positive_themes)
    )

    logger.info("\n" + "="*20 + " 步骤 1/2: 核心指令确认 " + "="*20)
    logger.info("AI根据您的频道内容，为您自动生成了以下核心指令 (base_prompt):")
    
    current_base_prompt = new_base_prompt

    while True:
        print(f"\n【当前版本】:\n{current_base_prompt}\n")
        confirm_base = input("❔ 是否接受此核心指令？(y/n/e/d - 接受/拒绝/编辑/与AI对话微调): ").lower()
        
        if confirm_base == 'y':
            config["llm_filter"]["base_prompt"] = current_base_prompt
            logger.info("✅ 核心指令已更新。")
            break
        elif confirm_base == 'n':
            logger.info("ℹ️ 已保留旧的核心指令。")
            break
        elif confirm_base == 'e':
            logger.info("✍️ 请粘贴您的新版核心指令，然后按两次回车键结束输入:")
            edited_prompt = []
            while True:
                line = input()
                if not line: break
                edited_prompt.append(line)
            current_base_prompt = "\n".join(edited_prompt)
            config["llm_filter"]["base_prompt"] = current_base_prompt
            logger.info("✅ 核心指令已通过手动编辑更新。")
            break
        elif confirm_base == 'd':
            feedback = input("✍️ 请输入您的修改意见: ")
            if not feedback:
                continue

            logger.info("🤖 正在请求AI根据您的意见进行微调...")

            positive_match = re.search(r'硬核技术新闻：(.*?)\。', current_base_prompt)
            
            current_pos_list = positive_match.group(1).split('、') if positive_match else []

            refine_prompt = (
                "你是一位精准的指令编辑器，正在帮助一位主编优化内容筛选规则。这项规则由一个【核心主题列表】（应该优先关注的内容）构成。\n\n"
                f"【当前核心主题列表】:\n{json.dumps(current_pos_list, ensure_ascii=False)}\n\n"
                f"【主编的修改意见】:\n{feedback}\n\n"
                "【你的任务】:\n"
                "1.  **分析意见**: 仔细分析【主编的修改意见】，对【核心主题列表】进行增、删、改操作。\n"
                "2.  **重新生成**: 使用修改后的新列表，严格按照以下模板重新生成一个**全新的、完整**的规则文本：\n"
                "   `你是一位专业的科技新闻编辑...你的核心任务是识别并转发与以下领域高度相关的、具有深度价值的硬核技术新闻：<新主题列表>。对于不在此列表但你判断同样具有高技术价值的新闻，也应考虑转发。你需要参考...重复，不予转发。`\n"
                "3.  **直接输出**: 不要有多余的解释或客套话，只返回优化后的规则全文。"
            )
            
            refine_payload = {
                "messages": [{"role": "user", "content": refine_prompt}],
                "temperature": 0.3, # Lower temperature for precise editing
            }
            
            try:
                # Use the designated model for this task
                response = llm_filter._call_llm("analysis_and_refinement", refine_payload)
                refined_text = response["choices"][0]["message"]["content"].strip()
                # Clean up potential markdown code blocks from the response
                current_base_prompt = re.sub(r'```(json)?', '', refined_text).strip()

            except (Exception, KeyError, IndexError):
                logger.error("❌ AI微调失败，请重试。")
        else:
            logger.warning("  -> 输入无效，请输入 y, n, e, 或 d。")

    # 5. 生成并确认用户偏好
    logger.info("\n" + "="*20 + " 步骤 2/2: 用户偏好选择 " + "="*20)
    logger.info("现在，请从AI归纳出的宏观主题中，选择您个人特别喜欢或不喜欢的类别。")
    for i, cat in enumerate(positive_themes, 1):
        print(f"  {i}: {cat}")
    
    def get_user_choices(prompt_text: str) -> List[str]:
        while True:
            try:
                raw_input = input(prompt_text)
                if not raw_input: return []
                indices = [int(i.strip()) for i in raw_input.split(',')]
                return [positive_themes[i-1] for i in indices if 0 < i <= len(positive_themes)]
            except (ValueError, IndexError):
                logger.warning("  -> 输入无效，请输入正确的数字编号，并用逗号隔开。")

    liked_cats = get_user_choices("\n👉 请输入您喜欢的类别编号（多个用逗号隔开，可跳过）: ")
    disliked_cats = get_user_choices("👉 请输入您不喜欢的类别编号（多个用逗号隔开，可跳过）: ")

    like_prompt = f"我特别喜欢关于{ '、'.join(liked_cats) }的新闻。" if liked_cats else ""
    dislike_prompt = f"我不喜欢关于{ '、'.join(disliked_cats) }的新闻。" if disliked_cats else ""
    config["llm_filter"]["user_like_prompt"] = like_prompt
    config["llm_filter"]["user_dislike_prompt"] = dislike_prompt

    logger.info("\n✅ 已根据您的选择生成个人偏好：")
    print(f"  👍 喜欢: {like_prompt if like_prompt else '无'}")
    print(f"  👎 不喜欢: {dislike_prompt if dislike_prompt else '无'}")
    
    # 6. 保存最终配置
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    logger.info("\n🎉 全部配置已成功更新到 config.json！现在您可以运行 main.py 了。")


if __name__ == "__main__":
    asyncio.run(generate_prompts())
