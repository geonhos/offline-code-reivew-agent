# Python 코딩 가이드라인

## 네이밍 규칙

### 변수명
- 변수명은 snake_case를 사용한다.
- 의미를 알 수 없는 한 글자 변수명은 금지한다. (예외: 루프 카운터 `i`, `j`, `k`)
- 불리언 변수는 `is_`, `has_`, `can_` 접두사를 사용한다.
- 상수는 UPPER_SNAKE_CASE를 사용한다.

```python
# Good
user_name = "홍길동"
is_active = True
MAX_RETRY_COUNT = 3

# Bad
userName = "홍길동"
a = True
max = 3
```

### 함수명
- 함수명은 snake_case를 사용하며 동사로 시작한다.
- 함수명은 기능을 명확히 설명해야 한다.
- private 함수는 `_` 접두사를 붙인다.

```python
# Good
def get_user_by_id(user_id: int) -> User:
def calculate_total_price(items: list[Item]) -> Decimal:
def _validate_input(data: dict) -> bool:

# Bad
def user(id):
def process(data):
```

### 클래스명
- 클래스명은 PascalCase를 사용한다.
- 약어는 대문자로 유지한다. (예: `HTTPClient`, `SQLParser`)

## 에러 처리

### 예외 사용 원칙
- 빈 except 절(`except:`)은 금지한다. 반드시 구체적인 예외 타입을 명시한다.
- 예외를 삼키지 않는다. 최소한 로깅을 남긴다.
- 비즈니스 로직용 커스텀 예외를 정의하여 사용한다.

```python
# Good
try:
    result = api_client.fetch(url)
except httpx.TimeoutException:
    logger.warning("API 호출 타임아웃: %s", url)
    raise ServiceUnavailableError(f"외부 API 타임아웃: {url}")
except httpx.HTTPStatusError as e:
    logger.error("API 오류 응답: %s %s", e.response.status_code, url)
    raise

# Bad
try:
    result = api_client.fetch(url)
except:
    pass
```

### 리소스 정리
- 파일, DB 연결, 네트워크 소켓 등의 리소스는 반드시 `with` 문 또는 `try/finally`로 정리한다.

```python
# Good
with open("data.json") as f:
    data = json.load(f)

# Bad
f = open("data.json")
data = json.load(f)
# f.close()가 누락될 수 있음
```

## 보안

### SQL 인젝션 방지
- 문자열 포매팅으로 SQL 쿼리를 만들지 않는다. 반드시 파라미터 바인딩을 사용한다.

```python
# Good
cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))

# Bad
cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
```

### 민감 정보 관리
- 비밀번호, API 키, 토큰 등 민감 정보를 코드에 하드코딩하지 않는다.
- 환경 변수 또는 시크릿 매니저를 사용한다.
- `.env` 파일은 반드시 `.gitignore`에 포함한다.

```python
# Good
import os
api_key = os.environ["API_KEY"]

# Bad
api_key = "sk-1234567890abcdef"
```

### 입력값 검증
- 외부 입력(사용자 입력, API 파라미터)은 항상 검증한다.
- Pydantic 모델을 활용하여 타입과 제약 조건을 선언적으로 검증한다.

## 성능

### 데이터베이스
- N+1 쿼리 문제를 주의한다. 반복문 안에서 DB 쿼리를 호출하지 않는다.
- 대량 데이터 처리 시 페이지네이션 또는 커서 기반 조회를 사용한다.
- 인덱스가 필요한 컬럼에는 반드시 인덱스를 생성한다.

```python
# Good - 한 번의 쿼리로 필요한 데이터를 가져옴
users = User.objects.filter(id__in=user_ids).select_related("profile")

# Bad - N+1 문제
for user_id in user_ids:
    user = User.objects.get(id=user_id)
    print(user.profile.name)
```

### 비동기 처리
- I/O 바운드 작업(API 호출, DB 쿼리)은 비동기로 처리한다.
- CPU 바운드 작업은 별도 스레드/프로세스 풀을 사용한다.

## 코드 구조

### 함수 크기
- 하나의 함수는 하나의 책임만 가진다.
- 함수 길이는 30줄을 넘지 않도록 한다. 넘어가면 분리를 고려한다.

### Import 순서
- 표준 라이브러리 → 서드파티 → 로컬 모듈 순으로 정렬한다.
- 각 그룹 사이에 빈 줄을 넣는다.

```python
# Good
import os
import sys

import httpx
from fastapi import FastAPI

from src.config import settings
from src.models import User
```

### 타입 힌트
- 함수의 파라미터와 반환 타입에 타입 힌트를 명시한다.
- `Any` 타입은 가급적 사용하지 않는다.
