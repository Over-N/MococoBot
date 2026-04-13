# MococoBot

디스코드 기반 로스트아크 일정 관리 프로젝트입니다.
모코코봇 서비스 종료로 인해 프로젝트를 오픈소스로 공개합니다.

원래는 전체 구조를 정리한 뒤 공개하려고 했지만, 손볼 부분이 너무 많아 끝까지 리팩토링하지 못했습니다.  
그래서 공개에 적합하지 않은 부분만 제외하고, 나머지는 가능한 한 원형에 가깝게 올렸습니다.

현재 저장소에는 frontend 관련 소스, Discord 로그인 구현, 민감한 내부 로직 일부가 제거되어 있습니다.  
또한 예전에 만들다가 만 기능이나, 더 이상 사용하지 않는 코드, 다소 정리되지 않은 부분도 그대로 남아 있습니다.
순수 재미로 개발하던 시절에 시작된 프로젝트라 코딩 좀 하시는분이 보면 쓰레기 코드입니다

잘 정돈된 예제 프로젝트라고 보기는 어렵지만, 실제로 운영하던 서비스의 구조와 흐름을 참고하는 용도로는 의미가 있을 것 같아 공개합니다.

봇은 별개의 서버인데 bot 디렉토리에 봇 운영 로직 올려두겠습니다.

+ discord.py 라이브러리 구동이 아닌, py-cord 기반 구동입니다. 봇 운영 로직 보실때 참고해주세요.

- 실제 서비스 구조를 참고하고 싶은 분
- FastAPI + Discord Bot + Scheduler + Render 서버 분리 구조를 보고 싶은 분
- 로컬에서 기능을 부분적으로 실행해 보고 싶은 분
---

## 프로젝트 구성

이 프로젝트는 크게 4개 프로세스로 나뉩니다.

### 1. API 서버
- 엔트리포인트: `main.py`
- 주요 역할:
  - REST API 제공
  - DB CRUD
  - 파티, 캐릭터, 디스코드 연동용 백엔드 처리
- 주요 디렉터리:
  - `routers/`
  - `services/`
  - `database/`
  - `utils/`

### 2. Render 서버
- 엔트리포인트: `render_main.py`
- 기본 포트: `9001`
- 주요 역할:
  - 캐릭터 카드 이미지
  - 파티 이미지
  - 기타 렌더링 전용 API 처리

### 3. Scheduler
- 엔트리포인트: `scheduler_main.py`
- 주요 역할:
  - 예약 작업
  - 고정 레이드 스케줄링
  - 주기적 백그라운드 작업

### 4. Discord Bot
- 엔트리포인트: `bot/main.py`
- 주요 역할:
  - 디스코드 slash command
  - 버튼 인터랙션 처리
  - 음성/TTS
  - 스티커, 인증, 친구 기능 등

---

## 디렉토리 설명
```text
.
├─ main.py                  # API 서버 시작점
├─ render_main.py           # 렌더 서버 시작점
├─ scheduler_main.py        # 스케줄러 시작점
├─ database/                # DB 연결 관련
├─ routers/                 # API 라우터
├─ services/                # 비즈니스 로직 / 외부 서비스 연동
├─ render/                  # 이미지 렌더링 로직
├─ render_routers/          # 렌더 전용 라우터
├─ utils/                   # 공용 유틸
└─ bot/                     # 별도 디스코드 봇 서버
   ├─ main.py
   ├─ cogs/
   ├─ commands/
   ├─ core/
   └─ handler/
````

---

## 실행 환경

### 권장 버전
* Python 3.12+
* MariaDB 10.5+
* Windows 또는 Linux

### 필요한 외부 구성요소
* MariaDB
* Discord Bot Application (py-cord)
* Lost Ark OpenAPI Key
* 일부 기능용 Discord 서버/채널/권한 설정

---

## 시작 전 주의사항
이 저장소는 공개 과정에서 일부 코드가 제거된 상태입니다.

즉, 아래 사항은 미리 알고 시작하는 게 좋습니다.

* 프론트엔드 관련 코드는 없습니다
* Discord OAuth 로그인 전체 구현은 포함되어 있지 않습니다
* 일부 내부 운영용 로직은 제거되어 있습니다
* 모든 기능이 “바로 실행 즉시 완전 동작”하는 형태는 아닐 수 있습니다

그래도 핵심 구조는 남아 있어서,
아키텍처 참고, 로컬 API 테스트, 기능 단위 재사용에는 충분히 의미가 있습니다.

---

## 설치 방법

### 1. 저장소 클론
```bash
git clone https://github.com/Over-N/MococoBot.git
cd MococoBot
```

### 2. 가상환경 생성

#### Windows
```bash
python -m venv .venv
.venv\Scripts\activate
```

#### Linux / macOS
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. 패키지 설치
이 저장소에는 공개본 기준으로 의존성 파일이 따로 정리되어 있지 않습니다.
그래서 아래 두 방식 중 하나를 추천합니다.

#### 방식 A. 직접 설치
프로젝트를 실행하면서 필요한 패키지를 설치하는 방식입니다.

예시:

```bash
pip install fastapi uvicorn httpx aiomysql python-dotenv psutil pillow
pip install py-cord
```

#### 방식 B. 직접 requirements 정리 후 설치
실제로 운영하거나 장기적으로 유지할 생각이면, 먼저 본인 기준으로 `requirements.txt`를 정리해서 쓰는 걸 추천합니다.

예시:

```txt
fastapi
uvicorn
httpx
aiomysql
python-dotenv
psutil
pillow
py-cord
```

설치:

```bash
pip install -r requirements.txt
```

---

## 데이터베이스 준비

### 1. MariaDB 생성
예시:

```sql
CREATE DATABASE mococobot CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
```

### 2. 테이블 준비
공개 저장소에는 스키마 덤프가 포함되어 있지 않거나, 완전하지 않을 수 있습니다.
그래서 실제 사용 전에는 코드에서 참조하는 테이블을 먼저 확인해야 합니다.

대표적으로 자주 등장하는 테이블 예시:

* `character`
* `class`
* `party`
* `participants`
* `raid`
* `server`
* `user`
* `discord_users`
* `bot_guilds`

처음 보는 분은 다음 순서로 보는 걸 추천합니다.

1. `routers/`
2. `services/`
3. `database/connection.py`
4. `bot/handler/`
5. `bot/cogs/`

즉, 이 프로젝트는 **코드 기준으로 스키마를 역추적하는 방식**이 가장 빠릅니다.

---

## 환경변수 설정
이 프로젝트는 서버 종류별로 필요한 환경변수가 조금 다릅니다.

공개본 기준으로 환경변수는 루트 `.env`와 `bot/.env`를 나눠 두는 방식이 가장 이해하기 쉽습니다.

---

### 루트 `.env` 예시

```env
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=mococobot

DB_POOL_SIZE=20
DB_POOL_MINSIZE=2
DB_POOL_RECYCLE=1800
DB_POOL_TIMEOUT=5
DB_CHARSET=utf8mb4
DB_AUTOCOMMIT=false

DISCORD_BOT_TOKEN=
DISCORD_CLIENT_ID=
DISCORD_CLIENT_SECRET=

LOSTARK_API_KEY=
LOSTARK_API_SUB1_KEY=
LOSTARK_API_SUB2_KEY=
LOSTARK_API_SUB3_KEY=
LOSTARK_API_SUB4_KEY=

API_KEY=your_internal_api_key

RENDER_WORKERS=2
RENDER_LIMIT_CONCURRENCY=64

SCHED_HEARTBEAT_SEC=180

METRICS_SLOW_QUERY_MS=200
METRICS_QUERY_SAMPLE_RATE=0.05
RAID_THUMB_CACHE_TTL_SEC=900
```

---

### `bot/.env` 예시
```env
API_BASE_URL=http://localhost:8000
API_KEY=your_internal_api_key

BOT_TOKEN=your_discord_bot_token

ADMIN_2FA_SECRET=

LOSTARK_API_KEY=
LOSTARK_API_SUB1_KEY=
LOSTARK_API_SUB2_KEY=
```

> 참고
> 공개 코드 기준으로 봇은 `bot/.env`를 직접 읽는 구조가 들어가 있습니다.
> 루트 서버 쪽과 봇 쪽 환경변수는 분리해서 관리하는 걸 추천합니다.

---

## 실행 순서
이 프로젝트는 보통 아래 순서로 띄우는 게 안전합니다.

### 1. API 서버 실행
```bash
python main.py
```

### 2. Render 서버 실행
```bash
python render_main.py
```

### 3. Scheduler 실행
```bash
python scheduler_main.py
```

### 4. Discord Bot 실행
```bash
cd bot
python main.py
```

---

## 로컬 개발 시 권장 순서
처음에는 한 번에 다 띄우지 말고, 아래 순서로 확인하세요.

### 1단계
API 서버만 실행

* DB 연결이 되는지 확인
* 기본 라우터 응답이 되는지 확인

### 2단계
Render 서버 추가

* 이미지 생성 API가 살아있는지 확인

### 3단계
Scheduler 추가

* 예약 작업이 에러 없이 도는지 확인

### 4단계
마지막으로 Bot 실행

* Discord 명령어 등록
* 인터랙션 처리
* API 연동 확인

---

## 어떤 파일부터 보면 좋은가
처음 보는 분 기준 추천 순서입니다.

### 전체 구조를 빨리 이해하고 싶다면
1. `README.md`
2. `main.py`
3. `render_main.py`
4. `scheduler_main.py`
5. `bot/main.py`

### API 구조를 보고 싶다면
1. `routers/character.py`
2. `routers/party.py`
3. `services/character_sync.py`
4. `services/party_service.py`
5. `database/connection.py`

### 디스코드 연동을 보고 싶다면
1. `services/discord_service.py`
2. `bot/main.py`
3. `bot/handler/`
4. `bot/cogs/`

### 이미지 렌더링 구조를 보고 싶다면
1. `render_main.py`
2. `render_routers/party_image.py`
3. `render_routers/character_image.py`
4. `render/`

---

## 이 프로젝트를 참고하기 좋은 포인트
이 저장소를 볼 때 특히 참고할 만한 부분은 아래입니다.

### 1. 서버 분리 구조
* API
* Render
* Scheduler
* Bot

를 분리한 구조라서, 실제 서비스 운영형 구조를 보는 데 도움이 됩니다.

### 2. Discord Bot + API 백엔드 분리
봇이 DB에 직접 다 때리는 구조가 아니라,
백엔드 API를 통해 처리하는 흐름이 많아서 역할 분리가 비교적 명확합니다.

### 3. 이미지 렌더 서버 분리
이미지 생성 로직을 일반 API와 분리해서 운영하는 방식이 들어가 있습니다.

### 4. 비동기 DB 연결
`aiomysql` 기반 풀 연결 구조를 참고할 수 있습니다.

---

## 현재 상태에서 바로 안 맞을 수 있는 부분
공개본 그대로 실행하면 아래가 바로 맞지 않을 수 있습니다.
* DB 스키마 누락
* 프론트 의존 기능 제거
* OAuth 관련 누락
* 내부 운영용 환경값 누락
* 특정 API/채널/역할 ID 부재
* Discord 권한 문제
* 렌더용 리소스 파일 누락 가능성


### 추천 접근 방식
* 전체를 한 번에 복구하려고 하지 않기
* 기능 단위로 실행해 보기
* DB부터 맞추기
* API와 Bot을 분리해서 확인하기
* 필요한 기능만 추려서 재구성하기

---

## 추천 커스터마이징 순서
이 프로젝트를 자기 프로젝트로 바꾸려면 보통 아래 순서가 좋습니다.

1. DB 스키마 정리
2. 환경변수 정리
3. requirements 정리
4. API 서버 단독 실행 성공
5. Render 서버 연동 성공
6. Bot 연동 성공
7. Scheduler 붙이기
8. 마지막으로 사용하지 않는 기능 제거

---

## 문제 해결 팁

### DB 연결이 안 될 때
* `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` 확인
* MariaDB 권한 확인
* 테이블 존재 여부 확인

### 봇이 명령어를 못 읽을 때
* `BOT_TOKEN` 확인
* Discord Developer Portal에서 Intent 설정 확인
* 서버 초대 권한 확인

### API는 뜨는데 봇이 반응이 없을 때
* `API_BASE_URL` 확인
* `API_KEY` 동일하게 맞췄는지 확인
* 봇 서버에서 API 서버로 접근 가능한지 확인

### 렌더가 안 될 때
* Pillow 설치 여부
* 렌더용 리소스 경로
* `render/`, `render_routers/` 내부 참조 경로 확인

---

## 이 저장소를 보는 관점
이 프로젝트는 “정제된 포트폴리오용 예제”보다
“실서비스 운영 코드 공개본”에 가깝습니다.

그래서 아래처럼 보는 게 맞습니다.

* 베끼기용 완성품
* 배포 즉시 가능한 템플릿

이 아니라,

* 구조 참고용
* 기능 재사용용
* 리팩토링 출발점
* 운영형 코드 학습용

으로 보는 걸 추천합니다.

---

## 마지막으로
이 저장소는 공개용으로 완벽하게 정리된 프로젝트는 아닙니다.

필요한 기능만 골라 가져가거나,
전체를 리팩토링하면서 자기 구조로 바꾸는 방식을 추천드립니다.

```
