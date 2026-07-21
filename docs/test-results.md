# 테스트 및 검증 결과

## 1. 결론

2026-07-22 로컬 기준으로 자동 테스트 **36개가 모두 통과**했고, 애플리케이션 전체 branch coverage는 **73%**다. Ruff lint/format, Bandit, 현재 가상환경 대상 pip-audit도 모두 실패 없이 끝났다.

이 결과는 가입, 상품, 전체·1:1 WebSocket 채팅, 신고·자동 격리·복구, 가상 KRW 송금, 검색, 관리자 RBAC라는 7개 최소 요구사항의 핵심 흐름과 주요 보안 회귀를 확인한다. Content-Length/chunked 본문 제한, WebSocket 동시 연결·느린 수신자, 서로 다른 키와 같은 키의 병렬 송금도 자동 검증했다. public 저장소의 GitHub Actions에서도 동일 게이트가 성공했다. 실제 HTTPS/WSS 역방향 프록시와 단일 프로세스 범위를 넘는 고부하 멀티프로세스는 별도 검증이 필요하다.

## 2. 검증 환경과 재현 명령

### 2.1 환경 스냅샷

| 항목 | 값 |
|---|---|
| 검증 일자 | 2026-07-22 (Asia/Seoul) |
| OS | macOS 26.5.2, arm64 |
| Python | 3.12.13 |
| SQLite | 3.50.4 |
| FastAPI / Starlette | 0.139.2 / 1.3.1 |
| Jinja2 / Pillow | 3.1.6 / 12.3.0 |
| argon2-cffi | 25.1.0 |
| DB | 테스트마다 새 임시 SQLite DB |
| HTTP/WS 클라이언트 | FastAPI/Starlette `TestClient` |

### 2.2 실행 명령과 실제 결과

저장소 루트에서 다음을 실행했다.

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/bandit -q -r app -x tests
.venv/bin/pip-audit --cache-dir /tmp/whs-pip-audit-cache --local
```

| 검사 | 실제 결과 | 판정 |
|---|---:|---|
| Pytest | 36 passed, 2 warnings, 3.75s | 통과 |
| Coverage | 73%, branch 포함 | 정보 |
| Ruff lint | `All checks passed!` | 통과 |
| Ruff format | 26 files already formatted | 통과 |
| Bandit | 보안 finding 0, exit 0 | 통과 |
| pip-audit | `No known vulnerabilities found` | 통과(2026-07-22 DB 기준) |

pip-audit 결과는 실행 시점의 취약점 데이터와 설치된 환경에 대한 결과다. 이후 새 CVE가 공개될 수 있으므로 제출·배포 직전에 다시 실행해야 한다. requirements 파일을 대상으로 한 GitHub Actions strict 감사도 2026-07-22 원격 실행에서 성공했다.

### 2.3 경고 해석

Pytest의 두 경고는 실패가 아니다.

1. Starlette `TestClient`가 현재 `httpx` 호환 계층의 향후 변경을 알리는 deprecation 경고다. 런타임 서비스 보안 실패는 아니지만 Starlette와 테스트 의존성 갱신 시 회귀 테스트가 필요하다.
2. `app.main` 모듈 전역 앱이 개발 환경에서 `APP_SECRET` 미설정을 감지해 프로세스 수명 개발용 키를 생성했다는 경고다. 테스트 fixture는 별도 강한 테스트 비밀을 전달한다. production은 강한 `APP_SECRET`, HTTPS Origin, Secure cookie 등이 없으면 시작을 거부한다.

## 3. 자동 테스트 구성

### 3.1 파일별 개수

| 파일 | 개수 | 주 검증 범위 |
|---|---:|---|
| `tests/test_auth.py` | 6 | 가입·로그인·CSRF·Argon2id·쿠키·프로필 XSS·세션 폐기 |
| `tests/test_products_moderation.py` | 5 | 업로드·재인코딩·XSS·SQL injection·소유권·신고 격리 |
| `tests/test_security_websocket.py` | 8 | 보안 헤더·오류 은닉·WS 인증·동시 연결·느린 수신자·전체/DM |
| `tests/test_wallet_admin.py` | 7 | 송금 원자성·원장·순차/병렬 멱등·병렬 초과출금 방지·관리자 RBAC |
| `tests/test_hardening.py` | 10 | 익명 HMAC CSRF·Content-Length/chunked 6MB·과대 픽셀·신고 신뢰 대기·비밀번호 제한·제재 복구 |
| **합계** | **36** | 모든 테스트 통과 |

### 3.2 인증·세션·CSRF

| 테스트 | 공격/오류 조건 | 확인한 결과 |
|---|---|---|
| `test_registration_uses_argon2_and_stores_only_session_hash` | DB 또는 응답에서 비밀번호·세션 원문 노출 | 비밀번호는 Argon2id, 세션은 64자 해시만 저장 |
| `test_duplicate_registration_and_login_failure_are_safely_reported` | 중복 계정, 존재/부재 계정 로그인 차이 탐색 | 중복 1행 유지, 로그인 오류 메시지 일반화 |
| `test_csrf_and_origin_are_required_for_registration` | 토큰 누락·오류·악성 Origin | 400/403, 사용자 생성 없음; 정상 토큰만 성공 |
| `test_session_cookie_security_attributes` | 개발 쿠키 속성 누락 | `HttpOnly`, `SameSite=Lax`, `Path=/` 확인 |
| `test_secure_cookie_attribute_when_enabled` | HTTPS 설정에서 쿠키·HSTS 누락 | `Secure`, `HttpOnly`, `SameSite`, HSTS 확인 |
| `test_profile_update_escapes_output_and_password_change_revokes_all_sessions` | 저장형 XSS, 비밀번호 변경 후 탈취 세션 재사용 | HTML 이스케이프, 모든 세션 폐기, 이전 비밀번호 거부 |
| `test_public_browsing_does_not_create_database_sessions` | 익명 GET 반복으로 sessions 테이블 증가 | 공개 조회·가입 폼 모두 DB 세션 0행 |
| `test_tampered_anonymous_csrf_cookie_is_rejected` | HMAC 서명 익명 쿠키 1바이트 변조 | 가입 403, 상태 변경 없음 |
| `test_password_change_attempts_are_rate_limited` | 현재 비밀번호 해시 검증을 반복해 CPU 소모 | 사용자+IP별 15분 5회 뒤 여섯 번째 요청 429, 기존 자격증명 불변 |

### 3.3 상품·검색·업로드

| 테스트 | 공격/오류 조건 | 확인한 결과 |
|---|---|---|
| `test_product_upload_is_reencoded_and_user_content_is_escaped` | 사용자 파일명·메타데이터·저장형 XSS | 무작위 40-hex JPEG로 재인코딩, HTML 이스케이프, 정확한 MIME와 nosniff |
| `test_invalid_image_upload_is_rejected_without_orphan_or_database_row` | SVG 내부 스크립트 업로드 | 400, 상품 0행, 고아 파일 0개 |
| `test_search_treats_sql_syntax_as_literal_data` | `%' OR 1=1 --` 검색 | 결과 확대·500·DB 변조 없이 리터럴 처리 |
| `test_non_owner_cannot_edit_or_delete_product` | 다른 회원의 edit/delete 직접 요청 | GET/POST 403, 이름·상태·숨김 값 불변 |
| `test_declared_oversized_request_is_rejected_before_route_parsing` | 6MB 초과 `Content-Length` | 라우트/multipart 파싱 전 413, DB 세션 0행, 보안 헤더 유지 |
| `test_chunked_oversized_request_is_rejected_by_received_byte_limit` | Content-Length 없이 7MB chunked 전송 | 실제 수신 byte 누적 상한에서 413, 폼 라우트 미도달 |
| `test_compressed_oversized_image_dimensions_are_rejected` | 파일 바이트는 작지만 5000×5000인 압축 PNG | 해상도 오류 400, 상품 0행, 변환 JPEG 0개 |

Pillow의 실제 format·크기·픽셀을 디코딩 전에 먼저 확인하고, verify 후 다시 열어 로드한다. 최대 원본 변은 10,000px, 최대 픽셀은 20,000,000이며 공개 JPEG는 최대 1600px로 축소한다. 이미지 검증·재인코딩은 event loop가 아닌 worker thread에서 실행하고, 프로세스 전체 `CapacityLimiter(3)`을 공유한다. 사용자+IP별 이미지 처리 버킷(등록 12회/시간, 교체 최대 20회/시간)과 사용자별 활성 상품 100개 상한도 적용한다.

### 3.4 WebSocket 전체·1:1 채팅

| 테스트 | 공격/오류 조건 | 확인한 결과 |
|---|---|---|
| `test_websocket_rejects_unauthenticated_connection` | 세션 없는 연결 | close code 4401 |
| `test_websocket_rejects_untrusted_origin_even_with_valid_session` | 유효 세션+악성 Origin | close code 4403 |
| `test_websocket_requires_matching_csrf_auth_message` | 연결 후 잘못된 CSRF 인증 프레임 | close code 4401 |
| `test_websocket_limits_concurrent_connections_per_user` | 같은 사용자의 네 번째 동시 연결 | 첫 세 연결 인증 성공, 네 번째 4429 |
| `test_slow_websocket_does_not_block_or_break_fanout` | 한 수신자의 send가 timeout보다 지연 | 빠른 수신자는 정상 수신, 느린 연결만 제거 |
| `test_websocket_global_dm_nonce_deduplication_and_length_validation` | 전체/DM, 같은 nonce 재전송, 501자 | 두 사용자에게 올바르게 전달, 중복 저장 거부, 길이 거부, DB 2행 |

연결 전 IP 30회/분, 인증 후 사용자+IP 20회/분, 메시지 20회/10초, 사용자당 동시 3개, 프로세스 전체 80개 상한을 적용한다. 각 연결의 send는 lock으로 직렬화하고 2초 timeout을 두며, fan-out은 `asyncio.gather`로 병렬 처리한다. 같은 테스트 파일의 보안 헤더 및 일반 오류 테스트도 HTTP 공통 통제를 확인한다. 실제 TLS 프록시와 멀티프로세스 고부하는 이 단일 프로세스 자동 시험 범위 밖이다.

### 3.5 신고·자동 격리·관리자 복구

| 테스트 | 공격/오류 조건 | 확인한 결과 |
|---|---|---|
| `test_duplicate_report_is_rejected_and_three_unique_reporters_quarantine_product` | 한 신고자 중복, 고유 신고 3건 | 중복 409, 3건만 집계, 상품 숨김, 감사 이벤트 1건, 익명 상세 404 |
| `test_new_accounts_can_report_but_cannot_immediately_trigger_quarantine` | 가입 직후 세 계정으로 공모 신고 | 신고는 접수하되 `auto_eligible=0`, 자동 집계 0·상품 미격리 |
| `test_dismissal_releases_only_automatic_product_quarantine` | 자동 격리 신고 1건 기각 후 같은 신고 재처리 | 활성 신고 2건으로 재계산, 자동 격리 상품만 복구, 두 번째 처리는 409 |
| `test_dismissal_does_not_reverse_manual_product_moderation` | 관리자가 직접 숨긴 상품의 신고 기각 | `visibility_source=admin` 유지, 자동 복구가 수동 조치를 덮지 않음 |
| `test_dismissal_reactivates_only_auto_suspended_user` | 신고 3건 자동 정지 사용자, 신고 기각 | `status_source=auto_report`인 사용자만 활성 복구 |
| `test_only_admin_can_change_user_status_and_suspension_revokes_sessions` | 일반 사용자 직접 관리자 POST, 관리자 정지 | 일반 사용자는 403·상태 불변, 관리자 조치는 감사되고 기존 세션 폐기 |

자동제재 집계에는 신고자 계정 생성 후 10분 신뢰 대기를 적용하고 `auto_eligible`을 신고 시점에 기록한다. 이 통제는 즉석 Sybil 계정, 한 계정의 반복 신고, 자동/수동 상태 혼선을 줄인다. 미리 숙성시킨 여러 계정의 공모를 완전히 식별하지는 못하므로 자동 조치는 임시 격리이고 관리자 검토·복구가 필수다. 자동 신고 정지와 관리자 정지는 해당 사용자의 기존 DB 세션뿐 아니라 연결 중인 WebSocket도 서버에서 즉시 종료한다.

### 3.6 가상 KRW 송금·원장

| 테스트 | 공격/오류 조건 | 확인한 결과 |
|---|---|---|
| `test_transfer_moves_integer_balance_atomically_and_ledger_sums_to_zero` | 정상 25,000원 이체 | 잔액 75,000/125,000, transfer 1행, ledger 2행, delta 합 0 |
| `test_insufficient_balance_and_self_transfer_leave_no_partial_state` | 잔액 초과·자기 송금 | 400, 양쪽 잔액 불변, transfer/ledger 0행 |
| `test_transfer_idempotency_prevents_duplicate_debit` | 같은 멱등 키 순차 재전송 | 두 번째 요청은 중복 안내, transfer 1행·ledger 2행·한 번만 차감 |
| `test_parallel_distinct_transfers_cannot_overdraw_balance` | 10만원 잔액에서 서로 다른 두 수신자에게 7만원씩 barrier 병렬 이체 | 한 건 성공·한 건 잔액 부족, 잔액 3만원, transfer 1행, ledger 2행·합 0 |
| `test_parallel_same_idempotency_key_creates_one_transfer` | 같은 멱등 키로 동일 이체를 barrier 병렬 호출 | 한 결과는 신규·하나는 중복, 같은 transfer ID, transfer 1행·ledger 2행·한 번만 차감 |

송금 서비스는 `BEGIN IMMEDIATE`, 조건부 차감, 입금, transfer, ledger 2행, 감사를 한 트랜잭션에서 처리하고 `(sender_id,idempotency_key)` 고유 제약을 둔다. 두 worker thread를 barrier로 동시에 시작한 두 경쟁 시나리오에서 초과출금과 중복 원장이 생기지 않았다.

### 3.7 관리자·공통 오류

| 테스트 | 공격/오류 조건 | 확인한 결과 |
|---|---|---|
| `test_admin_dashboard_is_forbidden_to_user_and_available_to_admin` | 일반 사용자의 관리자 URL 직접 접근 | 일반 사용자 403, 관리자 200 |
| `test_security_headers_are_present_on_normal_response` | 기본 헤더 누락 | CSP, nosniff, DENY, no-referrer, no-store, 요청 ID 확인 |
| `test_unhandled_error_response_does_not_disclose_exception_or_traceback` | SQL·해시·서버 경로를 포함한 내부 예외 | 500은 일반 메시지+요청 ID만 제공, 내부 문자열·Traceback 비노출 |

## 4. 최소 요구사항 추적 결과

| 강의 최소 요구사항 | 구현 위치 | 자동 증거 | 현재 판정 |
|---|---|---|---|
| 플랫폼 가입 | `routers/auth.py`, `security.py`, `web.py` | 인증·세션·CSRF·비밀번호 변경 제한 테스트 9개 | 구현 및 핵심 자동 검증 완료 |
| 상품 등록·열람 | `routers/products.py`, `images.py` | 등록·재인코딩·XSS·소유권·오류 테스트 | 구현 및 핵심 자동 검증 완료; 전체 경계표 남음 |
| 사용자 소통 | `routers/chat.py`, `chat.py`, `chat.js` | 전체/DM, 사용자당 연결 cap, 느린 수신자 timeout·병렬 fan-out, WS 인증 테스트 | 구현 및 핵심 자동 검증 완료; 실제 TLS 프록시는 별도 |
| 악성 사용자·상품 차단 | `moderation.py`, `repository.py`, 관리자 화면 | 중복·10분 신뢰 대기·임계값·감사·재처리 409·자동/수동 복구 테스트 | 구현 및 핵심 자동 검증 완료; 숙성 Sybil은 잔여 위험 |
| 사용자 간 송금 | `wallet.py`, `transfer_funds`, 원장 테이블 | 정상·실패·순차 멱등·서로 다른 키 병렬 초과출금·동일 키 병렬 멱등 테스트 | 구현 및 병렬 핵심 자동 검증 완료 |
| 상품 검색 | `products.home`, 바인딩 SQL | SQL 구문 모양 입력 회귀 테스트 | 구현 및 인젝션 자동 검증 완료; 검색 경계·수동 UX 남음 |
| 관리자 전체 관리 | `routers/admin.py`, 관리자 대시보드 | RBAC·직접 POST·감사·정지 세션 폐기·복구 테스트 | 구현 및 핵심 자동 검증 완료 |

## 5. 보안 약점 발견과 회귀 증거

개발 중 확인해 수정한 대표 약점과 현재 회귀 증거는 다음과 같다.

| 확인한 약점 | 가능한 영향 | 변경 | 회귀 증거 |
|---|---|---|---|
| 로그인/가입 전 CSRF를 위해 익명 DB 세션을 계속 만들 수 있음 | 공개 GET 반복으로 DB 증가·저장소 DoS | DB 쓰기 없는 만료형 HMAC 서명 익명 쿠키 | 공개 조회 후 DB 세션 0행, 변조 쿠키 403 |
| multipart 전체 크기의 애플리케이션 상한 부재 | 선언 길이 생략/chunked로 우회해 메모리·디스크 소모 | form parser 바깥 pure ASGI에서 `Content-Length`와 실제 chunk 합 6MB 이중 제한 | 초과 선언·7MB chunked 모두 413, 라우트 미도달 |
| 자동 신고 제재와 관리자 수동 제재 출처가 같음 | 신고 기각 시 수동 조치까지 잘못 복구 | `status_source`/`visibility_source`로 `auto_report`와 `admin` 분리 | 자동 상품/사용자만 복구, 수동 상품 숨김 유지 |
| 가입 직후 다계정 신고가 즉시 자동제재에 기여 | 낮은 비용의 즉석 Sybil로 상품·사용자 격리 | 신고는 저장하되 계정 생성 10분 후 신고만 `auto_eligible=1`로 자동 집계 | 신규 세 계정 신고 후 report_count 0, 상품 공개 유지 |
| 이미 처리한 신고를 다시 처리 가능 | 감사 상태와 집계가 반복 변경될 가능성 | pending 상태만 처리하고 나머지는 409 | 기각 뒤 재처리 요청 409 |
| 사용자 파일명을 그대로 신뢰할 가능성 | traversal·덮어쓰기·활성 콘텐츠 제공 | 실제 이미지 파싱 후 메타데이터 제거 JPEG 재인코딩, 무작위 이름 | 40-hex JPEG, SVG 거부, 고아 파일 없음 |
| 압축 이미지의 픽셀 수를 늦게 검사하거나 event loop에서 처리 | 압축폭탄 메모리·CPU 고갈, 다른 async 요청 지연 | 헤더 단계 크기·픽셀 선검사, Pillow 경고 오류화, 최대 1600px 정규화, worker thread 격리 | 5000×5000 압축 PNG 400·상품/파일 없음 |
| Argon2 verify/hash 반복을 async event loop에서 직접 실행 | CPU 고갈과 전체 요청 지연 | 비밀번호 변경 5회/15분 사용자+IP 제한, verify/hash worker thread 격리 | 여섯 번째 변경 시도 429·자격증명 불변 |
| async WebSocket/HTTP 경로에서 동기 SQLite 호출 | event loop 정지로 모든 연결 지연 | 세션·채팅·상품·신고·관리자 DB 작업을 worker thread로 오프로딩 | 36개 통합 테스트 통과; 멀티프로세스 고부하는 별도 |
| WebSocket 연결 폭주와 느린 수신자 | 메모리 고갈, 한 클라이언트가 전체 broadcast 지연 | pre-auth IP 30/분, 사용자 3·전체 80 연결 cap, send lock·2초 timeout·병렬 fan-out | 네 번째 연결 4429, 느린 연결 제거 후 빠른 수신자 정상 |
| 이미지·상품 무제한 생성 | CPU·메모리·디스크 고갈 | 전역 이미지 동시 3개, 사용자+IP 요청 제한, 활성 상품 100개 quota | 코드·경계 정책 확인; 고부하 포화 시험은 별도 |
| 상품·프로필의 사용자 HTML | 저장형 XSS, 특히 관리자 세션 영향 | Jinja2 자동 이스케이프, `safe` 미사용, CSP | 상품·프로필 payload가 문자 데이터로만 표시 |
| 로그인 오류 차이와 세션 원문 저장 | 계정 열거·DB 유출 시 세션 재사용 | 일반 오류, 더미 해시, 세션 SHA-256 해시 | 존재/부재 동일 오류, DB 원문 불일치 |
| 송금 재전송 | 이중 출금·원장 불일치 | 송신자별 멱등 키, 단일 트랜잭션, 조건부 차감 | 재전송에도 한 번만 차감, 원장 합 0 |
| 신고 해제 시 단순 카운터 감소/무조건 복구 | 집계 불일치·관리자 결정 덮어쓰기 | 활성 신고 재계산, 출처 조건부 복구 | report_count 2로 재계산, 수동 조치 보존 |

## 6. 자동 검증이 증명하지 않는 것

- coverage 73%는 미실행 분기가 있음을 명시적으로 보여 준다. 숫자가 높다는 이유만으로 보안 완전성을 주장하지 않는다.
- TestClient의 WebSocket 성공은 실제 프록시의 upgrade, TLS 인증서와 WSS를 증명하지 않는다.
- 두 thread의 SQLite 경쟁 테스트는 실제 다중 프로세스·다중 서버 부하를 대신하지 않는다.
- 정적 Bandit 0건은 논리적 권한 오류, 비즈니스 로직 남용, 새로운 공격 기법까지 탐지하지 않는다.
- pip-audit 0건은 해당 시점 알려진 취약점 DB의 결과이며, 설정 오류나 알려지지 않은 취약점을 배제하지 않는다.
- 신고자 10분 신뢰 대기는 즉석 Sybil을 줄이지만, 미리 만든 다계정 공모를 막지 못한다.
- 메모리 rate limiter는 재시작 시 초기화되고 단일 worker 밖에서 공유되지 않는다.
- 백업·복구는 유지보수 계획이며, 실제 운영 환경이 생기면 리허설 수치로 RPO/RTO를 별도 갱신한다.

## 7. 제출·공개 전 검증 게이트

다음 항목은 자동 결과와 분리해 실제 수행 후 날짜·환경·증거를 추가한다.

| 항목 | 기대 결과 | 현재 상태 |
|---|---|---|
| HTTPS/WSS | 유효 TLS, `Secure` 쿠키, HSTS, `wss://`, 정확한 Origin | 미배포 |
| 고부하 멀티프로세스 | 공유 rate limit·연결 상태·메시지 전달·DB 쓰기 부하 | 단일 worker 범위 밖 |
| GitHub Actions | lint, format, pytest, Bandit, pip-audit 모두 성공 | 통과(2026-07-22) |

완료된 자동 검증과 남은 수동 검증의 세부 체크 상태는 [security-checklist.md](security-checklist.md), 유지보수·복구 절차는 [maintenance.md](maintenance.md)를 따른다.
