# utils.py
import json
import os
from typing import Any

def escape_markdown_v2(text: str) -> str:
    """Telegram MarkdownV2 공식 규칙에 맞게 특수문자 이스케이프 처리"""
    if not text:
        return ""
    escape_chars = ['\\', '_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, '\\' + char)
    return text

def atomic_write(filepath: str, data: Any) -> None:
    """원자적 파일 쓰기(Atomic Write) - 프로세스 강제 종료 시 파일 손상 방지"""
    temp_filepath = filepath + ".tmp"
    with open(temp_filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(temp_filepath, filepath)

def split_message(text: str, limit: int = 4000) -> list[str]:
    """4096자 제한을 넘는 메시지를 줄바꿈 기준으로 안전하게 분할"""
    if len(text) <= limit:
        return [text]
    
    chunks = []
    current_chunk = ""
    lines = text.split('\n')
    for line in lines:
        if len(current_chunk) + len(line) + 1 > limit:
            chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
            
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks
