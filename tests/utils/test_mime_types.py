"""共享 MIME 映射测试"""

from vibeocr.backend.utils.mime_types import (
    EXT_TO_MIME,
    FILE_FILTER_ALL,
    FILE_FILTER_DOCUMENTS,
    FILE_FILTER_IMAGES,
    extension_to_mime,
    guess_mime_from_filename,
    is_document_file,
    is_office_file,
    mime_to_extension,
)


class TestMimeMap:
    """MIME 映射双向转换"""

    def test_extension_to_mime_images(self):
        assert extension_to_mime(".png") == "image/png"
        assert extension_to_mime(".jpg") == "image/jpeg"
        assert extension_to_mime(".jpeg") == "image/jpeg"
        assert extension_to_mime(".bmp") == "image/bmp"
        assert extension_to_mime(".tiff") == "image/tiff"
        assert extension_to_mime(".tif") == "image/tiff"
        assert extension_to_mime(".gif") == "image/gif"
        assert extension_to_mime(".webp") == "image/webp"
        assert extension_to_mime(".jp2") == "image/jp2"

    def test_extension_to_mime_documents(self):
        assert extension_to_mime(".pdf") == "application/pdf"
        assert extension_to_mime(".docx") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert extension_to_mime(".pptx") == (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert extension_to_mime(".xlsx") == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_extension_to_mime_unknown(self):
        assert extension_to_mime(".xyz") is None

    def test_mime_to_extension(self):
        assert mime_to_extension("image/png") == ".png"
        assert mime_to_extension("application/pdf") == ".pdf"
        assert mime_to_extension("image/jpeg") == ".jpg"
        assert (
            mime_to_extension(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            == ".docx"
        )

    def test_mime_to_extension_unknown(self):
        assert mime_to_extension("text/plain") is None

    def test_all_ext_entries_have_reverse_mapping(self):
        for ext, mime in EXT_TO_MIME.items():
            assert mime_to_extension(mime) is not None, (
                f"No reverse mapping for {ext} -> {mime}"
            )

    def test_file_filters_are_strings(self):
        assert isinstance(FILE_FILTER_IMAGES, str)
        assert isinstance(FILE_FILTER_DOCUMENTS, str)
        assert isinstance(FILE_FILTER_ALL, str)
        assert "docx" in FILE_FILTER_ALL
        assert "webp" in FILE_FILTER_ALL
        assert "jp2" in FILE_FILTER_ALL

    def test_guess_mime_from_filename(self):
        assert guess_mime_from_filename("test.pdf") == "application/pdf"
        assert guess_mime_from_filename("photo.webp") == "image/webp"
        assert guess_mime_from_filename("report.DOCX") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_is_office_file(self):
        assert is_office_file("report.docx") is True
        assert is_office_file("slides.pptx") is True
        assert is_office_file("data.xlsx") is True
        assert is_office_file("photo.png") is False
        assert is_office_file("doc.pdf") is False

    def test_is_document_file(self):
        assert is_document_file("doc.pdf") is True
        assert is_document_file("report.docx") is True
        assert is_document_file("slides.pptx") is True
        assert is_document_file("data.xlsx") is True
        assert is_document_file("photo.png") is False
        assert is_document_file("image.jpg") is False
        assert is_document_file("screenshot.BMP") is False


class TestGuessMimeFromBytes:
    """guess_mime_from_bytes 魔数识别（无文件名时按内容路由）。"""

    from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

    def test_png_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"\x89PNG\r\n\x1a\n" + b"rest") == "image/png"

    def test_pdf_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"%PDF-1.7...") == "application/pdf"

    def test_jpeg_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"\xff\xd8\xff\xe0rest") == "image/jpeg"

    def test_gif_87a_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"GIF87a" + b"data") == "image/gif"

    def test_gif_89a_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"GIF89a" + b"data") == "image/gif"

    def test_bmp_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"BM" + b"\x00" * 10) == "image/bmp"

    def test_tiff_little_endian_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"II*\x00rest") == "image/tiff"

    def test_tiff_big_endian_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"MM\x00*rest") == "image/tiff"

    def test_webp_magic(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert (
            guess_mime_from_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"vp8")
            == "image/webp"
        )

    def test_zip_container_treated_as_pdf(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        # DOCX/PPTX/XLSX 都是 ZIP 容器，统一归到 application/pdf（走文档解析）
        assert guess_mime_from_bytes(b"PK\x03\x04" + b"\x00" * 20) == "application/pdf"

    def test_unknown_falls_back_to_pdf(self):
        from vibeocr.backend.utils.mime_types import guess_mime_from_bytes

        assert guess_mime_from_bytes(b"\x00\x01\x02random") == "application/pdf"
        assert guess_mime_from_bytes(b"") == "application/pdf"
