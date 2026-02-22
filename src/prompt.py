"""프롬프트 템플릿 관리 - 코드 리뷰 전용 프롬프트 설계."""

from src.diff_parser import FileDiff
from src.vectorstore import GuidelineChunk

SYSTEM_PROMPT = """\
You are an expert code reviewer. Your task is to review code changes and provide \
actionable feedback.

Rules:
- Focus ONLY on the changed lines (lines starting with +).
- Reference specific line numbers from the diff.
- Classify each issue by severity: critical, warning, or info.
- If the code follows best practices, say so briefly.
- Respond ONLY with the JSON array format specified below. No other text.
- Write comments in Korean.
"""

REVIEW_PROMPT_TEMPLATE = """\
## 관련 코딩 가이드라인

{guidelines}

## 코드 변경 사항

파일: `{filename}`

```diff
{diff_content}
```

## 리뷰 지시사항

위 코드 변경 사항을 관련 가이드라인에 따라 리뷰하세요.
반드시 아래 JSON 배열 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요.

```json
[
  {{
    "file": "{filename}",
    "line": <라인번호>,
    "severity": "<critical|warning|info>",
    "message": "<리뷰 코멘트>"
  }}
]
```

이슈가 없으면 빈 배열 `[]`을 반환하세요.
"""

# Few-shot 예시: 좋은 리뷰 vs 나쁜 리뷰
FEW_SHOT_EXAMPLES = """\
## 리뷰 예시

### 좋은 리뷰 (구체적, 라인 번호 명시, 개선 방향 제시):
```json
[
  {
    "file": "src/auth.py",
    "line": 15,
    "severity": "critical",
    "message": "비밀번호가 하드코딩되어 있습니다. 환경 변수(os.environ)로 대체하세요."
  },
  {
    "file": "src/auth.py",
    "line": 23,
    "severity": "warning",
    "message": "빈 except 절입니다. 구체적인 예외 타입을 명시하고 로깅을 추가하세요."
  }
]
```

### 나쁜 리뷰 (모호, 라인 번호 없음, 개선 방향 없음):
```json
[
  {
    "file": "src/auth.py",
    "line": 0,
    "severity": "info",
    "message": "코드를 개선하세요."
  }
]
```
"""


def format_guidelines(chunks: list[GuidelineChunk]) -> str:
    """검색된 가이드라인 청크를 프롬프트용 텍스트로 포맷팅한다."""
    if not chunks:
        return "(관련 가이드라인 없음)"

    parts = []
    for i, chunk in enumerate(chunks, 1):
        category = f"[{chunk.category}]" if chunk.category else ""
        parts.append(f"### 가이드라인 {i} {category}\n{chunk.content}")
    return "\n\n".join(parts)


def format_diff(file_diff: FileDiff) -> str:
    """FileDiff를 프롬프트용 diff 텍스트로 변환한다."""
    lines = []
    for hunk in file_diff.hunks:
        lines.append(f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@")
        for line in hunk.lines:
            if line.type == "add":
                lines.append(f"+{line.content}")
            elif line.type == "delete":
                lines.append(f"-{line.content}")
            else:
                lines.append(f" {line.content}")
    return "\n".join(lines)


def build_review_prompt(
    file_diff: FileDiff,
    guidelines: list[GuidelineChunk],
    include_few_shot: bool = True,
) -> tuple[str, str]:
    """리뷰용 시스템 프롬프트와 사용자 프롬프트를 생성한다.

    Returns:
        (system_prompt, user_prompt) 튜플.
    """
    system = SYSTEM_PROMPT
    if include_few_shot:
        system += "\n" + FEW_SHOT_EXAMPLES

    user = REVIEW_PROMPT_TEMPLATE.format(
        guidelines=format_guidelines(guidelines),
        filename=file_diff.filename,
        diff_content=format_diff(file_diff),
    )

    return system, user
