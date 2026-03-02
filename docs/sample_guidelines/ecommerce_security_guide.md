# E-Commerce 마이크로서비스 보안 및 아키텍처 가이드라인

## 1. 인증 및 JWT 관리

### JWT 시크릿 관리
- JWT 서명 키를 소스 코드나 설정 파일에 하드코딩하지 않는다.
- 반드시 환경 변수(`${JWT_SECRET}`)나 Vault 등 시크릿 관리 도구를 사용한다.
- JWT 서명 키는 최소 256bit(32바이트) 이상의 랜덤 값을 사용한다.
- `HS256` 대신 `RS256`(비대칭 키)를 권장한다.

```java
// Good — 환경 변수에서 시크릿 로드
@Value("${jwt.secret}")
private String secretKey;  // 환경 변수로 주입

// Bad — 하드코딩
private static final String SECRET_KEY = "mySecretKey123";
```

### 비밀번호 해싱
- `MD5`, `SHA-1`, `SHA-256` 등 단방향 해시 함수를 비밀번호 해싱에 사용하지 않는다.
- 반드시 `bcrypt`, `scrypt`, 또는 `argon2`를 사용한다.
- Spring Security의 `BCryptPasswordEncoder`를 표준으로 사용한다.

```java
// Good — BCrypt 사용
@Bean
public PasswordEncoder passwordEncoder() {
    return new BCryptPasswordEncoder();
}

String hashed = passwordEncoder.encode(rawPassword);

// Bad — MD5 사용
MessageDigest md = MessageDigest.getInstance("MD5");
byte[] digest = md.digest(password.getBytes());
```

## 2. CORS 및 보안 설정

### CORS 허용 도메인
- `allowedOrigins`에 와일드카드(`*`)를 사용하지 않는다.
- 운영 환경에서는 정확한 도메인을 명시한다.
- `credentials: true`와 `origins: *`는 함께 사용할 수 없다.

```java
// Good — 도메인 명시
config.setAllowedOrigins(List.of("https://www.myshop.com", "https://admin.myshop.com"));

// Bad — 와일드카드
config.setAllowedOrigins(List.of("*"));
```

### application.yml 보안
- 데이터베이스 비밀번호, API 키, SMTP 비밀번호 등 민감 정보는 환경 변수로 주입한다.
- `spring.jpa.hibernate.ddl-auto`는 운영 환경에서 `none` 또는 `validate`로 설정한다.

```yaml
# Good
spring:
  datasource:
    password: ${DB_PASSWORD}
  mail:
    password: ${SMTP_PASSWORD}

# Bad
spring:
  datasource:
    password: root1234
  mail:
    password: gmail_app_password_123
```

## 3. 민감 정보 로깅 금지

### 카드번호 / 비밀번호 마스킹
- 신용카드 번호, CVV, 비밀번호 등을 로그에 그대로 출력하지 않는다.
- 마스킹 처리하여 앞 6자리 + 뒤 4자리만 표시하거나, 해시값을 사용한다.
- PCI DSS 규정에 따라 카드번호 전체 로깅은 금지된다.

```java
// Good — 마스킹
log.info("결제 처리: orderId={}, card=****{}", orderId, cardNumber.substring(cardNumber.length() - 4));

// Bad — 카드번호 전체 로깅
log.info("결제 처리: orderId={}, cardNumber={}", orderId, cardNumber);
```

### 에러 메시지 노출 금지
- 내부 예외 메시지를 클라이언트에 그대로 반환하지 않는다.
- `e.getMessage()`를 API 응답에 포함하지 않는다.
- 사용자에게는 일반적인 오류 메시지를, 로그에는 상세 정보를 기록한다.

```java
// Good
@ExceptionHandler(RuntimeException.class)
public ResponseEntity<?> handle(RuntimeException e) {
    log.error("내부 오류", e);
    return ResponseEntity.status(500).body(Map.of("error", "서버 오류가 발생했습니다."));
}

// Bad — 내부 정보 노출
@ExceptionHandler(RuntimeException.class)
public ResponseEntity<?> handle(RuntimeException e) {
    return ResponseEntity.status(500).body(Map.of("error", e.getMessage()));
}
```

## 4. TLS 인증서 검증

### TLS 검증 필수
- 외부 API 호출 시 TLS 인증서 검증을 비활성화하지 않는다.
- `TrustManager`를 오버라이드하여 모든 인증서를 허용하는 코드는 금지한다.
- 자체 서명 인증서가 필요한 경우 신뢰 저장소(truststore)에 등록한다.

```java
// Bad — 모든 인증서 신뢰 (MITM 공격 취약)
TrustManager[] trustAllCerts = new TrustManager[]{
    new X509TrustManager() {
        public void checkServerTrusted(X509Certificate[] certs, String authType) {}
        // ...
    }
};
SSLContext.getInstance("SSL").init(null, trustAllCerts, new SecureRandom());
```

## 5. SQL Injection 방지

### 파라미터 바인딩 필수
- JPQL/SQL 쿼리에 문자열 연결로 사용자 입력을 포함하지 않는다.
- 반드시 파라미터 바인딩(`:param`, `?1`)을 사용한다.

```java
// Good — 파라미터 바인딩
@Query("SELECT p FROM Product p WHERE p.name LIKE %:keyword%")
List<Product> searchByName(@Param("keyword") String keyword);

// Bad — 문자열 연결 (SQL Injection)
String jpql = "SELECT p FROM Product p WHERE p.name LIKE '%" + keyword + "%'";
entityManager.createQuery(jpql, Product.class).getResultList();
```

## 6. 입력 검증

### Bean Validation
- Controller의 `@RequestBody`에는 반드시 `@Valid`를 추가한다.
- 가격, 수량 등 숫자 필드에는 `@Min(0)`, `@Positive` 등 범위 검증을 추가한다.
- 이메일 필드에는 `@Email` 어노테이션을 사용한다.

```java
// Good
@PostMapping
public ResponseEntity<?> createOrder(@Valid @RequestBody CreateOrderRequest request) { ... }

public class ProductCreateRequest {
    @NotBlank private String name;
    @Positive private BigDecimal price;  // 양수만 허용
    @Min(0) private int stock;
}

public class UserRegisterRequest {
    @Email private String email;
    @NotBlank @Size(min = 8) private String password;
}

// Bad — 검증 없음
@PostMapping
public ResponseEntity<?> createOrder(@RequestBody CreateOrderRequest request) { ... }
```

## 7. 예외 처리

### 커스텀 예외 사용
- `RuntimeException`을 직접 throw하지 않는다. 비즈니스 예외를 정의한다.
- `e.printStackTrace()`를 사용하지 않는다. SLF4J `log.error("메시지", e)`를 사용한다.
- `catch(Exception e)` 포괄 처리를 피하고, 구체적 예외를 구분하여 처리한다.

```java
// Good
throw new OrderNotFoundException(orderId);
log.error("결제 처리 실패: orderId={}", orderId, e);

// Bad
throw new RuntimeException("주문을 찾을 수 없습니다.");
e.printStackTrace();
catch (Exception e) { log.error("실패", e); }
```

## 8. 성능

### N+1 쿼리 방지
- 루프 내에서 개별 엔티티를 조회하지 않는다.
- `@EntityGraph` 또는 JPQL `JOIN FETCH`로 연관 엔티티를 한 번에 로딩한다.

```java
// Good — JOIN FETCH
@Query("SELECT p FROM Product p JOIN FETCH p.reviews WHERE p.id IN :ids")
List<Product> findAllWithReviewsByIdIn(@Param("ids") List<Long> ids);

// Bad — N+1
for (Long id : productIds) {
    Product p = productRepository.findById(id).orElse(null);
    var reviews = reviewRepository.findByProductId(id);  // N+1
}
```

### findAll() 메모리 필터링 금지
- `findAll()`로 전체 조회 후 스트림으로 필터링하지 않는다.
- DB 수준 쿼리(COUNT, SUM, GROUP BY)나 페이지네이션을 사용한다.

### 페이지네이션 필수
- 검색 API는 반드시 페이지네이션을 적용한다.
- Spring Data의 `Pageable`과 `Page<T>`를 사용한다.

### HTTP 클라이언트 타임아웃
- 외부 API 호출 시 반드시 `connectTimeout`과 `readTimeout`을 설정한다.
- 타임아웃 미설정 시 외부 장애가 전파되어 스레드 풀이 고갈된다.

```java
// Good
conn.setConnectTimeout(3000);
conn.setReadTimeout(5000);

// Bad — 타임아웃 미설정
HttpURLConnection conn = (HttpURLConnection) url.openConnection();
// setConnectTimeout / setReadTimeout 없음
```

### 비동기 처리
- 이메일, SMS 등 알림 발송은 요청 스레드에서 동기적으로 처리하지 않는다.
- `@Async` 또는 메시지 큐(Kafka, RabbitMQ)를 사용하여 비동기로 처리한다.

```java
// Good — 비동기 처리
@Async
public void sendEmail(String to, String subject, String body) {
    emailSender.send(to, subject, body);
}

// Bad — 동기 블로킹
public void sendEmail(String to, String subject, String body) {
    emailSender.send(to, subject, body);  // 요청 스레드 블로킹
}
```

## 9. 아키텍처 (마이크로서비스)

### 분산 트랜잭션
- 하나의 로컬 트랜잭션에서 여러 마이크로서비스를 호출하지 않는다.
- Saga 패턴 또는 이벤트 기반 처리를 사용한다.
- 실패 시 보상 트랜잭션(Compensation)을 구현한다.

```java
// Good — Saga 패턴
@Transactional
public Long createOrder(CreateOrderCommand cmd) {
    Order order = Order.create(...);
    orderRepository.save(order);
    eventPublisher.publish(new OrderCreatedEvent(order.getId(), cmd.getItems()));
    return order.getId();
}

// Bad — 트랜잭션 내 외부 호출
@Transactional
public Long createOrder(CreateOrderCommand cmd) {
    Order order = Order.create(...);
    orderRepository.save(order);
    productClient.decreaseStock(...);  // 외부 호출
    paymentClient.requestPayment(...); // 외부 호출
    return order.getId();
}
```

### 멱등성 키 (Idempotency Key)
- 결제 API는 반드시 멱등성 키를 요구한다.
- 네트워크 재시도 시 중복 결제를 방지한다.
- `Idempotency-Key` 헤더 또는 요청 본문에 고유 키를 포함한다.

```java
// Good
@PostMapping
public ResponseEntity<?> processPayment(
        @RequestHeader("Idempotency-Key") String idempotencyKey,
        @RequestBody PaymentRequest request) {
    // idempotencyKey로 중복 체크
}

// Bad — 멱등성 키 없음
@PostMapping
public ResponseEntity<?> processPayment(@RequestBody PaymentRequest request) {
    // 재시도 시 중복 결제 위험
}
```

### Dead Letter Queue (DLQ)
- 비동기 메시지 처리 실패 시 메시지를 유실하지 않는다.
- DLQ에 실패한 메시지를 저장하고, 재처리 메커니즘을 구현한다.
- 최대 재시도 횟수를 설정하고, 초과 시 DLQ로 이동한다.

## 10. 동시성 제어

### 낙관적 락 (@Version)
- 동시 수정이 가능한 엔티티에는 `@Version` 필드를 추가한다.
- 재고 차감, 주문 상태 변경 등 경합이 발생할 수 있는 연산에 필수이다.
- `OptimisticLockException` 발생 시 재시도 로직을 구현한다.

```java
// Good — 낙관적 락
@Entity
public class Order {
    @Version
    private Long version;
    // ...
}

// Bad — @Version 없음 (동시 주문 시 재고 경합)
@Entity
public class Order {
    // @Version 필드 없음
}
```

## 11. 코드 품질

### 로깅
- `System.out.println()`을 사용하지 않는다. SLF4J 로거(`log.info`, `log.error`)를 사용한다.
- `e.printStackTrace()` 대신 `log.error("메시지", e)`를 사용한다.
