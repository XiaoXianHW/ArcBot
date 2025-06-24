import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

import jwt
import requests

SCRIPT_DIR = Path(__file__).parent.resolve()
CONFIG_PATH = SCRIPT_DIR / "config.json"
HISTORY_PATH = SCRIPT_DIR / "forward_history.json"


class LLMFilter:
    def __init__(self, config: Dict[str, Any]):
        self.llm_filter_config = config.get("llm_filter", {})
        if not self.llm_filter_config or not self.llm_filter_config.get("enabled"):
            self.enabled = False
            return

        self.enabled = True
        self.llm_configs = config.get("llm_configs", {})
        self.task_mapping = config.get("task_model_mapping", {})
        self.base_prompt = self.llm_filter_config.get("base_prompt")
        self.user_like_prompt = self.llm_filter_config.get("user_like_prompt", "")
        self.user_dislike_prompt = self.llm_filter_config.get("user_dislike_prompt", "")
        self.deduplication_window_hours = self.llm_filter_config.get("deduplication_window_hours", 24)

        self._initialize_history()

    def _initialize_history(self):
        """如果历史文件不存在，则创建一个空的列表文件。"""
        if not HISTORY_PATH.exists():
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump([], f)

    def _load_and_clean_history(self) -> List[Dict[str, Any]]:
        """加载历史记录并移除超出时间窗口的条目。"""
        if not HISTORY_PATH.exists():
            return []

        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            try:
                history = json.load(f)
            except json.JSONDecodeError:
                history = []

        now = datetime.now()
        window = timedelta(hours=self.deduplication_window_hours)
        
        fresh_history = [
            item for item in history 
            if now - datetime.fromtimestamp(item.get("timestamp", 0)) <= window
        ]

        if len(fresh_history) < len(history):
            self._save_history(fresh_history)
            
        return fresh_history
    
    def _save_history(self, history: List[Dict[str, Any]]):
        """将历史记录保存到文件。"""
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _generate_zhipu_token(self, api_key: str) -> str:
        """生成JWT"""
        try:
            id, secret = api_key.split('.')
        except ValueError:
            raise ValueError("Invalid ZhipuAI API Key format.")

        payload = {
            "api_key": id,
            "exp": int(time.time()) + 3600,  # 1 hour expiration
            "timestamp": int(time.time()),
        }
        return jwt.encode(payload, secret, algorithm="HS256", headers={"alg": "HS256", "sign_type": "SIGN"})

    def _call_llm(self, task_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """调用LLM API并返回JSON结果。"""
        model_key = self.task_mapping.get(task_name)
        if not model_key: return {"error": f"Task '{task_name}' not found in task_model_mapping."}
        
        model_config = self.llm_configs.get(model_key)
        if not model_config: return {"error": f"Model config '{model_key}' not found."}

        provider = model_config.get("provider")
        api_key = model_config.get("api_key")
        endpoint = f"{model_config.get('api_base')}/chat/completions"
        
        headers = {"Content-Type": "application/json"}
        if provider == "zhipuai":
            headers["Authorization"] = f"Bearer {self._generate_zhipu_token(api_key)}"
        else: # OpenAI or compatible
            headers["Authorization"] = f"Bearer {api_key}"
        
        # 将模型名称注入到payload中
        payload["model"] = model_config.get("model")

        try:
            response = requests.post(
                endpoint, 
                headers=headers, 
                json=payload,
                timeout=90
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"LLM API request for task '{task_name}' failed: {e}")
            if e.response is not None:
                logging.error(f"LLM Response Body: {e.response.text}")
                return {"error": str(e), "details": e.response.text}
            return {"error": str(e)}

    def should_forward(self, message_text: str) -> Tuple[bool, str]:
        """
        核心决策函数：判断消息是否应被转发。
        返回 (是否转发, 原因)。
        """
        if not self.enabled:
            return True, "AI filter is disabled."

        history = self._load_and_clean_history()
        recent_summaries = [item.get("summary", "") for item in history]
        
        summary_block = "【重要参考】这是过去24小时内已转发的新闻摘要，请避免转发任何与以下内容高度相似或重复的新闻：\n"
        if recent_summaries:
            summary_block += "\n".join(f"- {s}" for s in recent_summaries)
        else:
            summary_block += "无"

        prompt_parts = [
            self.base_prompt,
            f"用户偏好：\n- 喜欢：{self.user_like_prompt}" if self.user_like_prompt else "",
            f"- 厌恶：{self.user_dislike_prompt}" if self.user_dislike_prompt else "",
            summary_block,
            f"【待审议新闻】请分析以下这条新消息：\n---\n{message_text}\n---",
            "【你的任务】综合以上所有信息，判断是否应该转发【待审议新闻】。你的回答必须是一个JSON对象，且仅包含两个键：\n1. 'decision': string类型，值必须是 'yes' 或 'no'。\n2. 'reason': string类型，用中文简要说明你做出该决策的原因。"
        ]
        
        final_prompt = "\n\n".join(filter(None, prompt_parts))

        payload = {
            "messages": [{"role": "user", "content": final_prompt}],
            "temperature": 0.5,
            "response_format": {"type": "json_object"},
        }
        
        llm_response = self._call_llm("filtering", payload)
        
        if "error" in llm_response:
            return False, f"AI API call failed: {llm_response.get('details', llm_response['error'])}"
            
        try:
            # 智谱AI和OpenAI的响应结构在此处兼容
            choice = llm_response.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "{}")
            decision_data = json.loads(content)
            
            decision = decision_data.get("decision")
            reason = decision_data.get("reason", "No reason provided.")

            if decision == "yes":
                return True, reason
            else:
                return False, reason
        except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
            logging.error(f"Failed to parse LLM response for filtering: {llm_response}, error: {e}")
            return False, "AI response parsing failed."

    def generate_and_add_summary(self, message_text: str):
        """为通过筛选的消息生成摘要并添加到历史记录中。"""
        if not self.enabled:
            return

        prompt = f"请将以下科技新闻浓缩为一句25字以内的中文摘要，只保留核心事件与主题，用于后续的重复内容检查。\n\n原文：\n---\n{message_text}\n---\n\n摘要："
        
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        
        try:
            response = self._call_llm("summarization", payload)
            summary_text = response["choices"][0]["message"]["content"].strip()

            if summary_text:
                history = self._load_and_clean_history()
                history.append({
                    "timestamp": time.time(),
                    "summary": summary_text,
                    "original_text": message_text
                })
                self._save_history(history)
                logging.info(f"New summary added to history: {summary_text}")

        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, IndexError) as e:
            logging.error(f"Failed to generate or save summary: {e}")

def load_llm_filter() -> LLMFilter:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)
    return LLMFilter(config) 