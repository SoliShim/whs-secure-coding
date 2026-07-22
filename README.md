# whs-secure-coding

[![CI](https://github.com/SoliShim/whs-secure-coding/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/SoliShim/whs-secure-coding/actions/workflows/ci.yml)

보안을 우선해 만든 **Tiny Second-hand Shopping Platform**입니다. FastAPI와 Jinja2로 서버 렌더링 UI를 제공하고, SQLite에 데이터를 저장하며, WebSocket으로 전체 채팅과 1:1 채팅을 처리합니다.

이 프로젝트는 WhiteHat School 시큐어 코딩 과제의 개발 전 과정인 요구사항 분석, 설계, 구현, 보안 체크리스트·테스트, 유지보수를 함께 문서화합니다.

공개 저장소: [SoliShim/whs-secure-coding](https://github.com/SoliShim/whs-secure-coding)

> **가상 KRW 안내**
>
> 바로지갑의 `원` 표시는 학습용 정수 원장 단위입니다. 현금 가치가 없고 충전·출금·환전할 수 없으며, 실제 은행·카드·결제대행사와 연결되지 않습니다. 계좌번호, 카드번호, 주민등록번호도 수집하지 않습니다.

## 최소 요구사항 7개 매핑

| 강의 최소 요구사항 | 구현 |
|---|---|
| 1. 플랫폼 가입 | 회원가입, 로그인·로그아웃, 공개 프로필, 소개글·비밀번호 변경 |
| 2. 상품 등록·열람 | 상품 이미지 업로드, 목록·상세, 판매자 본인의 수정·삭제·상태 관리 |
| 3. 사용자 간 소통 | 인증된 WebSocket 전체 채팅과 1:1 채팅, 메시지 이력 |
| 4. 악성 사용자·상품 차단 | 사용자·상품 신고, 신고자별 중복 차단, 가입 10분이 지난 계정의 고유 신고 3건 자동 격리, 관리자 검토·복구 |
| 5. 사용자 간 송금 | 현재 비밀번호 재확인, 정수 가상 KRW 송금, 멱등 처리와 이중 원장 |
| 6. 상품 검색 | 상품명·설명 검색과 페이지네이션, 바인딩 SQL |
| 7. 관리자 전체 관리 | 사용자 상태, 상품 노출, 신고 처리, 거래·감사 데이터 열람 |

## 보안 하이라이트

- 비밀번호는 평문이나 복호화 가능한 값으로 저장하지 않고 **Argon2id**로 해시합니다.
- 세션 원문 토큰은 쿠키에만 전달하고 DB에는 **SHA-256 해시**만 저장합니다.
- 로그인 전 CSRF 세션은 만료 시간이 든 **HMAC 서명 쿠키**라 익명 조회가 DB 세션을 쌓지 않습니다.
- 세션 쿠키에 `HttpOnly`, `SameSite=Lax`를 적용하고 운영 HTTPS에서는 `Secure`를 강제합니다.
- 모든 상태 변경 폼은 세션 결합 CSRF 토큰과 Origin/Referer 정책을 검사합니다. 토큰은 상수 시간으로 비교합니다.
- 로그인 성공 시 세션을 회전하고, 비밀번호 변경·계정 정지 시 기존 세션을 모두 폐기합니다.
- 로그인·가입·신고·송금·채팅에 행위별 속도 제한을 적용합니다.
- Jinja2 자동 이스케이프와 CSP를 사용하며 사용자 입력에 `safe`를 적용하지 않습니다.
- SQL 값은 `sqlite3` 바인딩 매개변수로 전달합니다. 객체 변경 시 인증과 별도로 소유권 또는 관리자 RBAC를 검사합니다.
- pure ASGI 미들웨어가 `Content-Length`와 실제 chunked 수신량을 각각 확인해 HTTP 본문을 6MB로 제한합니다.
- JPEG·PNG·WebP의 픽셀·축 길이를 디코딩 전에 검사한 뒤 별도 worker thread에서 메타데이터 없는 JPEG로 다시 저장합니다. 전역 동시 이미지 처리 3개, 사용자+IP별 요청 제한, 활성 상품 100개 상한도 적용합니다.
- WebSocket은 세션, 정확한 Origin, 첫 CSRF 인증 프레임, 대화 상대 권한, 메시지 길이와 속도를 검사합니다. 인증 전 IP 30회/분, 사용자당 동시 3개, 프로세스 전체 80개 연결 상한을 둡니다.
- WebSocket 전송은 연결별 lock과 2초 timeout을 사용하고 병렬 fan-out하여 느린 클라이언트 하나가 다른 수신자를 막지 않게 합니다.
- async 라우트의 SQLite·Pillow·Argon2 작업은 worker thread로 넘겨 event loop 점유를 줄입니다.
- 송금은 `BEGIN IMMEDIATE` 안에서 잔액 조건부 차감, 입금, transfer와 원장 2행을 함께 기록합니다. 송신자별 멱등 키가 중복 출금을 막으며, 서로 다른 키와 같은 키의 병렬 요청도 회귀 테스트합니다.
- transfer, 원장, 감사 로그에는 UPDATE/DELETE 방지 트리거가 있습니다.
- CSP, `nosniff`, clickjacking 방어, Referrer/Permissions 정책 등 공통 보안 헤더를 적용합니다.
- 예상하지 못한 오류는 요청 ID가 있는 일반 메시지로 바꾸며 스택 트레이스·SQL·파일 경로를 응답에 노출하지 않습니다.
- production 설정은 강한 비밀, HTTPS Origin, `Secure` 쿠키가 없으면 시작을 거부합니다.

## 요구 환경

- Python **3.12.x** (`pyproject.toml` 기준 3.13 미지원)
- Git
- Linux, macOS 또는 Windows의 WSL2 Ubuntu
- 최신 브라우저 2개 또는 브라우저와 휴대폰: 다중 사용자 채팅 시연용

Windows 네이티브 Python보다 강의 환경과 가까운 WSL2 사용을 권장합니다. 아래 셸 명령은 Linux, macOS, WSL의 Bash/Zsh 기준입니다.

## 설치와 초기 설정

저장소를 clone하거나 압축을 푼 뒤 저장소 루트에서 그대로 실행합니다.

```bash
cd whs-secure-coding
cp .env.example .env
python3.12 --version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m app.cli init-db
python -m app.cli create-admin
```

`create-admin`은 관리자 아이디, 표시 이름, 12자 이상의 비밀번호를 대화형으로 묻습니다. 비밀번호는 화면에 표시되지 않습니다.

데모 계정과 상품이 필요할 때만 다음 명령을 한 번 실행합니다.

```bash
python -m app.cli seed-demo
```

`seed-demo`는 `alice`, `bob`, `charlie`와 샘플 상품을 생성합니다. 공통 임시 비밀번호는 **최초 생성 시 터미널에 한 번만** 무작위로 출력됩니다. 출력값을 외부에 공유하거나 public 저장소에 기록하지 마세요. 명령을 다시 실행해도 기존 데모 비밀번호를 표시하거나 변경하지 않습니다.

런타임 패키지만 필요한 경우 `requirements-dev.txt` 대신 다음을 설치할 수 있습니다.

```bash
python -m pip install -r requirements.txt
```

## 로컬 실행

WebSocket 연결 상태와 속도 제한이 프로세스 메모리에 있으므로 **worker는 반드시 1개**로 실행합니다. WebSocket 압축도 명시적으로 끕니다.

```bash
.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --workers 1 \
  --no-server-header \
  --ws-per-message-deflate false \
  --ws-max-size 4096 \
  --ws-max-queue 16 \
  --limit-concurrency 100
```

브라우저에서 [http://127.0.0.1:8000](http://127.0.0.1:8000)을 엽니다. 상태 확인 엔드포인트는 `GET /healthz`입니다.

서버를 재시작했을 때 개발용 `APP_SECRET`이 자동 생성되었다는 경고가 나오면 `.env`의 `APP_SECRET`이 비어 있다는 뜻입니다. 개발에는 사용할 수 있지만 기존 세션의 지속성을 원하면 다음 명령으로 값을 만들고 **출력 문자열 자체를** `.env`의 `APP_SECRET=` 뒤에 붙여 넣으세요. `.env`에서는 셸의 `$(...)` 치환이 실행되지 않습니다.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 계정과 데모 데이터 주의사항

- 일반 회원은 가입 시 현금 가치가 없는 가상 잔액 `100,000원`을 받습니다.
- CLI로 만든 관리자는 초기 잔액이 `0원`입니다.
- 송금 화면은 현재 비밀번호를 다시 확인하며 1회 송금 한도는 `1,000,000원`입니다.
- 가상 잔액이나 원장 기록을 실제 대금 지급 증거로 사용하지 마세요.
- LAN/HTTP 데모에는 합성 이름과 전용 임시 비밀번호만 사용하세요. 다른 서비스에서 사용하는 비밀번호를 입력하지 마세요.
- DB, `.env`, 업로드, 로그와 백업 파일은 `.gitignore` 대상이며 public GitHub에 올리면 안 됩니다.
- 데모 종료 후 서버를 내리고, 외부 터널을 사용했다면 해당 세션도 종료하세요.

## 같은 Wi-Fi의 휴대폰으로 시연

이 절은 신뢰할 수 있는 로컬 네트워크에서의 짧은 과제 시연용입니다. 공용 Wi-Fi나 인터넷에 HTTP 서버를 공개하지 마세요.

1. 개발 PC의 LAN IP를 확인합니다.

   ```bash
   # macOS Wi-Fi의 일반적인 경우
   ipconfig getifaddr en0

   # Linux/WSL에서 주소 후보 확인
   hostname -I
   ```

2. 예를 들어 LAN IP가 `192.168.0.10`이면 `.env`에서 다음처럼 Host와 Origin을 명시합니다.

   ```dotenv
   ALLOWED_HOSTS=127.0.0.1,localhost,192.168.0.10
   ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,http://192.168.0.10:8000
   SECURE_COOKIES=false
   ```

   `ALLOWED_HOSTS`에는 스킴과 포트를 넣지 않고, `ALLOWED_ORIGINS`에는 스킴과 포트를 포함한 정확한 Origin을 넣습니다.

3. 모든 인터페이스에서 단일 worker 서버를 엽니다.

   ```bash
   .venv/bin/uvicorn app.main:app \
     --host 0.0.0.0 \
     --port 8000 \
     --workers 1 \
     --no-server-header \
     --ws-per-message-deflate false \
     --ws-max-size 4096 \
     --ws-max-queue 16 \
     --limit-concurrency 100
   ```

4. OS 방화벽에서 신뢰하는 로컬 네트워크의 TCP 8000만 임시 허용하고, 휴대폰에서 `http://192.168.0.10:8000`에 접속합니다.
5. PC와 휴대폰에서 서로 다른 데모 계정으로 로그인해 전체 채팅, 1:1 채팅, 상품·송금을 시연합니다.

WSL2는 Windows와 별도 NAT를 사용할 수 있습니다. Windows 11의 mirrored networking을 사용하거나 Windows 방화벽·포트 전달을 별도로 구성해야 휴대폰에서 Windows 호스트 IP로 접근할 수 있습니다. 이 설정이 어렵다면 HTTPS를 제공하는 신뢰 가능한 임시 터널을 사용하고, 터널의 정확한 HTTPS Origin과 Host를 `.env`에 추가하세요.

### HTTP 개발 시연의 위험

로컬 HTTP에서는 `Secure` 쿠키를 사용할 수 없고 WebSocket도 평문 `ws://`입니다. 같은 네트워크의 공격자가 트래픽을 볼 수 있는 환경에서는 세션과 메시지의 기밀성을 보장할 수 없습니다. 따라서 실제 개인정보, 실제 거래 내용, 재사용 비밀번호를 입력하지 말고 신뢰하는 LAN에서만 짧게 사용하세요.

## 운영 배포: HTTPS/WSS 필수

공개 네트워크에서는 Uvicorn을 직접 노출하지 말고 TLS와 WebSocket 업그레이드를 지원하는 역방향 프록시 뒤에 둡니다. HTTPS 페이지에서는 프런트엔드가 자동으로 `wss://`를 사용합니다.

1. 강한 비밀을 생성합니다.

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. 출력값을 그대로 붙여 넣어 운영 `.env`를 구성합니다. 아래 도메인과 경로는 실제 값으로 바꾸세요.

   ```dotenv
   APP_ENV=production
   DATABASE_PATH=/srv/baromarket/market.sqlite3
   UPLOAD_DIR=/srv/baromarket/uploads
   APP_SECRET=여기에_위_명령의_출력값을_붙여넣기
   ALLOWED_HOSTS=market.example.com
   ALLOWED_ORIGINS=https://market.example.com
   SECURE_COOKIES=true
   SESSION_TTL_SECONDS=28800
   SESSION_IDLE_SECONDS=1800
   REPORT_THRESHOLD=3
   DEBUG=false
   ```

3. 프록시와 같은 호스트의 loopback에서 애플리케이션을 단일 worker로 실행합니다.

   ```bash
   .venv/bin/uvicorn app.main:app \
     --host 127.0.0.1 \
     --port 8000 \
     --workers 1 \
     --no-server-header \
     --ws-per-message-deflate false \
     --ws-max-size 4096 \
     --ws-max-queue 16 \
     --limit-concurrency 100 \
     --proxy-headers \
     --forwarded-allow-ips 127.0.0.1
   ```

4. 프록시에서 HTTPS 인증서, HSTS 정책, 요청 크기 제한, WebSocket upgrade, 접근 로그 보호와 백업 권한을 검토합니다.

production에서는 다음 조건 중 하나라도 맞지 않으면 애플리케이션이 fail-closed로 시작을 거부합니다.

- `APP_SECRET`이 없거나 약함
- `SECURE_COOKIES`가 `false`
- `ALLOWED_HOSTS` 또는 `ALLOWED_ORIGINS`를 명시하지 않음
- Origin이 HTTPS가 아님
- `DEBUG=true` 또는 wildcard Host 사용

## 환경변수

애플리케이션은 저장소 루트의 `.env`를 읽고, 같은 이름의 프로세스 환경변수가 있으면 프로세스 값을 우선합니다.

| 변수 | 개발 예시/기본값 | 설명 |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test`, `production` 중 하나 |
| `APP_HOST` | `127.0.0.1` | `.env.example`의 실행 참고값. 현재 바인딩 주소는 Uvicorn `--host`가 결정 |
| `APP_PORT` | `8000` | `.env.example`의 실행 참고값. 현재 포트는 Uvicorn `--port`가 결정 |
| `DATABASE_PATH` | `app/data/market.sqlite3` | SQLite 파일. 상대 경로는 저장소 루트 기준, 운영은 별도 절대 경로 권장 |
| `UPLOAD_DIR` | `app/data/uploads` | 재인코딩된 상품 이미지 저장 위치. 웹 정적 파일·소스와 분리 권장 |
| `APP_SECRET` | 개발에서는 빈 값 허용 | IP 지문 등 애플리케이션 보안 비밀. production에서는 예측 불가능한 32바이트 이상 값 필수 |
| `ALLOWED_HOSTS` | `127.0.0.1,localhost` | 쉼표 구분 Host 허용 목록. 스킴·경로·포트 금지 |
| `ALLOWED_ORIGINS` | 로컬 HTTP Origin 2개 | 쉼표 구분 정확한 Origin. production에서는 HTTPS만 허용 |
| `SECURE_COOKIES` | `false` | HTTPS 운영에서는 반드시 `true` |
| `SESSION_TTL_SECONDS` | `28800` | 세션 절대 만료. 300초~7일 |
| `SESSION_IDLE_SECONDS` | `1800` | 유휴 만료. 60초 이상이며 절대 만료 이하 |
| `REPORT_THRESHOLD` | `3` | 가입 후 10분이 지난 서로 다른 신고자가 임계값에 도달하면 자동 격리. 2~20 |
| `DEBUG` | `false` | 상세 개발 동작 플래그. production에서는 `false` 필수 |

`.env`에는 셸 확장이나 명령 실행 기능이 없습니다. 비밀 생성 명령의 결과만 넣고 `.env` 자체는 커밋하지 마세요.

## 테스트와 보안 검사

개발 의존성을 설치한 뒤 저장소 루트에서 실행합니다.

```bash
# 기능·보안 회귀 테스트와 branch coverage
.venv/bin/python -m pytest -q

# 정적 품질 검사
.venv/bin/ruff check .
.venv/bin/ruff format --check .

# Python 보안 패턴 검사
.venv/bin/bandit -q -r app

# 설치된 개발 가상환경 전체의 알려진 취약점 확인
.venv/bin/pip-audit --local --strict

# 선택: CI처럼 개발 requirements를 새로 해석하거나 runtime만 별도로 검사
.venv/bin/pip-audit -r requirements-dev.txt --strict
.venv/bin/pip-audit -r requirements.txt
```

2026-07-22 기준 검증 결과:

| 검사 | 결과 |
|---|---:|
| Pytest | **36 passed** |
| 전체 애플리케이션 coverage | **73%** (branch 포함) |
| Ruff | 오류 0, 26개 Python 파일 포맷 일치 |
| Bandit | 보안 이슈 0 |
| pip-audit | 알려진 취약점 0 |

의존성 취약점 DB와 코드는 계속 바뀝니다. 위 수치는 영구 보증이 아니므로 제출·배포 직전에 다시 실행하세요. `pip-audit`은 최신 취약점 조회를 위해 네트워크가 필요합니다.

같은 품질 게이트는 [GitHub Actions CI](https://github.com/SoliShim/whs-secure-coding/actions/workflows/ci.yml)에서 `main` push, pull request, 수동 실행마다 수행합니다. CI는 Ruff lint/format, Pytest, Bandit, 개발 의존성 strict `pip-audit`를 순서대로 검사합니다. Dependabot은 pip와 GitHub Actions 업데이트를 매주 확인합니다.

## 제출용 PDF 보고서 생성

공개 저장소에는 개인정보가 없는 보고서 생성기만 포함합니다. 개발 의존성을 설치한 뒤 아래 예시의 반, 이름, 전화번호 뒷자리만 본인 정보로 바꿔 실행합니다.

```bash
.venv/bin/python scripts/generate_report.py \
  --class-name 01반 \
  --name 홍길동 \
  --phone-last4 1234 \
  --repo-url https://github.com/SoliShim/whs-secure-coding
```

결과는 `output/pdf/[WHS][secure-coding][01반]홍길동(1234).pdf` 형식으로 생성되며 파일명의 공백은 자동으로 제거됩니다. 로컬 `output/screenshots/`에 검증 이미지가 있으면 보고서에 사용하고, 없으면 이미지 절만 생략합니다. 생성된 PDF와 스크린샷은 개인정보·로컬 검증 자료이므로 `.gitignore`로 추적하지 않습니다. 자세한 내용은 [보고서 생성 안내](report/README.md)를 참고하세요.

## 관리 명령

```bash
# 스키마와 불변성 트리거 준비
.venv/bin/python -m app.cli init-db

# 대화형 관리자 생성
.venv/bin/python -m app.cli create-admin

# 선택적 합성 계정·상품 생성
.venv/bin/python -m app.cli seed-demo

# 만료된 세션 정리
.venv/bin/python -m app.cli cleanup-sessions
```

## 디렉터리 구조

```text
whs-secure-coding/
├── .github/
│   ├── workflows/ci.yml   # push·PR·수동 실행 품질 게이트
│   └── dependabot.yml     # pip·GitHub Actions 주간 업데이트 확인
├── app/
│   ├── routers/           # 인증, 상품, 신고, 지갑, 채팅, 관리자 HTTP/WS 라우트
│   ├── templates/         # Jinja2 서버 렌더링 화면
│   ├── static/            # 로컬 CSS, JavaScript, 기본 이미지
│   ├── main.py            # FastAPI 팩토리, lifespan, 미들웨어, 보안 헤더
│   ├── config.py          # .env 로딩과 fail-closed 설정 검증
│   ├── schema.sql         # SQLite 제약, 인덱스, 불변성 트리거
│   ├── repository.py      # 바인딩 SQL과 도메인 트랜잭션
│   ├── security.py        # Argon2id, 토큰, CSRF, 입력·헤더 보조 함수
│   ├── request_limit.py   # Content-Length·chunked 6MB pure ASGI 제한
│   ├── chat.py            # 단일 worker WebSocket 연결 관리자
│   ├── images.py          # 이미지 검증·재인코딩
│   └── cli.py             # DB, 관리자, 데모, 세션 관리 명령
├── tests/                 # TestClient HTTP/WebSocket 보안 회귀 테스트
├── docs/                  # 요구사항, 설계, 위협 모델, 체크리스트, 유지보수, AI 활용
├── report/                # 제출용 PDF 생성 안내
├── scripts/
│   └── generate_report.py # 제출 규칙에 맞는 PDF 생성기
├── app/data/              # 기본 런타임 DB·업로드 위치; 생성 데이터는 Git 제외
├── output/
│   ├── pdf/               # 로컬 제출 PDF; 생성 파일은 Git 제외
│   └── screenshots/       # 로컬 검증 이미지; 생성 파일은 Git 제외
├── .env.example           # 비밀이 없는 설정 예시
├── requirements.txt       # 직접 런타임 의존성 버전 고정
├── requirements-dev.txt   # 테스트·정적·보안 검사·PDF 생성 도구
├── pyproject.toml         # 프로젝트 및 pytest/coverage/Ruff 설정
├── Makefile               # 자주 쓰는 로컬 명령 단축키
├── Dockerfile             # 운영 설정·HTTPS 프록시가 필요한 배포 기반
├── SECURITY.md            # 비공개 취약점 신고와 안전한 연구 정책
└── LICENSE                # MIT License
```

## 알려진 제한사항

- WebSocket 연결 관리자와 속도 제한이 메모리에 있으므로 **Uvicorn worker 1개만 지원**합니다. 현재 프로세스 안에서는 사용자당 3개·전체 80개 연결 상한을 적용하지만, 다중 worker/서버의 고부하는 검증하지 않았습니다. 확장하려면 Redis 같은 공유 저장소와 메시지 브로커가 필요합니다.
- SQLite는 단일 인스턴스·과제 시연 규모에 맞춘 선택입니다. 고가용성, 다중 지역, 대규모 동시 쓰기, 실제 금융 원장용이 아닙니다.
- 일반 회원의 초기 가상 잔액 `100,000원`은 `wallet_balance`에 직접 설정되고 genesis/funding 원장 행은 만들지 않습니다. 현재 원장은 사용자 간 이체가 시작된 뒤의 변동만 감사하므로 실제 회계 시스템으로 확장하려면 초기 지급도 원장에 기록해야 합니다.
- 로컬 rate limiter는 프로세스 재시작 시 초기화되고 분산 공격을 전역 집계하지 못합니다.
- 업로드 파일은 로컬 파일시스템에 저장합니다. 여러 서버에서는 전용 객체 저장소와 악성 파일 스캔, 보존 정책이 추가로 필요합니다.
- 신규 계정의 신고는 접수하되 가입 10분이 지나기 전에는 자동 격리 수에 넣지 않습니다. 그래도 장기 다계정 공모를 완전히 막지는 못하므로 관리자 검토와 출처별 안전 복구가 필요합니다.
- 개발용 HTTP/WS는 전송 기밀성을 제공하지 않습니다. 공개 운영은 반드시 HTTPS/WSS를 사용해야 합니다.
- 실제 HTTPS/WSS 역방향 프록시와 운영 백업·복구 리허설은 아직 수행하지 않았습니다.
- 직접 의존성 버전과 GitHub Actions commit SHA는 고정했지만, 간접 의존성 lock/hash와 Docker base-image digest 고정은 운영 공급망 하드닝 항목으로 남아 있습니다.
- 이 프로젝트는 교육용이며 실제 상거래·금융·개인정보 처리 서비스의 법률·가용성·감사 요구를 충족한다고 주장하지 않습니다.

## 개발 문서

- [요구사항 분석](docs/requirements.md)
- [시스템 설계](docs/design.md)
- [STRIDE 위협 모델](docs/threat-model.md)
- [시큐어 코딩 체크리스트](docs/security-checklist.md)
- [테스트 및 검증 결과](docs/test-results.md)
- [유지보수·CI·백업·사고 대응](docs/maintenance.md)
- [AI 도구 활용과 사람 검증](docs/ai-usage.md)

강의 PDF와 녹음 원본은 저작권과 제출 범위 때문에 public 저장소에 포함하지 않습니다.

## 라이선스

이 프로젝트는 [MIT License](LICENSE)로 배포됩니다.
