"""의존성 파서 테스트 - 다양한 의존성 파일 형식에서 패키지 추출 검증."""

from pathlib import Path

import pytest

from src.dependency_parser import (
    Dependency,
    is_dependency_file,
    parse_dependencies_from_diff,
)
from src.diff_parser import DiffResult, FileDiff, Hunk, Line, parse_diff

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestIsDependencyFile:
    def test_requirements_txt(self):
        assert is_dependency_file("requirements.txt") is True

    def test_requirements_dev_txt(self):
        assert is_dependency_file("requirements-dev.txt") is True

    def test_pyproject_toml(self):
        assert is_dependency_file("pyproject.toml") is True

    def test_package_json(self):
        assert is_dependency_file("package.json") is True

    def test_pom_xml(self):
        assert is_dependency_file("pom.xml") is True

    def test_build_gradle(self):
        assert is_dependency_file("build.gradle") is True

    def test_regular_python_file(self):
        assert is_dependency_file("src/main.py") is False

    def test_lock_file(self):
        assert is_dependency_file("package-lock.json") is False


class TestParseRequirementsTxt:
    def test_parse_pinned_version(self):
        diff = DiffResult(files=[FileDiff(
            filename="requirements.txt",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=3,
                lines=[
                    Line(number=1, content="flask==2.0.0", type="add"),
                    Line(number=2, content="requests==2.25.0", type="add"),
                    Line(number=3, content="django==3.2.0", type="add"),
                ],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 3
        assert deps[0].name == "flask"
        assert deps[0].version == "2.0.0"
        assert deps[1].name == "requests"
        assert deps[2].name == "django"

    def test_parse_extras(self):
        diff = DiffResult(files=[FileDiff(
            filename="requirements.txt",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=1,
                lines=[Line(number=1, content="psycopg[binary]==3.1.0", type="add")],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 1
        assert deps[0].name == "psycopg"
        assert deps[0].version == "3.1.0"

    def test_parse_minimum_version(self):
        diff = DiffResult(files=[FileDiff(
            filename="requirements.txt",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=1,
                lines=[Line(number=1, content="pydantic>=2.0.0", type="add")],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert deps[0].version == "2.0.0"

    def test_skip_comments_and_blanks(self):
        diff = DiffResult(files=[FileDiff(
            filename="requirements.txt",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=3,
                lines=[
                    Line(number=1, content="# this is a comment", type="add"),
                    Line(number=2, content="", type="add"),
                    Line(number=3, content="flask==2.0.0", type="add"),
                ],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 1

    def test_only_added_lines(self):
        """삭제된 라인의 패키지는 추출하지 않는다."""
        diff = DiffResult(files=[FileDiff(
            filename="requirements.txt",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=1, new_start=1, new_count=1,
                lines=[
                    Line(number=1, content="flask==1.0.0", type="delete"),
                    Line(number=1, content="flask==2.0.0", type="add"),
                ],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 1
        assert deps[0].version == "2.0.0"

    def test_no_version(self):
        diff = DiffResult(files=[FileDiff(
            filename="requirements.txt",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=1,
                lines=[Line(number=1, content="flask", type="add")],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 1
        assert deps[0].name == "flask"
        assert deps[0].version == ""


class TestParsePyprojectToml:
    def test_parse_dependency(self):
        diff = DiffResult(files=[FileDiff(
            filename="pyproject.toml",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=1,
                lines=[Line(number=1, content='    "flask>=2.0.0",', type="add")],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 1
        assert deps[0].name == "flask"
        assert deps[0].version == "2.0.0"


class TestParsePackageJson:
    def test_parse_dependency(self):
        diff = DiffResult(files=[FileDiff(
            filename="package.json",
            status="modified",
            hunks=[Hunk(
                old_start=1, old_count=0, new_start=1, new_count=2,
                lines=[
                    Line(number=1, content='    "express": "^4.18.0",', type="add"),
                    Line(number=2, content='    "lodash": "~4.17.21"', type="add"),
                ],
            )],
        )])
        deps = parse_dependencies_from_diff(diff)
        assert len(deps) == 2
        assert deps[0].name == "express"
        assert deps[0].version == "4.18.0"


class TestParseSampleDiff:
    def test_parse_from_fixture(self):
        """sample.diff fixture에서 requirements.txt 의존성을 추출한다."""
        text = (FIXTURES_DIR / "sample.diff").read_text()
        diff_result = parse_diff(text)
        deps = parse_dependencies_from_diff(diff_result)
        assert len(deps) == 1
        assert deps[0].name == "psycopg"
        assert deps[0].version == "3.1.0"


class TestEmptyDiff:
    def test_no_dependency_files(self):
        diff = DiffResult(files=[FileDiff(filename="src/main.py")])
        deps = parse_dependencies_from_diff(diff)
        assert deps == []
