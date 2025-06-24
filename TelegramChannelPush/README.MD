# ArcBot - 智能 Telegram 频道聚合与分发机器人 ️🤖

> 一个基于 [Telethon](https://github.com/LonamiWebs/Telethon) + [NapcatQQ](https://github.com/NapNeko/NapCatQQ) 的 Python 脚本，现已进化为具备 **LLM 智能过滤**、**多模型任务分配** 和 **交互式指令生成** 的高级内容聚合工具。

## ✨ 核心功能 (Core Features)

- **多频道同时监听**: 可在 `config.json` 中配置任意数量的 Telegram 频道进行监控。
- **🤖 LLM 智能筛选**:
    - **精准过滤**: 使用大语言模型（如 DeepSeek, ZhipuAI 等）根据您设定的核心指令和个人偏好，自动筛选有价值的新闻，过滤低质量内容。
    - **24小时去重**: AI会记录并比对24小时内已转发的消息摘要，有效避免在不同频道推送重复或高度相似的内容。
    - **多模型/多任务架构**: 可为不同任务（如初步过滤、内容摘要、指令优化）分配不同的AI模型，实现成本与效果的最佳平衡。
- **🚀 交互式指令生成器**:
    - 无需手写复杂的Prompt！运行 `prompt_generator.py`，它会读取频道历史消息，通过AI分析并与您问答，自动生成高质量的 `base_prompt` 和用户偏好配置。
    - 支持对话式微调，让您用自然语言即可优化筛选规则。
- **健壮的消息处理**:
    - **相册聚合**: 自动将相册（grouped messages）聚合成一条消息发送，避免刷屏。
    - **格式化与清理**: 自动将消息中的链接转换为参考来源格式，并可配置移除文本中的多余内容（如频道页脚）。
- **完善的辅助工具**:
    - **单元测试**: 提供 `test_handler.py` 脚本，用于在不真实发送消息的情况下，模拟和验证AI过滤和文本处理逻辑。
- **高可配置性**:
    - **代理支持**: 可通过 `config.json` 配置 SOCKS5/HTTP 等代理。
    - **多群分发**: 支持将筛选后的消息推送到多个QQ群。


## 📁 文件结构

```
.
├── main.py               # 主程序，负责监听TG频道并调用处理器
├── llm_handler.py        # 核心：处理所有与大语言模型(LLM)相关的交互、任务分发和历史记录
├── prompt_generator.py   # 辅助工具：交互式生成和优化AI筛选指令
├── test_handler.py       # 辅助工具：用于测试AI过滤和文本格式化逻辑
├── post_extension.py     # 扩展模块，负责将格式化后的消息通过HTTP POST推送到Napcat
├── text_formatter.py     # 文本处理模块，负责链接格式化和内容清理
└── config.json           # 全局配置文件
```

## 🚀 安装 & 部署

1.  **克隆本仓库**
    ```bash
    git clone https://github.com/your-repo/ArcBot.git # 请替换为您的仓库地址
    cd ArcBot/TelegramChannelPush
    ```
2.  **安装依赖**
    建议使用 Python 3.9+
    ```bash
    pip install -r requirements.txt
    ```
3.  **配置 `config.json`**
    这是现在的核心。请仔细按需填写：

    ```jsonc
    {
      "api_id": "你的TG-API ID",
      "api_hash": "你的TG-API hash",
      "phone_number": "你的手机号（含+区号）",
      "channel_usernames": [
        "channel1_to_listen",
        "channel2_to_listen"
      ],
      "debug": false,
      "proxy": {
        "proxy_type": "socks5",
        "addr": "localhost",
        "port": 7890,
        "rdns": true
      },
      "napcat_url": "http://ip:port/send_group_msg",
      "napcat_token": "你的napcat http服务token",
      "napcat_group_ids": ["group_id_1", "group_id_2"],
      "removal_strings": ["投稿", "频道"],
      
      // --- AI 模型库 ---
      "llm_configs": {
        "deepseek_filter_model": {
          "provider": "openai", // 使用openai兼容的API格式
          "api_base": "https://api.deepseek.com/v1",
          "api_key": "sk-your-deepseek-key",
          "model": "deepseek-chat"
        },
        "glm_analysis_model": {
          "provider": "zhipuai", // 使用智谱AI的API格式
          "api_base": "https://open.bigmodel.cn/api/paas/v4",
          "api_key": "your-zhipu-key",
          "model": "glm-4-flash"
        }
      },
      
      // --- 任务到模型的映射 ---
      "task_model_mapping": {
        "analysis_and_refinement": "deepseek_filter_model", // 高质量模型用于分析和微调
        "filtering": "deepseek_filter_model",             // 高质量模型用于主过滤任务
        "summarization": "glm_analysis_model"               // 高性价比模型用于生成摘要
      },
      
      // --- AI 过滤器详细配置 ---
      "llm_filter": {
        "enabled": true,
        "base_prompt": "你是一位专业的科技新闻编辑...", // 建议使用 prompt_generator.py 生成
        "user_like_prompt": "我特别喜欢关于AI硬件的新闻。", // 可选，表达个人偏好
        "user_dislike_prompt": "我不喜欢关于加密货币的新闻。", // 可选，明确不感兴趣的领域
        "deduplication_window_hours": 24 // 去重时间窗口
      }
    }
    ```

4.  **生成智能指令 (强烈建议)**
    在启动主程序前，先运行交互式指令生成器：
    ```bash
    python prompt_generator.py
    ```
    根据提示，它会引导您完成 `base_prompt` 的创建和微调。

5.  **启动 Bot**
    ```bash
    python main.py
    ```
    首次启动会提示输入 Telegram 验证码 / 2FA 密码。成功后即可开始智能监听。

## 🛠️ 辅助工具使用

-   **测试AI逻辑**: 在调整了 `config.json` 中的 `llm_filter` 配置后，可以运行测试脚本来快速验证AI的行为是否符合预期，而无需等待真实消息。
    ```bash
    python test_handler.py
    ```

## 常见问题 ❓

1.  **首次启动提示验证码？**
    - 这是正常的 Telegram 登录流程。成功后会保留 session 文件，无需再次登录。

2.  **如何指定多个频道监听？**
    - 只需在 `config.json` 的 `channel_usernames` 列表中添加更多频道用户名即可。

3.  **AI过滤不符合预期怎么办？**
    - 首先，运行 `python prompt_generator.py`，通过对话式微调功能优化您的 `base_prompt`。
    - 其次，检查 `user_like_prompt` 和 `user_dislike_prompt` 是否准确表达了您的偏好。
    - 最后，使用 `python test_handler.py` 快速迭代和测试您的修改。

## 鸣谢 & 许可 📜

- 感谢 [Telethon](https://github.com/LonamiWebs/Telethon) 提供强大的 Telegram API 接口封装。
- 感谢 [NapcatQQ](https://github.com/NapNeko/NapCatQQ) 提供了可靠的QQ消息推送能力。

> 如有问题或改进建议，欢迎提交 [Issues](#) 或发起 [Pull Requests](#)。祝使用愉快！
