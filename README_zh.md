<div align="center">
  <img src="docs/images/prism-insight-logo.jpeg" alt="PRISM-INSIGHT Logo" width="300">
  <br><br>
  <img src="https://img.shields.io/badge/License-AGPL%20v3-blue.svg" alt="License">
  <img src="https://img.shields.io/badge/python-3.10+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OpenAI-GPT--5-green.svg" alt="OpenAI">
  <img src="https://img.shields.io/badge/Anthropic-Claude--Sonnet--4.5-green.svg" alt="Anthropic">
</div>

# PRISM-INSIGHT

[![GitHub Sponsors](https://img.shields.io/github/sponsors/dragon1086?style=for-the-badge&logo=github-sponsors&color=ff69b4&label=Sponsors)](https://github.com/sponsors/dragon1086)
[![Stars](https://img.shields.io/github/stars/dragon1086/prism-insight?style=for-the-badge)](https://github.com/dragon1086/prism-insight/stargazers)

> **AI 驱动的股票市场分析与交易系统**
>
> 13+ 个专业 AI 代理协同工作，检测异动股票、生成分析师级别的研究报告，并自动执行交易。

<p align="center">
  <a href="README.md">English</a> |
  <a href="README_ko.md">한국어</a> |
  <a href="README_ja.md">日本語</a> |
  <a href="README_zh.md">中文</a> |
  <a href="README_es.md">Español</a>
</p>

---

### 🏆 铂金赞助商

<div align="center">
<a href="https://wrks.ai/en">
  <img src="docs/images/wrks_ai_logo.png" alt="AI3 WrksAI" width="50">
</a>

**[AI3](https://www.ai3.kr/) | [WrksAI](https://wrks.ai/en)**

**WrksAI** 的开发者 **AI3** —— 专为职场人士打造的 AI 助手，<br>
自豪地赞助 **PRISM-INSIGHT** —— 专为投资者打造的 AI 助手。
</div>

---

## ⚡ 立即体验（无需安装）

### 1. 实时仪表盘
实时查看 AI 交易绩效：
👉 **[analysis.stocksimulation.kr](https://analysis.stocksimulation.kr/)**

### 2. Telegram 频道
每日获取异动股票提醒和 AI 分析报告：
- 🇺🇸 **[英语频道](https://t.me/prism_insight_global_en)**
- 🇰🇷 **[韩语频道](https://t.me/stock_ai_agent)**
- 🇯🇵 **[日语频道](https://t.me/prism_insight_ja)**
- 🇨🇳 **[中文频道](https://t.me/prism_insight_zh)**
- 🇪🇸 **[西班牙语频道](https://t.me/prism_insight_es)**

### 3. 示例报告
---


## 🚀 完整安装

### 前提条件
- Python 3.10+ 或 Docker
- OpenAI API 密钥（[在此获取](https://platform.openai.com/api-keys)）

### 方式 A：Python 安装

```bash
# 1. Clone & Install
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
pip install -r requirements.txt

# 2. Install Playwright for PDF generation
python3 -m playwright install chromium

# 3. Install perplexity-ask MCP server
cd perplexity-ask && npm install && npm run build && cd ..

# 4. Setup config
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit mcp_agent.secrets.yaml with your OpenAI API key
# Edit mcp_agent.config.yaml with KRX credentials (Kakao account)

# 5. Run analysis (no Telegram required!)
python stock_analysis_orchestrator.py --mode morning --no-telegram
```

### 方式 B：Docker（推荐用于生产环境）

```bash
# 1. Clone & Configure
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
# Edit config files with your API keys

# 2. Build & Run
docker-compose up -d

# 3. Run analysis manually (optional)
docker exec prism-insight-container python3 stock_analysis_orchestrator.py --mode morning --no-telegram
```

📖 **完整安装指南**：[docs/SETUP.md](docs/SETUP.md)

---

## 📖 什么是 PRISM-INSIGHT？

PRISM-INSIGHT 是一个**完全开源、免费**的 AI 驱动股票分析系统，支持**韩国（KOSPI/KOSDAQ）**市场。

### 核心功能
- **异动股票检测** - 自动检测成交量/价格异常波动的股票
- **AI 分析报告** - 由 13 个专业 AI 代理生成的专业分析师级别报告
- **交易模拟** - AI 驱动的买卖决策与投资组合管理
- **自动交易** - 通过韩国投资证券 API 实际执行交易
- **Telegram 集成** - 实时提醒与多语言播报

### AI 模型
- **分析与交易**：OpenAI GPT-5
- **Telegram 机器人**：Anthropic Claude Sonnet 4.5
- **翻译**：OpenAI GPT-5（支持英语、日语、中文）

---

## 🤖 AI 代理系统

13+ 个专业代理以团队形式协作：

| 团队 | 代理数量 | 职责 |
|------|----------|------|
| **分析** | 6 个代理 | 技术分析、财务分析、行业分析、新闻分析、市场分析 |
| **策略** | 1 个代理 | 投资策略综合 |
| **通信** | 3 个代理 | 摘要生成、质量评估、翻译 |
| **交易** | 3 个代理 | 买卖决策、交易日志 |
| **咨询** | 2 个代理 | 通过 Telegram 进行用户交互 |

<details>
<summary>📊 查看代理工作流程图</summary>
<br>
<img src="docs/images/aiagent/agent_workflow2.png" alt="Agent Workflow" width="700">
</details>

📖 **代理系统详细文档**：[docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md)

---

## ✨ 主要特性

| 特性 | 说明 |
|------|------|
| **🤖 AI 分析** | 通过 GPT-5 多代理系统进行专家级股票分析 |
| **📊 异动检测** | 通过早盘/午盘市场趋势分析自动生成观察列表 |
| **📱 Telegram** | 实时分析报告分发至频道 |
| **📈 交易模拟** | AI 驱动的投资策略模拟 |
| **💱 自动交易** | 通过韩国投资证券 API 执行交易 |
| **🎨 仪表盘** | 透明的投资组合、交易记录和绩效追踪 |
| **🧠 自我进化** | 交易日志反馈回路 —— 历史触发胜率自动影响未来买入决策（[详情](docs/TRADING_JOURNAL.md#performance-tracker-피드백-루프-self-improving-trading)） |

<details>
<summary>🖼️ 查看截图</summary>
<br>
<img src="docs/images/trigger-en.png" alt="异动检测" width="500">
<img src="docs/images/summary-en.png" alt="摘要" width="500">
<img src="docs/images/dashboard1-en.png" alt="仪表盘" width="500">
</details>

---

## 📈 交易绩效

### 第二赛季（进行中）
| 指标 | 数值 |
|------|------|
| 开始日期 | 2025.09.29 |
| 总交易次数 | 50 |
| 胜率 | 42.00% |
| **累计收益率** | **127.34%** |
| 实盘账户收益率 | +8.50% |

👉 **[实时仪表盘](https://analysis.stocksimulation.kr/)**

---


## 📚 文档

| 文档 | 说明 |
|------|------|
| [docs/SETUP.md](docs/SETUP.md) | 完整安装指南 |
| [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md) | AI 代理系统详情 |
| [docs/TRIGGER_BATCH_ALGORITHMS.md](docs/TRIGGER_BATCH_ALGORITHMS.md) | 异动检测算法 |
| [docs/TRADING_JOURNAL.md](docs/TRADING_JOURNAL.md) | 交易记忆系统 |

---

## 🎨 前端示例

### 落地页
使用 Next.js 和 Tailwind CSS 构建的现代响应式落地页。

👉 **[在线演示](https://prism-insight-landing.vercel.app/)**

```bash
cd examples/landing
npm install
npm run dev
# Visit http://localhost:3000
```

**特性**：矩阵雨动画、打字机效果、GitHub Star 计数器、响应式设计

### 仪表盘
实时投资组合跟踪与绩效仪表盘。

```bash
cd examples/dashboard
npm install
npm run dev
# Visit http://localhost:3000
```

**特性**：投资组合概览、交易历史、绩效指标

📖 **仪表盘安装指南**：[examples/dashboard/DASHBOARD_README.md](examples/dashboard/DASHBOARD_README.md)

---

## 💡 MCP 服务器

### 韩国市场
- **[kospi_kosdaq](https://github.com/dragon1086/kospi-kosdaq-stock-server)** - KRX 股票数据
- **[firecrawl](https://github.com/mendableai/firecrawl-mcp-server)** - 网页爬取
- **[perplexity](https://github.com/perplexityai/modelcontextprotocol)** - 网络搜索
- **[sqlite](https://github.com/modelcontextprotocol/servers-archived)** - 交易模拟数据库


---

## 🤝 参与贡献

1. Fork 本项目
2. 创建功能分支（`git checkout -b feature/amazing-feature`）
3. 提交更改（`git commit -m 'Add amazing feature'`）
4. 推送到分支（`git push origin feature/amazing-feature`）
5. 创建 Pull Request

---

## 📄 许可证

**双重许可：**

### 个人与开源使用
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

个人使用、非商业项目和开源开发免费使用，遵循 AGPL-3.0 协议。

### 商业 SaaS 使用
SaaS 公司需要单独的商业许可证。

📧 **联系方式**：dragon1086@naver.com
📄 **详情**：[LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md)

---

## ⚠️ 免责声明

分析信息仅供参考，不构成投资建议。所有投资决策及由此产生的盈亏均由投资者自行承担。

---

## 💝 赞助支持

### 支持本项目

每月运营成本（约 $310/月）：
- OpenAI API：约 $235/月
- Anthropic API：约 $11/月
- Firecrawl + Perplexity：约 $35/月
- 服务器基础设施：约 $30/月

目前免费服务 450+ 用户。

<div align="center">
  <a href="https://github.com/sponsors/dragon1086">
    <img src="https://img.shields.io/badge/Sponsor_on_GitHub-❤️-ff69b4?style=for-the-badge&logo=github-sponsors" alt="在 GitHub 上赞助">
  </a>
</div>

### 个人赞助者
<!-- sponsors -->
- [@jk5745](https://github.com/jk5745) 💙
<!-- sponsors -->

---

## ⭐ 项目成长

发布以来 **10 周内获得 250+ Star**！

[![Star History Chart](https://api.star-history.com/svg?repos=dragon1086/prism-insight&type=Date)](https://star-history.com/#dragon1086/prism-insight&Date)

---

**⭐ 如果本项目对您有帮助，请给我们一个 Star！**

📞 **联系方式**：[GitHub Issues](https://github.com/dragon1086/prism-insight/issues) | [Telegram](https://t.me/stock_ai_agent) | [Discussions](https://github.com/dragon1086/prism-insight/discussions)
