from io import BytesIO
from types import SimpleNamespace
import zipfile

import pytest

from utils.upload_safety import UploadValidationError, validate_upload


def _upload(filename, content):
    return SimpleNamespace(filename=filename, stream=BytesIO(content))


def test_validate_upload_preserves_stream_position():
    upload = _upload('notes.txt', b'abcdef')
    upload.stream.seek(2)

    assert validate_upload(upload, max_bytes=1024) == 6
    assert upload.stream.tell() == 2


def test_validate_upload_rejects_oversized_file():
    upload = _upload('notes.txt', b'x' * 11)

    with pytest.raises(UploadValidationError) as exc_info:
        validate_upload(upload, max_bytes=10)

    assert exc_info.value.status_code == 413


def test_validate_upload_rejects_zip_expansion_before_parsing():
    archive = BytesIO()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr('word/document.xml', 'x' * 1024)
    upload = _upload('lesson.docx', archive.getvalue())

    with pytest.raises(UploadValidationError) as exc_info:
        validate_upload(
            upload,
            max_bytes=1024 * 1024,
            zip_extensions={'.docx'},
            max_uncompressed_bytes=512,
            max_member_bytes=2048,
        )

    assert exc_info.value.status_code == 413


def test_validate_upload_rejects_malformed_zip_container():
    upload = _upload('lesson.docx', b'not a zip file')

    with pytest.raises(UploadValidationError):
        validate_upload(upload, max_bytes=1024, zip_extensions={'.docx'})
