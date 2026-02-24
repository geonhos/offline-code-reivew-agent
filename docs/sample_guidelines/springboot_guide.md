# Spring Boot 운영 및 아키텍처 가이드라인

## 설정 및 보안

### application.yml 보안
- `spring.datasource.password`, API 키 등 민감 정보는 반드시 환경 변수(`${ENV_VAR}`)로 주입한다.
- `application.yml`에 비밀번호를 평문으로 작성하지 않는다.
- `spring.jpa.hibernate.ddl-auto`는 운영 환경에서 반드시 `none` 또는 `validate`로 설정한다. `update`나 `create`는 데이터 유실 위험이 있다.
- `show-sql: true`는 개발 환경에서만 사용한다. 운영 환경에서는 성능 저하를 유발한다.

```yaml
# Good (운영)
spring:
  datasource:
    password: ${DB_PASSWORD}
  jpa:
    hibernate:
      ddl-auto: validate
    show-sql: false

# Bad (운영 위험)
spring:
  datasource:
    password: root1234
  jpa:
    hibernate:
      ddl-auto: update
    show-sql: true
```

## 예외 처리

### 커스텀 예외 사용
- `RuntimeException`을 직접 throw하지 않는다. 비즈니스 예외를 정의하여 사용한다.
- 예외 클래스에 HTTP 상태 코드를 매핑한다. `@ResponseStatus` 또는 `@ExceptionHandler`를 사용한다.
- `e.printStackTrace()`를 사용하지 않는다. SLF4J 로거로 예외를 기록한다.

```java
// Good
public class OrderNotFoundException extends RuntimeException {
    public OrderNotFoundException(Long orderId) {
        super("주문을 찾을 수 없습니다: " + orderId);
    }
}

// Bad
throw new RuntimeException("주문을 찾을 수 없습니다: " + orderId);
```

### GlobalExceptionHandler
- `RuntimeException`을 포괄적으로 catch하지 않는다. 비즈니스 예외별로 핸들러를 분리한다.
- 예외 메시지를 클라이언트에 그대로 노출하지 않는다. 내부 오류 정보가 유출될 수 있다.
- 예외 로깅 시 `e.printStackTrace()` 대신 `log.error("메시지", e)`를 사용한다.

```java
// Good
@ExceptionHandler(OrderNotFoundException.class)
public ResponseEntity<ErrorResponse> handleOrderNotFound(OrderNotFoundException e) {
    log.warn("주문 미발견: {}", e.getMessage());
    return ResponseEntity.status(HttpStatus.NOT_FOUND)
        .body(new ErrorResponse("ORDER_NOT_FOUND", e.getMessage()));
}

// Bad
@ExceptionHandler(RuntimeException.class)
public ResponseEntity<Map<String, String>> handle(RuntimeException e) {
    return ResponseEntity.status(500).body(Map.of("error", e.getMessage()));
}
```

## 입력 검증

### Bean Validation
- Controller의 `@RequestBody`에는 반드시 `@Valid`를 추가한다.
- Request DTO에 `@NotNull`, `@NotBlank`, `@Size`, `@Min` 등 검증 어노테이션을 사용한다.
- 검증 실패 시 `MethodArgumentNotValidException`을 GlobalExceptionHandler에서 처리한다.

```java
// Good
@PostMapping
public ResponseEntity<?> createOrder(@Valid @RequestBody CreateOrderRequest request) { ... }

public class CreateOrderRequest {
    @NotNull private Long memberId;
    @NotBlank private String address;
    @NotEmpty private List<OrderItemRequest> items;
}

// Bad
@PostMapping
public ResponseEntity<?> createOrder(@RequestBody CreateOrderRequest request) { ... }
// → null, 빈 값 등이 검증 없이 서비스 레이어로 전달됨
```

## 성능

### 데이터 조회
- 통계, 집계 등 대량 데이터 처리 시 `findAll()`로 전체 조회 후 메모리에서 필터링하지 않는다.
- DB 수준의 쿼리(GROUP BY, COUNT, SUM)나 페이지네이션을 사용한다.
- N+1 쿼리를 방지한다. `@EntityGraph` 또는 JPQL `JOIN FETCH`를 사용한다.

```java
// Good - DB에서 집계
@Query("SELECT COUNT(o) FROM Order o WHERE o.status = :status")
long countByStatus(@Param("status") OrderStatus status);

@Query("SELECT SUM(o.totalAmount) FROM Order o WHERE o.status != 'CANCELLED'")
BigDecimal calculateTotalRevenue();

// Bad - 전체 로딩 후 메모리 필터링
List<Order> allOrders = orderRepository.findAll();
long cancelCount = allOrders.stream().filter(o -> o.getStatus() == CANCELLED).count();
```

### RestTemplate / WebClient 설정
- RestTemplate에 반드시 Connection Timeout과 Read Timeout을 설정한다.
- 타임아웃 미설정 시 외부 서비스 장애가 전파되어 스레드 풀이 고갈될 수 있다.

```java
// Good
@Bean
public RestTemplate restTemplate() {
    var factory = new SimpleClientHttpRequestFactory();
    factory.setConnectTimeout(3000);
    factory.setReadTimeout(5000);
    return new RestTemplate(factory);
}

// Bad - 타임아웃 미설정
@Bean
public RestTemplate restTemplate() {
    return new RestTemplate();
}
```

## 도메인 설계 (DDD)

### Aggregate Root 보호
- 도메인 엔티티의 상태 변경은 엔티티 메서드를 통해서만 수행한다.
- 엔티티에 `@Setter`를 사용하지 않는다. 의미 있는 메서드명으로 상태를 변경한다.
- 상태 전이 시 현재 상태를 검증한다. 유효하지 않은 전이는 예외를 발생시킨다.

```java
// Good - 상태 검증 후 전이
public void cancel() {
    if (this.status != OrderStatus.ORDERED && this.status != OrderStatus.PAID) {
        throw new IllegalStateException("취소할 수 없는 상태입니다: " + this.status);
    }
    this.status = OrderStatus.CANCELLED;
    this.cancelledAt = LocalDateTime.now();
}

// Bad - 상태 검증 없음
public void cancel() {
    this.status = OrderStatus.CANCELLED;  // COMPLETED 상태에서도 취소 가능?
}
```

### 트랜잭션 경계
- 하나의 트랜잭션에서 여러 Aggregate를 수정하지 않는다.
- 외부 서비스 호출(HTTP, 메시지 큐)은 트랜잭션 완료 후 수행한다. 실패 시 보상 트랜잭션(Saga)을 고려한다.
- 재고 차감 등 분산 리소스 변경은 트랜잭션 내에서 직접 호출하지 않는다. 이벤트 기반 처리를 권장한다.

```java
// Good - 이벤트 기반
@Transactional
public Long createOrder(CreateOrderCommand command) {
    Order order = Order.create(...);
    orderRepository.save(order);
    eventPublisher.publish(new OrderCreatedEvent(order.getId(), items));
    return order.getId();
}

// Bad - 트랜잭션 내 외부 호출
@Transactional
public Long createOrder(CreateOrderCommand command) {
    Order order = Order.create(...);
    orderRepository.save(order);
    productClient.decreaseStock(...);  // 실패 시 주문은 저장되고 재고는 안 줄어듦
    return order.getId();
}
```
