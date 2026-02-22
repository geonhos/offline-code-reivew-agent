# Java 코딩 가이드라인

## 네이밍 규칙

### 변수명
- 변수명은 camelCase를 사용한다.
- 의미를 알 수 없는 한 글자 변수명은 금지한다. (예외: 루프 카운터 `i`, `j`, `k`)
- 불리언 변수는 `is`, `has`, `can` 접두사를 사용한다.
- 상수는 `static final`로 선언하며 UPPER_SNAKE_CASE를 사용한다.

```java
// Good
String userName = "홍길동";
boolean isActive = true;
static final int MAX_RETRY_COUNT = 3;

// Bad
String UserName = "홍길동";
boolean flag = true;
int max = 3;
```

### 메서드명
- 메서드명은 camelCase를 사용하며 동사로 시작한다.
- 반환 타입이 boolean이면 `is`, `has`, `can` 접두사를 사용한다.
- getter/setter는 JavaBeans 규약을 따른다.

```java
// Good
public User findUserById(Long userId) { ... }
public BigDecimal calculateTotalPrice(List<Item> items) { ... }
public boolean isExpired() { ... }

// Bad
public User user(Long id) { ... }
public void process(Object data) { ... }
```

### 클래스명
- 클래스명은 PascalCase를 사용한다.
- 인터페이스에 `I` 접두사를 붙이지 않는다. 구현체에 `Impl` 접미사를 사용한다.
- 약어도 PascalCase 규칙을 따른다. (예: `HttpClient`, `SqlParser`)

```java
// Good
public class UserService { ... }
public interface UserRepository { ... }
public class UserRepositoryImpl implements UserRepository { ... }

// Bad
public class IUserRepository { ... }
public class HTTPClient { ... }
```

### 패키지명
- 패키지명은 모두 소문자로 작성한다.
- 단어 구분에 밑줄이나 대문자를 사용하지 않는다.

```java
// Good
package com.example.userservice;

// Bad
package com.example.userService;
package com.example.user_service;
```

## 에러 처리

### 예외 사용 원칙
- `Exception`이나 `Throwable`을 직접 catch하지 않는다. 구체적인 예외 타입을 명시한다.
- 예외를 삼키지 않는다. 최소한 로깅을 남긴다.
- 비즈니스 로직용 커스텀 예외를 정의하여 사용한다.
- Checked Exception보다 Unchecked Exception(RuntimeException)을 권장한다.

```java
// Good
try {
    return objectMapper.readValue(json, User.class);
} catch (JsonProcessingException e) {
    log.error("JSON 파싱 실패: {}", json, e);
    throw new InvalidRequestException("잘못된 JSON 형식", e);
}

// Bad
try {
    return objectMapper.readValue(json, User.class);
} catch (Exception e) {
    // 아무 처리 없음
}
```

### 리소스 정리
- `Closeable`/`AutoCloseable` 리소스는 반드시 try-with-resources로 관리한다.
- `finally` 블록에서 직접 `close()`를 호출하지 않는다.

```java
// Good
try (var conn = dataSource.getConnection();
     var stmt = conn.prepareStatement(sql)) {
    stmt.setLong(1, userId);
    return stmt.executeQuery();
}

// Bad
Connection conn = null;
try {
    conn = dataSource.getConnection();
    // ...
} finally {
    if (conn != null) conn.close();  // 예외 발생 가능
}
```

### Null 처리
- `null`을 반환하는 대신 `Optional`을 사용한다.
- `Optional`을 필드나 메서드 파라미터로 사용하지 않는다. 반환 타입에만 사용한다.
- `Optional.get()`을 직접 호출하지 않는다. `orElse`, `orElseThrow` 등을 사용한다.

```java
// Good
public Optional<User> findByEmail(String email) { ... }
User user = userRepository.findByEmail(email)
    .orElseThrow(() -> new UserNotFoundException(email));

// Bad
public User findByEmail(String email) {
    // null 반환 가능
}
```

## 보안

### SQL 인젝션 방지
- 문자열 연결로 SQL 쿼리를 만들지 않는다. PreparedStatement 또는 JPA/QueryDSL을 사용한다.

```java
// Good
PreparedStatement stmt = conn.prepareStatement("SELECT * FROM users WHERE id = ?");
stmt.setLong(1, userId);

// Bad
Statement stmt = conn.createStatement();
stmt.executeQuery("SELECT * FROM users WHERE id = " + userId);
```

### 민감 정보 관리
- 비밀번호, API 키, 토큰 등 민감 정보를 코드에 하드코딩하지 않는다.
- `application.yml`의 민감 값은 환경 변수로 주입한다.
- `application.yml`을 Git에 포함할 경우 민감 값은 `${ENV_VAR}` 플레이스홀더를 사용한다.

```yaml
# Good
spring:
  datasource:
    password: ${DB_PASSWORD}

# Bad
spring:
  datasource:
    password: mypassword123
```

### 입력값 검증
- 외부 입력(API 파라미터, 요청 본문)은 항상 검증한다.
- Bean Validation(`@Valid`, `@NotNull`, `@Size` 등)을 활용한다.
- 검증 로직을 컨트롤러 레이어에서 처리한다.

```java
// Good
@PostMapping("/users")
public ResponseEntity<User> createUser(@Valid @RequestBody CreateUserRequest request) {
    return ResponseEntity.ok(userService.create(request));
}

public record CreateUserRequest(
    @NotBlank String name,
    @Email String email,
    @Size(min = 8, max = 100) String password
) {}
```

## 성능

### 데이터베이스
- N+1 쿼리 문제를 주의한다. `@EntityGraph` 또는 `fetch join`을 사용한다.
- 대량 데이터 처리 시 페이지네이션을 사용한다.
- 읽기 전용 쿼리에는 `@Transactional(readOnly = true)`를 설정한다.

```java
// Good - fetch join으로 N+1 방지
@Query("SELECT u FROM User u JOIN FETCH u.profile WHERE u.id IN :ids")
List<User> findAllWithProfile(@Param("ids") List<Long> ids);

// Bad - N+1 문제
List<User> users = userRepository.findAllById(ids);
users.forEach(u -> System.out.println(u.getProfile().getName()));
```

### 컬렉션 처리
- 대량 컬렉션에 Stream API를 활용하되, 불필요한 중간 연산을 줄인다.
- `parallelStream()`은 CPU 바운드 작업에서만 사용하고, I/O 작업에는 사용하지 않는다.
- 빈 컬렉션 반환 시 `null` 대신 `Collections.emptyList()` 또는 `List.of()`를 사용한다.

```java
// Good
List<String> names = users.stream()
    .filter(User::isActive)
    .map(User::getName)
    .toList();

// Bad
List<String> names = new ArrayList<>();
for (User user : users) {
    if (user.isActive()) {
        names.add(user.getName());
    }
}
```

## 코드 구조

### 메서드 크기
- 하나의 메서드는 하나의 책임만 가진다.
- 메서드 길이는 30줄을 넘지 않도록 한다. 넘어가면 분리를 고려한다.
- 메서드 파라미터는 3개 이하를 권장한다. 초과 시 DTO로 묶는다.

### 클래스 구조
- 클래스 내 멤버 순서: 상수 → 필드 → 생성자 → public 메서드 → private 메서드.
- 하나의 클래스는 하나의 책임만 가진다 (SRP).

### Import
- 와일드카드 import(`import java.util.*`)는 사용하지 않는다.
- 사용하지 않는 import는 제거한다.
- IDE의 자동 정리 기능을 활용한다.

```java
// Good
import java.util.List;
import java.util.Optional;

// Bad
import java.util.*;
```

### 로깅
- `System.out.println()`을 사용하지 않는다. SLF4J 로거를 사용한다.
- 로그 메시지에 문자열 연결 대신 플레이스홀더를 사용한다.
- 로그 레벨을 적절히 사용한다. (ERROR: 즉시 대응 필요, WARN: 주의, INFO: 주요 흐름, DEBUG: 디버깅)

```java
// Good
private static final Logger log = LoggerFactory.getLogger(UserService.class);
log.info("사용자 생성 완료: userId={}", user.getId());

// Bad
System.out.println("사용자 생성 완료: " + user.getId());
```
