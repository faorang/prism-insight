# 🐳 PRISM-INSIGHT Docker 설치 가이드

Ubuntu 24.04 기반 AI 주식 분석 시스템을 Docker로 간편하게 실행하세요.

---

## 📋 목차

1. [시스템 구성](#-시스템-구성)
2. [준비사항](#-준비사항)
3. [설치 및 실행](#-설치-및-실행)
4. [설정 파일](#-설정-파일)
5. [테스트](#-테스트)
6. [사용법](#-사용법)
7. [문제 해결](#-문제-해결)

---

## 🔧 시스템 구성

### Docker 이미지에 포함된 구성요소

#### 시스템

- **OS**: Ubuntu 24.04 LTS
- **Python**: 3.12.x (가상환경)
- **Node.js**: 22.x LTS
- **UV**: Python 패키지 관리자
- **wkhtmltopdf**: PDF 변환 도구
- **한글 폰트**: Nanum 폰트 패밀리

#### Python 패키지

- OpenAI API (GPT-4.1, GPT-5)
- Anthropic API (Claude Sonnet 4.5)
- MCP Agent 및 관련 서버들
- matplotlib, seaborn (데이터 시각화)
- 프로젝트 requirements.txt의 모든 패키지

#### MCP 서버

- **kospi-kosdaq**: 한국 주식 데이터
- **perplexity-ask**: AI 검색
- **firecrawl**: 웹 크롤링
- **sqlite**: 데이터베이스
- **time**: 시간 관리

---

## 📦 준비사항

### 1. Docker 설치 확인

```bash
# Docker 버전 확인
docker --version

# Docker Compose 버전 확인
docker-compose --version
```

Docker가 없다면:

- **Ubuntu**: https://docs.docker.com/engine/install/ubuntu/
- **macOS**: https://docs.docker.com/desktop/install/mac-install/
- **Windows**: https://docs.docker.com/desktop/install/windows-install/

### 2. 시스템 요구사항

- Docker 20.10 이상
- 4GB RAM 이상
- 10GB 디스크 여유 공간

### 3. 필수 API 키 준비

- OpenAI API 키 (https://platform.openai.com/api-keys)
- Anthropic API 키 (https://console.anthropic.com/settings/keys)
- Perplexity API 키 (https://www.perplexity.ai/settings/api)
- Firecrawl API 키 (https://www.firecrawl.dev/)
- Telegram Bot Token ([@BotFather](https://t.me/BotFather)에서 발급)
- Telegram Channel ID

---

## 🚀 설치 및 실행

### 전체 흐름

```
1️⃣ 호스트(로컬)에서 설정 파일 준비
   ↓
2️⃣ 호스트(로컬)에서 Docker Compose 실행
   ↓
3️⃣ 컨테이너에 접속하여 테스트
```

### 방법 1: Docker Compose 사용 (권장)

#### 1단계: 설정 파일 준비 (호스트/로컬에서)

프로젝트 루트 디렉토리에서 실행:

```bash
# 현재 위치 확인 (프로젝트 루트여야 함)
pwd
# 예: /home/user/prism-insight

# .env 파일 생성 및 편집
cp .env.example .env
nano .env
# 또는 vi, vim, code 등 원하는 에디터 사용

# MCP 설정 파일 생성 및 편집
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
nano mcp_agent.config.yaml

# MCP secrets 파일 생성 및 편집
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
nano mcp_agent.secrets.yaml
```

**중요**: 이 단계는 **컨테이너 실행 전** 로컬 컴퓨터에서 해야 합니다!

#### 2단계: Docker Compose 실행 (호스트/로컬에서)

```bash
# 빌드 및 실행 (백그라운드)
docker-compose up -d --build

# 로그 확인 (Ctrl+C로 종료)
docker-compose logs -f

# 컨테이너 접속
docker-compose exec prism-insight /bin/bash
```

#### 3단계: 테스트 (컨테이너 내부)

```bash
# Python 버전 확인
python3 --version

# 프로젝트 디렉토리 확인
ls -la /app/prism-insight

# 시장 영업일 확인
python3 check_market_day.py
```

### 방법 2: Docker 명령어 직접 사용

모든 명령어는 **호스트(로컬)**에서 실행합니다.

```bash
# 이미지 빌드
docker build -t prism-insight:latest .

# 컨테이너 실행
docker run -it --name prism-insight-container \
  -v $(pwd)/data:/app/prism-insight/data \
  -v $(pwd)/.env:/app/prism-insight/.env \
  -v $(pwd)/mcp_agent.config.yaml:/app/prism-insight/mcp_agent.config.yaml \
  -v $(pwd)/mcp_agent.secrets.yaml:/app/prism-insight/mcp_agent.secrets.yaml \
  -v $(pwd)/reports:/app/prism-insight/reports \
  -v $(pwd)/pdf_reports:/app/prism-insight/pdf_reports \
  prism-insight:latest

# 실행 중인 컨테이너 접속 (새 터미널에서)
docker exec -it prism-insight-container /bin/bash
```

---

## ⚙️ 설정 파일

### 필수 설정 파일 3개

#### 1. `.env` 파일

```bash
TELEGRAM_BOT_TOKEN=여기에_봇_토큰_입력
TELEGRAM_AI_BOT_TOKEN=여기에_AI봇_토큰_입력
TELEGRAM_CHANNEL_ID=@여기에_채널ID_입력
```

#### 2. `mcp_agent.config.yaml` 파일

```yaml
$schema: ../../schema/mcp-agent.config.schema.json
execution_engine: asyncio
logger:
  type: console
  level: info
mcp:
  servers:
    firecrawl:
      command: "npx"
      args: ["-y", "firecrawl-mcp"]
      env:
        FIRECRAWL_API_KEY: "여기에_Firecrawl_API키_입력"
    kospi_kosdaq:
      command: "python3"
      args: ["-m", "kospi_kosdaq_stock_server"]
    perplexity:
      command: "node"
      args: ["perplexity-ask/dist/index.js"]
      env:
        PERPLEXITY_API_KEY: "여기에_Perplexity_API키_입력"
    sqlite:
      command: "uv"
      args:
        [
          "--directory",
          "sqlite",
          "run",
          "mcp-server-sqlite",
          "--db-path",
          "stock_tracking_db",
        ]
    time:
      command: "uvx"
      args: ["mcp-server-time"]
openai:
  default_model: gpt-5
  reasoning_effort: medium
```

#### 3. `mcp_agent.secrets.yaml` 파일

```yaml
$schema: ../../schema/mcp-agent.config.schema.json
openai:
  api_key: 여기에_OpenAI_API키_입력
anthropic:
  api_key: 여기에_Anthropic_API키_입력
```

### 보안 주의사항

```bash
# 파일 권한 설정
chmod 600 .env
chmod 600 mcp_agent.secrets.yaml

# Git 추적 제외 확인
cat .gitignore | grep -E "\.env|secrets"
```

---

## 🧪 테스트

컨테이너 접속 후 아래 명령어들로 테스트하세요.

### 1. 기본 환경 테스트

```bash
# Python 버전 확인 (3.12.x 예상)
python3 --version

# 가상환경 확인 (/app/venv/bin/python 예상)
which python

# 주요 패키지 확인
pip list | grep -E "openai|anthropic|mcp-agent"

# Node.js 확인
node --version
npm --version

# UV 확인
uv --version
```

### 2. 한글 폰트 테스트

```bash
# 한글 폰트 목록
fc-list | grep -i nanum

# Python 한글 차트 테스트
python3 << 'EOF'
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

fonts = [f.name for f in fm.fontManager.ttflist if 'Nanum' in f.name]
print("한글 폰트:", fonts)

plt.rcParams['font.family'] = 'NanumGothic'
fig, ax = plt.subplots()
ax.plot([1, 2, 3], [1, 4, 9])
ax.set_title('한글 테스트')
plt.savefig('/tmp/test_korean.png')
print("✅ 차트 생성 완료: /tmp/test_korean.png")
EOF
```

### 3. 주식 데이터 조회 테스트

```bash
python3 << 'EOF'

from krx_data_client import (
    get_market_ohlcv_by_ticker,
    get_nearest_business_day_in_a_week,
    get_market_cap_by_ticker,
    get_market_ticker_name,
)
from datetime import datetime, timedelta

today = datetime.now().strftime("%Y%m%d")
week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

try:
    df = get_market_ohlcv(week_ago, today, "005930")
    print("✅ 삼성전자 주가 데이터 조회 성공!")
    print(df.tail())
except Exception as e:
    print(f"⚠️ 오류 (주말/공휴일일 수 있음): {e}")
EOF
```

### 4. 프로젝트 스크립트 테스트

```bash
# 시장 영업일 확인
python3 check_market_day.py

# 도움말 확인
python3 stock_analysis_orchestrator.py --help
python3 trigger_batch.py --help
```

---

## 💻 사용법

### 실행 위치 안내

- **🖥️ 호스트/로컬**: Docker Compose 명령어
- **🐳 컨테이너 내부**: 프로젝트 실행 명령어

---

### Docker Compose 명령어 (호스트/로컬에서)

```bash
# 컨테이너 시작
docker-compose up -d

# 컨테이너 중지
docker-compose stop

# 컨테이너 재시작
docker-compose restart

# 로그 확인
docker-compose logs -f prism-insight

# 컨테이너 접속
docker-compose exec prism-insight /bin/bash

# 컨테이너 삭제
docker-compose down

# 볼륨까지 삭제
docker-compose down -v
```

### 프로젝트 실행 (컨테이너 내부)

```bash
# 프로젝트 디렉토리로 이동
cd /app/prism-insight

# 오전 급등주 분석
python3 stock_analysis_orchestrator.py --mode morning

# 오후 급등주 분석
python3 stock_analysis_orchestrator.py --mode afternoon

# 오전 + 오후 모두
python3 stock_analysis_orchestrator.py --mode both
```

### Crontab 자동화 설정

```bash
# 컨테이너 내부에서
chmod +x utils/setup_crontab_simple.sh
./utils/setup_crontab_simple.sh
```

### 데이터 백업 (호스트/로컬에서)

```bash
# 호스트에서 실행
docker-compose exec prism-insight tar -czf /tmp/backup.tar.gz \
  stock_tracking_db.sqlite reports/ pdf_reports/

docker cp prism-insight-container:/tmp/backup.tar.gz \
  ./backup-$(date +%Y%m%d).tar.gz
```

---

## 🔧 문제 해결

### 명령어 실행 위치

| 증상/작업            | 실행 위치        | 예시                                          |
| -------------------- | ---------------- | --------------------------------------------- |
| Docker 빌드/실행     | 🖥️ 호스트/로컬   | `docker-compose up -d`                        |
| 컨테이너 접속        | 🖥️ 호스트/로컬   | `docker-compose exec prism-insight /bin/bash` |
| Python 스크립트 실행 | 🐳 컨테이너 내부 | `python3 check_market_day.py`                 |
| 설정 파일 편집       | 🖥️ 호스트/로컬   | `nano .env`                                   |

---

### 빌드 실패 (호스트/로컬에서)

```bash
# Docker 서비스 확인
sudo systemctl status docker

# Docker 재시작
sudo systemctl restart docker

# 캐시 없이 재빌드
docker-compose build --no-cache

# 또는
docker build --no-cache -t prism-insight:latest .
```

### 한글이 깨져 보임 (컨테이너 내부에서)

```bash
# 컨테이너 내부에서 실행
fc-cache -fv
python3 ./cores/ubuntu_font_installer.py
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### 가상환경 미활성화 (컨테이너 내부에서)

```bash
# 가상환경 활성화
source /app/venv/bin/activate

# 확인
which python
# 예상 출력: /app/venv/bin/python
```

### API 키 인식 오류

```bash
# 1. 호스트/로컬에서 설정 파일 확인
cat .env
cat mcp_agent.secrets.yaml

# 2. 컨테이너에 제대로 마운트되었는지 확인 (호스트/로컬에서)
docker-compose exec prism-insight cat /app/prism-insight/.env

# 3. 컨테이너 재시작 (호스트/로컬에서)
docker-compose restart
```

### 권한 문제 (호스트/로컬에서)

```bash
# 호스트에서
chmod -R 755 data reports pdf_reports
sudo chown -R $USER:$USER data reports pdf_reports
```

### 포트 충돌

```bash
# docker-compose.yml에서 포트 변경
# ports:
#   - "8080:8080"  # 다른 포트로 변경
```

---

## 📊 추가 정보

### 컨테이너 내부 디렉토리 구조

```
/app/
├── venv/                      # Python 가상환경
└── prism-insight/            # 프로젝트 루트
    ├── cores/                # AI 분석 엔진
    ├── trading/              # 자동매매
    ├── perplexity-ask/       # MCP 서버
    ├── sqlite/               # 데이터베이스
    ├── reports/              # 분석 보고서
    └── pdf_reports/          # PDF 보고서
```

### 이미지 정보

- **베이스 이미지**: ubuntu:24.04
- **예상 크기**: ~3-4GB
- **빌드 시간**: ~5-10분 (네트워크 속도에 따라)

### 주요 특징

- ✅ 완전 자동화 (Git clone ~ 의존성 설치)
- ✅ 한글 완벽 지원 (Nanum 폰트)
- ✅ MCP 서버 통합
- ✅ 데이터 영속성 (볼륨 마운트)
- ✅ Docker Compose 지원

---

## 📞 지원

- **프로젝트**: https://github.com/dragon1086/prism-insight
- **텔레그램**: https://t.me/stock_ai_agent
- **이슈**: https://github.com/dragon1086/prism-insight/issues

---

## ⚠️ 주의사항

- API 키는 절대 Git에 커밋하지 마세요
- `.env` 파일은 `.gitignore`에 추가되어 있습니다
- 실제 운영 환경에서는 적절한 보안 조치를 취하세요
- 첫 빌드는 5-10분 정도 소요됩니다

---

## 🔧 경로 설정 정보

프로젝트는 **자동 경로 감지**를 사용하므로 어떤 환경에서도 작동합니다:

- **로컬 환경**: `~/my-path/prism-insight` ✅
- **Docker 환경**: `/app/prism-insight` ✅
- **다른 개발자**: `/home/user/custom-path` ✅

Python 실행 파일도 자동 감지됩니다 (우선순위):

1. 프로젝트 가상환경 (`venv/bin/python`)
2. pyenv Python (`~/.pyenv/shims/python`)
3. 시스템 Python (`python3`)

---

**⭐ 도움이 되셨다면 GitHub 저장소에 Star를 눌러주세요!**
**라이센스**: MIT | **만든 사람**: PRISM-INSIGHT 커뮤니티
