"""Bounded validation helpers for user-uploaded files.

The application parses spreadsheets and DOCX files with libraries that may
inspect more than the first few logical rows.  Validate the underlying stream
before handing it to those parsers so a small compressed archive cannot expand
into an unbounded amount of work or storage.
"""

from __future__ import annotations

import os
import zipfile


class UploadValidationError(ValueError):
    """A user upload failed a bounded safety check."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _stream_size(stream) -> int:
    """Return the current stream size without changing its current position."""

    try:
        current_position = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = int(stream.tell())
        stream.seek(current_position)
    except (AttributeError, OSError, ValueError, TypeError) as exc:
        raise UploadValidationError('无法读取上传文件，请重新选择文件。') from exc
    if size < 0:
        raise UploadValidationError('上传文件大小无效。')
    return size


def _validate_zip_stream(
    stream,
    *,
    max_uncompressed_bytes: int,
    max_members: int,
    max_member_bytes: int,
) -> None:
    """Validate a ZIP container using its central directory only."""

    try:
        current_position = stream.tell()
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            if len(infos) > max_members:
                raise UploadValidationError('压缩文件包含过多内部文件。', 413)

            total_uncompressed = 0
            for info in infos:
                if info.is_dir():
                    continue
                member_size = int(info.file_size)
                if member_size > max_member_bytes:
                    raise UploadValidationError('压缩文件中的单个文件过大。', 413)
                total_uncompressed += member_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise UploadValidationError('压缩文件解压后的内容过大。', 413)

                # A very high compression ratio is a useful second signal for
                # zip-bomb payloads.  The total/member bounds above remain the
                # authoritative limits, so normal small office documents are
                # not rejected merely because they compress well.
                compressed_size = max(int(info.compress_size), 1)
                if member_size > 1024 * 1024 and member_size / compressed_size > 1000:
                    raise UploadValidationError('压缩文件压缩比例异常。', 413)
    except zipfile.BadZipFile as exc:
        raise UploadValidationError('压缩文件格式无效，请重新导出后上传。') from exc
    finally:
        try:
            stream.seek(current_position)
        except (AttributeError, OSError, ValueError, UnboundLocalError):
            pass


def validate_upload(
    file_storage,
    *,
    max_bytes: int,
    zip_extensions=(),
    max_uncompressed_bytes: int = 64 * 1024 * 1024,
    max_members: int = 2000,
    max_member_bytes: int = 32 * 1024 * 1024,
) -> int:
    """Validate a Flask ``FileStorage`` and return its byte size.

    The stream position is preserved so the caller can pass the same upload
    directly to pandas, python-docx, or another parser after validation.
    """

    stream = getattr(file_storage, 'stream', None)
    if stream is None:
        raise UploadValidationError('上传文件不可读取。')

    size = _stream_size(stream)
    if size > max_bytes:
        raise UploadValidationError(
            f'上传文件不能超过 {max_bytes // (1024 * 1024)} MB。',
            413,
        )

    filename = str(getattr(file_storage, 'filename', '') or '').lower()
    extension = os.path.splitext(filename)[1]
    if extension in {str(item).lower() for item in zip_extensions}:
        _validate_zip_stream(
            stream,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_members=max_members,
            max_member_bytes=max_member_bytes,
        )
    return size
