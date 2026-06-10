from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.parser import ParsedFile

TARGET_MIN_TOKENS = 400
TARGET_MAX_TOKENS = 600


@dataclass(slots=True)
class CodeChunk:
    path: str
    content: str
    chunk_index: int
    start_line: int
    end_line: int


def create_chunks_for_file(
    parsed_file: ParsedFile,
    source_text: str,
    *,
    target_min_tokens: int = TARGET_MIN_TOKENS,
    target_max_tokens: int = TARGET_MAX_TOKENS,
) -> list[CodeChunk]:
    """
    Create semantic chunks while respecting function boundaries.

    Rules:
    - Function ranges are never split across chunks.
    - Non-function areas are greedily grouped into chunks near the target token size.
    """
    lines = source_text.splitlines()
    if not lines:
        return []

    line_count = len(lines)
    ranges = _build_semantic_ranges(parsed_file=parsed_file, line_count=line_count)

    chunks: list[CodeChunk] = []
    chunk_index = 0
    for start_line, end_line, is_function_range in ranges:
        if start_line > end_line:
            continue

        if is_function_range:
            content = _slice_lines(lines, start_line, end_line)
            chunks.append(
                CodeChunk(
                    path=parsed_file.path,
                    content=content,
                    chunk_index=chunk_index,
                    start_line=start_line,
                    end_line=end_line,
                )
            )
            chunk_index += 1
            continue

        remaining_start = start_line
        while remaining_start <= end_line:
            window_end = _fit_range_to_token_budget(
                lines=lines,
                start_line=remaining_start,
                max_end_line=end_line,
                target_min_tokens=target_min_tokens,
                target_max_tokens=target_max_tokens,
            )
            content = _slice_lines(lines, remaining_start, window_end)
            chunks.append(
                CodeChunk(
                    path=parsed_file.path,
                    content=content,
                    chunk_index=chunk_index,
                    start_line=remaining_start,
                    end_line=window_end,
                )
            )
            chunk_index += 1
            remaining_start = window_end + 1

    return chunks


def _build_semantic_ranges(parsed_file: ParsedFile, line_count: int) -> list[tuple[int, int, bool]]:
    function_ranges = sorted(
        (
            max(1, function.start_line),
            min(line_count, function.end_line),
        )
        for function in parsed_file.functions
        if function.start_line <= function.end_line
    )

    merged_functions: list[tuple[int, int]] = []
    for start, end in function_ranges:
        if not merged_functions:
            merged_functions.append((start, end))
            continue
        previous_start, previous_end = merged_functions[-1]
        if start <= previous_end + 1:
            merged_functions[-1] = (previous_start, max(previous_end, end))
        else:
            merged_functions.append((start, end))

    ranges: list[tuple[int, int, bool]] = []
    cursor = 1
    for start, end in merged_functions:
        if cursor < start:
            ranges.append((cursor, start - 1, False))
        ranges.append((start, end, True))
        cursor = end + 1

    if cursor <= line_count:
        ranges.append((cursor, line_count, False))
    if not ranges:
        ranges.append((1, line_count, False))
    return ranges


def _fit_range_to_token_budget(
    *,
    lines: list[str],
    start_line: int,
    max_end_line: int,
    target_min_tokens: int,
    target_max_tokens: int,
) -> int:
    best_end_line = start_line
    best_tokens = 0
    current_end_line = start_line

    while current_end_line <= max_end_line:
        candidate_text = _slice_lines(lines, start_line, current_end_line)
        token_estimate = _estimate_tokens(candidate_text)

        if token_estimate > target_max_tokens:
            if best_tokens >= target_min_tokens:
                return best_end_line
            # If even first line is too large, keep at least one line.
            return max(start_line, current_end_line - 1)

        best_end_line = current_end_line
        best_tokens = token_estimate
        if target_min_tokens <= token_estimate <= target_max_tokens:
            return current_end_line

        current_end_line += 1

    return best_end_line


def _slice_lines(lines: list[str], start_line: int, end_line: int) -> str:
    return "\n".join(lines[start_line - 1 : end_line]).strip()


def _estimate_tokens(text: str) -> int:
    # Simple conservative approximation for code-focused token counting.
    if not text:
        return 0
    return max(1, len(text) // 4)
