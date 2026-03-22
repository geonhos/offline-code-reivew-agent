"""컨텍스트 수집기 테스트 - AST 분석, 호출 관계, 전체 파일 fetch 검증."""

from unittest.mock import MagicMock

import pytest

from src.context_enricher import ContextEnricher, EnrichedContext, FileContext
from src.diff_parser import FileDiff, Hunk, Line


@pytest.fixture()
def enricher():
    mock_client = MagicMock()
    return ContextEnricher(gitlab_client=mock_client), mock_client


SAMPLE_PYTHON = """\
import os
from pathlib import Path
from typing import Optional

class OrderService:
    def find_order(self, order_id: str) -> dict:
        query = f"SELECT * FROM orders WHERE id = '{order_id}'"
        return self.db.execute(query)

    def process_payment(self, amount: float) -> bool:
        return self.find_order("test") is not None

def helper_func():
    pass

def main():
    svc = OrderService()
    svc.find_order("123")
    helper_func()
"""


class TestDetectLanguage:
    def test_python(self, enricher):
        ce, _ = enricher
        assert ce._detect_language("src/main.py") == "python"

    def test_java(self, enricher):
        ce, _ = enricher
        assert ce._detect_language("com/example/App.java") == "java"

    def test_javascript(self, enricher):
        ce, _ = enricher
        assert ce._detect_language("src/index.js") == "javascript"

    def test_typescript(self, enricher):
        ce, _ = enricher
        assert ce._detect_language("src/App.tsx") == "typescript"

    def test_unknown(self, enricher):
        ce, _ = enricher
        assert ce._detect_language("README.md") == "unknown"


class TestAnalyzePythonAst:
    def test_extracts_imports(self, enricher):
        ce, _ = enricher
        ctx, tree = ce._analyze_python_ast(SAMPLE_PYTHON)
        assert "os" in ctx.imports
        assert "pathlib" in ctx.imports
        assert "typing" in ctx.imports

    def test_extracts_function_signatures(self, enricher):
        ce, _ = enricher
        ctx, _ = ce._analyze_python_ast(SAMPLE_PYTHON)
        sig_names = [s.split("(")[0] for s in ctx.function_signatures]
        assert "def find_order" in sig_names
        assert "def process_payment" in sig_names
        assert "def helper_func" in sig_names
        assert "def main" in sig_names

    def test_extracts_class_names(self, enricher):
        ce, _ = enricher
        ctx, _ = ce._analyze_python_ast(SAMPLE_PYTHON)
        assert "OrderService" in ctx.class_names

    def test_function_signature_includes_types(self, enricher):
        ce, _ = enricher
        ctx, _ = ce._analyze_python_ast(SAMPLE_PYTHON)
        find_order_sig = next(s for s in ctx.function_signatures if "find_order" in s)
        assert "str" in find_order_sig
        assert "dict" in find_order_sig

    def test_handles_syntax_error(self, enricher):
        ce, _ = enricher
        ctx, tree = ce._analyze_python_ast("def broken(:\n  pass")
        assert ctx.imports == []
        assert ctx.function_signatures == []
        assert ctx.language == "python"
        assert tree is None


class TestFindCallers:
    def test_finds_direct_caller(self, enricher):
        import ast
        ce, _ = enricher
        tree = ast.parse(SAMPLE_PYTHON)
        callers = ce._find_callers(tree, ["helper_func"])
        assert "main" in callers

    def test_finds_method_caller(self, enricher):
        import ast
        ce, _ = enricher
        tree = ast.parse(SAMPLE_PYTHON)
        callers = ce._find_callers(tree, ["find_order"])
        assert "process_payment" in callers
        assert "main" in callers

    def test_no_callers_for_unused_function(self, enricher):
        import ast
        ce, _ = enricher
        tree = ast.parse(SAMPLE_PYTHON)
        callers = ce._find_callers(tree, ["nonexistent_func"])
        assert callers == []


class TestExtractChangedFunctions:
    def test_maps_changed_lines_to_functions(self, enricher):
        import ast
        ce, _ = enricher
        tree = ast.parse(SAMPLE_PYTHON)
        file_diff = FileDiff(
            filename="test.py",
            hunks=[Hunk(
                old_start=7, old_count=1, new_start=7, new_count=1,
                lines=[Line(number=7, content='query = "SELECT ..."', type="add")],
            )],
        )
        funcs = ce._extract_changed_function_names(file_diff, tree)
        assert "find_order" in funcs


class TestEnrich:
    def test_enrich_with_mock_gitlab(self, enricher):
        ce, mock_client = enricher
        mock_client.get_file_content.return_value = SAMPLE_PYTHON

        file_diff = FileDiff(
            filename="app/service.py",
            hunks=[Hunk(
                old_start=7, old_count=1, new_start=7, new_count=1,
                lines=[Line(number=7, content="changed line", type="add")],
            )],
        )

        results = ce.enrich(project_id=1, file_diffs=[file_diff])
        assert len(results) == 1
        assert isinstance(results[0], FileContext)
        assert results[0].enriched.language == "python"
        assert len(results[0].enriched.imports) > 0
        assert len(results[0].enriched.function_signatures) > 0

    def test_enrich_non_python_file(self, enricher):
        ce, mock_client = enricher
        mock_client.get_file_content.return_value = "public class App {}"

        file_diff = FileDiff(filename="App.java")
        results = ce.enrich(project_id=1, file_diffs=[file_diff])
        assert results[0].enriched.language == "java"
        assert results[0].enriched.imports == []

    def test_enrich_file_not_found(self, enricher):
        ce, mock_client = enricher
        mock_client.get_file_content.return_value = ""

        file_diff = FileDiff(filename="missing.py")
        results = ce.enrich(project_id=1, file_diffs=[file_diff])
        assert results[0].enriched.full_source == ""
        assert results[0].enriched.language == "python"
        assert results[0].enriched.imports == []
