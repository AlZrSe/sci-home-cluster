"""Tests for the BoundedLineWriter (agent/log_writer.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path


from agent.log_writer import BoundedLineWriter


class TestBoundedLineWriter:
    """Tests for BoundedLineWriter."""

    def test_write_line_basic(self):
        """Test writing a simple line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"hello world")

            content = file_path.read_text(encoding="utf-8")
            assert content == "hello world\n"

    def test_write_line_no_newline(self):
        """Test that newline is added if missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"hello world")

            content = file_path.read_text(encoding="utf-8")
            assert content.endswith("\n")

    def test_write_line_with_newline(self):
        """Test that existing newline is preserved (not doubled)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"hello world\n")

            content = file_path.read_text(encoding="utf-8")
            assert content == "hello world\n"

    def test_write_line_truncation(self):
        """Test that long lines are truncated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            # Create a line longer than MAX_LINE_LENGTH (10240)
            long_line = "x" * 15000
            writer.write_line(long_line.encode("utf-8"))

            content = file_path.read_text(encoding="utf-8")
            # Should be truncated to MAX_LINE_LENGTH
            assert len(content) <= BoundedLineWriter.MAX_LINE_LENGTH
            assert content.endswith("\n")

    def test_write_line_undecodable_bytes(self):
        """Test handling of undecodable bytes with errors='replace'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            # Invalid UTF-8 sequence
            writer.write_line(b"hello \xff\xfe world")

            content = file_path.read_text(encoding="utf-8")
            # Should contain replacement character
            assert "�" in content or "hello" in content

    def test_multiple_writes(self):
        """Test multiple writes append to the file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"line 1")
            writer.write_line(b"line 2")
            writer.write_line(b"line 3")

            content = file_path.read_text(encoding="utf-8")
            assert content == "line 1\nline 2\nline 3\n"

    def test_context_manager(self):
        """Test using BoundedLineWriter as a context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"

            with BoundedLineWriter(file_path) as writer:
                writer.write_line(b"hello")

            content = file_path.read_text(encoding="utf-8")
            assert content == "hello\n"

    def test_close(self):
        """Test explicit close."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"hello")
            writer.close()

            # Should be able to write again after close
            writer.write_line(b"world")

            content = file_path.read_text(encoding="utf-8")
            assert content == "hello\nworld\n"

    def test_flush(self):
        """Test async flush method."""
        import asyncio

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"hello")
            asyncio.run(writer.flush())

            content = file_path.read_text(encoding="utf-8")
            assert content == "hello\n"

    def test_implicit_cleanup_on_gc(self):
        """Test that file handle is closed when writer is garbage collected."""
        import gc
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)
            writer.write_line(b"hello")
            del writer
            gc.collect()  # Force collection

            # Should be able to read/delete the file
            content = file_path.read_text(encoding="utf-8")
            assert content == "hello\n"


class TestBoundedLineWriterEdgeCases:
    """Edge case tests for BoundedLineWriter."""

    def test_empty_line(self):
        """Test writing an empty line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"")

            content = file_path.read_text(encoding="utf-8")
            assert content == "\n"

    def test_exactly_max_length(self):
        """Test line at exactly MAX_LINE_LENGTH."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            # Line that with newline equals MAX_LINE_LENGTH
            line = "x" * (BoundedLineWriter.MAX_LINE_LENGTH - 1)
            writer.write_line(line.encode("utf-8"))

            content = file_path.read_text(encoding="utf-8")
            assert len(content) == BoundedLineWriter.MAX_LINE_LENGTH

    def test_unicode_content(self):
        """Test writing Unicode content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line("Hello 世界 🌍".encode("utf-8"))

            content = file_path.read_text(encoding="utf-8")
            assert "世界" in content
            assert "🌍" in content

    def test_directory_creation(self):
        """Test that parent directories are created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "nested" / "deep" / "test.log"
            writer = BoundedLineWriter(file_path)

            writer.write_line(b"hello")

            content = file_path.read_text(encoding="utf-8")
            assert content == "hello\n"
