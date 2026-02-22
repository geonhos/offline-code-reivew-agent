"""가이드라인 문서 적재 - Markdown 파싱 → 청킹 → 임베딩 → pgvector 저장."""

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.embedding import embed
from src.vectorstore import VectorStore

# 헤더(##) 수준에서 카테고리를 자동 추출하기 위한 매핑
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "naming": ["네이밍", "이름", "명명", "naming", "변수명", "함수명", "클래스명", "메서드명", "패키지명"],
    "error_handling": ["에러", "예외", "exception", "error", "리소스 정리", "null"],
    "security": ["보안", "security", "sql 인젝션", "민감 정보", "입력값 검증", "injection"],
    "performance": ["성능", "performance", "n+1", "비동기", "캐시", "페이지네이션", "컬렉션"],
    "code_structure": ["코드 구조", "import", "함수 크기", "메서드 크기", "타입 힌트", "로깅", "구조"],
}

BATCH_SIZE = 32


@dataclass
class Chunk:
    content: str
    category: str | None = None
    source: str = ""
    chunk_index: int = 0
    headers: list[str] = field(default_factory=list)


def detect_category(headers: list[str], content: str) -> str | None:
    """헤더와 본문 내용으로 카테고리를 추론한다."""
    text = " ".join(headers).lower() + " " + content[:200].lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return category
    return None


def chunk_markdown(text: str, source: str) -> list[Chunk]:
    """Markdown 문서를 ## 헤더 단위로 청킹한다.

    전략:
    - ## (h2) 단위로 분할하여 주제별 독립 청크 생성
    - 상위 헤더(#, h1)는 모든 하위 청크에 컨텍스트로 포함
    - ### (h3) 이하의 소제목은 해당 ## 섹션에 포함
    """
    lines = text.split("\n")
    chunks: list[Chunk] = []

    h1_header = ""
    current_headers: list[str] = []
    current_lines: list[str] = []
    chunk_index = 0

    def flush():
        nonlocal chunk_index
        if not current_lines:
            return
        body = "\n".join(current_lines).strip()
        if not body:
            return

        # 상위 헤더를 컨텍스트로 포함
        header_context = "\n".join(current_headers)
        full_content = f"{header_context}\n\n{body}" if header_context else body

        chunks.append(Chunk(
            content=full_content,
            category=detect_category(current_headers, body),
            source=source,
            chunk_index=chunk_index,
            headers=list(current_headers),
        ))
        chunk_index += 1

    in_code_block = False
    for line in lines:
        # 코드 블록(```) 토글 — 코드 블록 안의 #은 헤더가 아님
        if line.startswith("```"):
            in_code_block = not in_code_block
            current_lines.append(line)
            continue

        if in_code_block:
            current_lines.append(line)
            continue

        # h1 헤더: 문서 제목 — 모든 청크에 포함
        if re.match(r"^# [^#]", line):
            h1_header = line
            continue

        # h2 헤더: 청킹 경계
        if re.match(r"^## [^#]", line):
            flush()
            current_lines = []
            current_headers = [h1_header, line] if h1_header else [line]
            continue

        # h3 이하: 같은 청크에 포함
        current_lines.append(line)

    # 마지막 섹션 처리
    flush()

    return chunks


def ingest_file(path: Path, store: VectorStore) -> int:
    """단일 Markdown 파일을 청킹하여 벡터 DB에 적재한다."""
    text = path.read_text(encoding="utf-8")
    chunks = chunk_markdown(text, source=str(path))

    if not chunks:
        print(f"  ⚠ {path.name}: 청크 없음 (건너뜀)")
        return 0

    # 배치 임베딩
    stored = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c.content for c in batch]
        embeddings = embed(texts)

        items = [
            {
                "content": chunk.content,
                "embedding": emb,
                "category": chunk.category,
                "source": chunk.source,
                "chunk_index": chunk.chunk_index,
            }
            for chunk, emb in zip(batch, embeddings)
        ]
        store.insert_batch(items)
        stored += len(items)

    return stored


def ingest_directory(source_dir: str) -> int:
    """디렉토리 내 모든 Markdown 파일을 적재한다."""
    source_path = Path(source_dir)
    if not source_path.is_dir():
        raise FileNotFoundError(f"디렉토리를 찾을 수 없습니다: {source_dir}")

    md_files = sorted(source_path.glob("**/*.md"))
    if not md_files:
        print(f"⚠ {source_dir}에서 Markdown 파일을 찾을 수 없습니다.")
        return 0

    store = VectorStore()
    total = 0

    print(f"📂 {source_dir}에서 {len(md_files)}개 파일 발견")
    for path in md_files:
        count = ingest_file(path, store)
        print(f"  ✅ {path.name}: {count}개 청크 적재")
        total += count

    print(f"\n총 {total}개 청크 적재 완료")
    return total


def main():
    parser = argparse.ArgumentParser(description="가이드라인 문서를 벡터 DB에 적재합니다.")
    parser.add_argument("--source", required=True, help="Markdown 파일이 있는 디렉토리 경로")
    args = parser.parse_args()

    ingest_directory(args.source)


if __name__ == "__main__":
    main()
