# Common Tasks - PRISM-INSIGHT

> **Note**: This is a detailed task reference. For quick overview, see main [CLAUDE.md](../CLAUDE.md).

---

## Task 1: Adding a New AI Agent

```python
# 1. Create agent file
# File: cores/agents/your_agent.py

from mcp_agent import Agent

def create_your_agent(company_name, company_code, reference_date, language="ko"):
    if language == "en":
        instruction = """Your English instruction..."""
    else:
        instruction = """한국어 지시사항..."""

    return Agent(
        instruction=instruction,
        description=f"Your Agent for {company_name}",
        mcp_servers=["kospi_kosdaq"],  # Add required MCP servers
    )

# 2. Register in cores/agents/__init__.py
from .your_agent import create_your_agent

def get_agent_directory(...):
    agents = {
        # ... existing agents
        "your_section": lambda: create_your_agent(...),
    }
    return agents

# 3. Add to base_sections in cores/analysis.py
base_sections = [
    "price_volume_analysis",
    # ... existing sections
    "your_section",  # Add your section
]

# 4. Add section template in cores/report_generation.py
section_templates = {
    # ... existing templates
    "your_section": """
## Your Section Title

{content}
""",
}
```

---

## Task 2: Modifying Surge Detection Criteria

```python
# File: trigger_batch.py

def detect_surge_stocks(mode="morning"):
    # Modify thresholds
    VOLUME_THRESHOLD = 2.0  # Change: Volume surge ratio
    GAP_THRESHOLD = 3.0     # Change: Price gap percentage
    MIN_MARKET_CAP = 1000   # Change: Minimum market cap (billion KRW)

    # Add custom filters
    filtered_stocks = df[
        (df['volume_ratio'] >= VOLUME_THRESHOLD) &
        (df['gap_percent'] >= GAP_THRESHOLD) &
        (df['market_cap'] >= MIN_MARKET_CAP) &
        (df['your_custom_condition'])  # Add custom condition
    ]

    return filtered_stocks
```

---

## Task 3: Adding Multi-Language Support

```python
# 1. Add language to cores/language_config.py
class LanguageConfig:
    SUPPORTED_LANGUAGES = ["ko", "en", "ja", "zh", "es", "fr", "de", "your_lang"]

    TEMPLATES = {
        "your_lang": {
            "report_title": "Your Language Title",
            "sections": {
                "technical_analysis": "Technical Analysis",
                # ... add all sections
            }
        }
    }

# 2. Add Telegram channel to .env
TELEGRAM_CHANNEL_ID_YOUR_LANG="-1001234567899"

# 3. Use in broadcasting
python stock_analysis_orchestrator.py --broadcast-languages ko,en,your_lang
```

---

## Task 4: Modifying Trading Strategy

```python
# File: cores/agents/trading_agents.py

def create_trading_scenario_agent(...):
    instruction = """
    Trading Scenario Generation Instructions (William O'Neil CAN SLIM):

    FUNDAMENTAL GATE (F1~F4):
    - F1 Profitability: recent 2 quarters operating profit positive (or clear turnaround)
    - F2 Balance Sheet: debt ratio < 200% or below industry average
    - F3 Growth: ROE >= 5% or 2-year sales growth >= 10%
    - F4 Business Clarity: clear business model and competitive advantages

    MARKET REGIME MATRIX:
    - parabolic / strong_bull / moderate_bull: min_score 6, stop loss -7%
    - sideways: min_score 7, stop loss -6%
    - moderate_bear: min_score 7, stop loss -5%
    - strong_bear: min_score 8, stop loss -5%

    RISK MANAGEMENT:
    - Expected risk-reward ratio floor based on regime (e.g., strong_bull: 1.0, sideways: 1.3, strong_bear: 1.8)
    - Trailing stop-loss: raised to protect gains (-8~10% in bull, -3~5% in bear)

    PORTFOLIO CONSTRAINTS:
    - Max slot usage: 6~10 slots based on market risk level
    - Same industry sector cap: 2 positions (rationale required for more)
    """
    return Agent(instruction=instruction, ...)

# Apply changes
# 1. Modify instruction text
# 2. Update stock_tracking_agent.py if needed
# 3. Test with quick_test.py
```

---

## Task 5: Customizing Report Format

```python
# File: cores/report_generation.py

# 1. Modify report template
REPORT_TEMPLATE = """
# {company_name} ({company_code}) Investment Analysis Report

**Analysis Date**: {reference_date}
**Analyst**: PRISM-INSIGHT AI Agent System
**Language**: {language}

---

## Your Custom Section

{custom_content}

---

{sections}

---

## Investment Strategy

{investment_strategy}

---

**Disclaimer**: {disclaimer}
"""

# 2. Add custom sections
def generate_full_report(section_reports, investment_strategy, ...):
    custom_content = generate_custom_section(...)

    report = REPORT_TEMPLATE.format(
        company_name=company_name,
        custom_content=custom_content,
        sections=format_sections(section_reports),
        investment_strategy=investment_strategy,
        ...
    )
    return report
```

---

## Task 6: Adding New MCP Server

```bash
# 1. Install MCP server
npm install -g your-mcp-server
# or
pip install your-mcp-server
```

```yaml
# 2. Add to mcp_agent.config.yaml
mcp:
  servers:
    your_server: npx your-mcp-server
    # or
    your_server: python3 -m your_mcp_server
```

```yaml
# 3. Add credentials to mcp_agent.secrets.yaml (if needed)
YOUR_SERVER_API_KEY: "your-api-key"
```

```python
# 4. Use in agent
def create_your_agent(...):
    return Agent(
        instruction="...",
        mcp_servers=["your_server"],  # Add your server
    )
```

---

## Task 8: Dashboard JSON Generation

```bash
# Generate dashboard data from trading history
python examples/generate_dashboard_json.py

# Skip English translation (faster)
python examples/generate_dashboard_json.py --no-translation
```

**Output files:**
- `examples/dashboard/public/dashboard_data.json` (Korean)
- `examples/dashboard/public/dashboard_data_en.json` (English)

**Features:**
- Database to JSON conversion from trading history
- Multi-language support via translation_utils.py
- Market index data integration
- Portfolio performance metrics
- Trading Insights data (principles, journal, intuitions)
- Performance analysis (7/14/30 day tracking)

---

## Task 9: Trading Memory Compression & Cleanup

```bash
# Weekly memory compression with cleanup (recommended for cron)
python compress_trading_memory.py

# Preview changes without executing
python compress_trading_memory.py --dry-run

# Skip cleanup phase (compression only)
python compress_trading_memory.py --skip-cleanup

# Custom cleanup thresholds
python compress_trading_memory.py \
    --max-principles 30 \
    --max-intuitions 30 \
    --stale-days 60 \
    --archive-days 180
```

**Cleanup thresholds:**
- `max-principles`: 50 (default) - Maximum active principles
- `max-intuitions`: 50 (default) - Maximum active intuitions
- `stale-days`: 90 (default) - Deactivate unvalidated items
- `archive-days`: 365 (default) - Delete old Layer 3 journals

---

## Task 10: Performance Tracking Migration

```bash
# Migrate watchlist/trading history to performance tracker
# (For analyzing 7/14/30 day returns of analyzed stocks)

# Preview migration
python utils/migrate_watchlist_to_performance_tracker.py --dry-run

# Execute migration
python utils/migrate_watchlist_to_performance_tracker.py

# Reset and re-migrate (deletes existing tracker data)
python utils/migrate_watchlist_to_performance_tracker.py --reset
```

**Features:**
- Fetches 7/14/30 day prices from pykrx
- Auto-detects trigger_type (volume_surge, gap_up, etc.)
- Period unification: aligns trading history with watchlist dates
- Duplicate prevention (ticker + date unique constraint)

---

## Task 11: Lessons to Principles Migration

```bash
# Migrate trading_journal lessons to trading_principles table

# Preview migration
python utils/migrate_lessons_to_principles.py --dry-run

# Execute migration
python utils/migrate_lessons_to_principles.py
```

**What it does:**
- Extracts high-priority lessons as universal principles
- Links principles to source journal entries
- Sets appropriate scope (universal/sector/market)

---

*See also: [CLAUDE.md](../CLAUDE.md) | [CLAUDE_AGENTS.md](CLAUDE_AGENTS.md) | [CLAUDE_TROUBLESHOOTING.md](CLAUDE_TROUBLESHOOTING.md)*
