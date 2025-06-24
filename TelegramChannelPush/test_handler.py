import asyncio
import re
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock
from typing import Tuple, Dict, Any
from pathlib import Path

from llm_handler import load_llm_filter
from post_extension import load_config
from text_formatter import process_markdown_links_and_add_references

# --- 配置 ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 测试用例集 ---
# 从 debug_prompt.txt 中挑选出覆盖多种场景的测试消息
TEST_CASES = [
    {
        "name": "1. 高质量硬核科技新闻 (应通过)",
        "text": """**三星电子推进 2nm 工艺计划**

三星电子正在加速推进其 2nm 工艺的发展。根据韩媒报道，三星计划于 2026 年初在美国得克萨斯州泰勒晶圆厂引入 2nm 量产线所需设备。该厂原计划实施 4nm 工艺，但因市场状况调整为 2nm。此前，项目因制程成熟度不足多次推迟，但泰勒厂的洁净室装修已于今年二季度重启，并预计年底竣工。

与此同时，三星电子推迟了在平泽二号晶圆厂建设 1.4nm 测试线的计划，预计延至今年底或明年初，1.4nm 量产可能推迟至 2028 年。三星此举旨在集中资源以确保 2nm 工艺的顺利量产。

IT之家""",
        "expected": "pass"
    },
    {
        "name": "2. 无关的政治新闻 (应过滤)",
        "text": """**以色列与伊朗达成全面停火协议**

以色列总理内塔anyahu于当地时间 6 月 24 日宣布，与伊朗达成全面停火协议。声明称，此举是在密集外交和安全努力后取得的成果，协议将在未来数小时内逐步生效。""",
        "expected": "filter"
    },
    {
        "name": "3. 带有页脚的开源项目新闻 (应通过并清理)",
        "text": """**开源项目**

开源微型事件库，函数式、带类型、直接调用回掉函数即可无参数解绑，配合 react useEffect 使用更顺手。

Github

__via 御坂云见17535__

💡 **本频道仅作项目分享，风险自控**

📮分享投稿  ☘️频道  🍵茶馆""",
        "expected": "pass"
    },
    {
        "name": "4. 无关领域的安全新闻 (应过滤)",
        "text": """**研究称 160 亿记录泄露或为旧数据汇编**

近期报告的 160 亿条记录大规模数据泄露事件，其真实情况可能并不如最初设想的严重。据网络安全媒体 BleepingComputer 的研究，目前没有证据表明这批数据为全新或未曾公开的内容，该事件更可能是一次对过往已泄露凭证的汇编，而非一次新的攻击。""",
        "expected": "filter"
    },
    {
        "name": "5. 重复消息 (应因重复而过滤 - 此用例前提是上一条通过，但现在上一条被过滤，所以它会因为内容本身被过滤)",
        "text": """**研究称 160 亿记录泄露或为旧数据汇编**

近期报告的 160 亿条记录大规模数据泄露事件，其真实情况可能并不如最初设想的严重。据网络安全媒体 BleepingComputer 的研究，目前没有证据表明这批数据为全新或未曾公开的内容，该事件更可能是一次对过往已泄露凭证的汇编，而非一次新的攻击。""",
        "expected": "filter"
    },
    {
        "name": "6. 低价值商业新闻 (应过滤)",
        "text": """**高瓴资本拟收购星巴克中国业务**

高瓴资本近期表达了收购星巴克中国业务的意向。凯雷投资、信宸资本等多家机构亦有参与。星巴克中国业务估值约 50 至 60 亿美元，预计谈判将持续至 2026 年。""",
        "expected": "filter"
    }
]


async def simulate_message_handler(mock_msg: MagicMock, llm_filter: Any, config: Dict[str, Any]) -> Tuple[str, str]:
    """
    模拟处理单条消息，返回一个包含处理状态和结果的元组。
    :return: (状态, 结果) -> ('pass', 格式化后的文本) 或 ('filter', 过滤原因)
    """
    raw_text = mock_msg.message or ""
    active_entities = mock_msg.entities or []

    if not raw_text.strip():
        return "filter", "消息为空"

    text_for_llm = re.sub(r'https?://\S+', '', raw_text)
    text_for_llm = re.sub(r'[@#\[\]\(\)\{\}]', '', text_for_llm)
    text_for_llm = re.sub(r'\*|\_', '', text_for_llm)

    should, reason = await asyncio.to_thread(llm_filter.should_forward, text_for_llm)
    if not should:
        return "filter", reason

    await asyncio.to_thread(llm_filter.generate_and_add_summary, text_for_llm)

    removal_strings = config.get("removal_strings", [])
    raw_text_cleaned_tail = re.sub(r"(\s*\n+\s*\S*\s*\n*\s*)$", "", raw_text).rstrip()
    body, refs = process_markdown_links_and_add_references(
        raw_text_cleaned_tail, entities=active_entities, removal_strings=removal_strings
    )
    text_for_send = f"{body}\n\n{refs}" if refs else body
    
    return "pass", text_for_send


async def run_test_suite():
    """
    运行完整的测试用例集。
    """
    logger.info("🚀 正在初始化测试环境...")
    
    config = load_config()

    # --- 修正: 直接构造并删除历史文件路径 ---
    # 为了确保测试的独立性，直接定位并删除历史记录文件
    script_dir = Path(__file__).parent.resolve()
    history_file_path = script_dir / "forward_history.json"
    history_file_path.unlink(missing_ok=True) 
    logger.info("🧹 已清空旧的转发历史记录，确保测试环境纯净。")
    # --- 修正结束 ---

    llm_filter = load_llm_filter()

    if not llm_filter.enabled:
        logger.error("❌ LLM过滤器在 config.json 中被禁用，无法运行测试。")
        return
    
    logger.info("👍 测试环境准备就绪，开始执行测试套件...")
    print("\n" + "="*70)
    
    passed_count = 0
    for i, case in enumerate(TEST_CASES):
        case_name = case["name"]
        case_text = case["text"]
        expected_result = case["expected"]
        
        logger.info(f"▶️  正在执行: {case_name}")

        mock_message = MagicMock()
        mock_message.id = 1000 + i
        mock_message.message = case_text
        mock_message.entities = []

        actual_result, reason_or_text = await simulate_message_handler(mock_message, llm_filter, config)
        
        if actual_result == expected_result:
            passed_count += 1
            logger.info(f"✅ [通过] - 结果符合预期 ({actual_result})")
            if actual_result == "pass":
                 print(f"    最终文本:\n---\n{reason_or_text}\n---")
            else:
                 print(f"    过滤原因: {reason_or_text}")
        else:
            logger.error(f"❌ [失败] - 预期结果: '{expected_result}', 实际结果: '{actual_result}'")
            print(f"    详情: {reason_or_text}")
        
        print("\n" + "="*70)

    logger.info("🏁 测试套件执行完毕")
    if passed_count == len(TEST_CASES):
        logger.info(f"🎉🎉🎉 全部 {len(TEST_CASES)} 个测试用例均已通过！ 🎉🎉🎉")
    else:
        logger.warning(f"⚠️  测试完成，通过: {passed_count}/{len(TEST_CASES)}，失败: {len(TEST_CASES)-passed_count}/{len(TEST_CASES)}")


if __name__ == "__main__":
    try:
        asyncio.run(run_test_suite())
    except Exception as e:
        logger.critical(f"❌ 测试运行时发生意外错误: {e}", exc_info=True) 