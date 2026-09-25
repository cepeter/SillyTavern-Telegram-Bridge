from application_test_setup import ensure_application_extensions
from settings_test_support import SettingsTestCase

import bridge.document_extraction as _owner_document_extraction
import bridge.session_core as _owner_session_core

ensure_application_extensions()

import tempfile
import unittest
from pathlib import Path

import bridge.language as _m_language
import bridge.memory_curator as _m_memory_curator
import bridge.session_naming as _m_session_naming


class PdfWorkerTests(SettingsTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app_settings_builder.db_file = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = _m_memory_curator.db_connect(app_settings=self.app_settings_builder.build())

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_pdf_extraction_uses_bounded_worker(self):
        text = "Hello PDF worker"
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                b"/Resources << /Font << /F1 5 0 R >> >> >>"
            ),
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        pdf = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
        xref = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
        pdf.extend(b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]))
        pdf.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())

        self.assertIn(
            text,
            _owner_document_extraction.extract_data_bank_text(
                "fixture.pdf", bytes(pdf), app_settings=self.app_settings_builder.build()
            ),
        )
        with self.assertRaises(ValueError):
            _owner_document_extraction.extract_data_bank_text(
                "fixture.pdf", b"not a pdf", app_settings=self.app_settings_builder.build()
            )

        first = _owner_session_core.ensure_session(
            self.db, "chat", self.app_settings_builder.default_model, app_settings=self.app_settings_builder.build()
        )
        _m_language.set_response_language(
            self.db,
            "chat",
            first["session_id"],
            "id",
            update_session=_owner_session_core.update_session,
        )
        second = _m_session_naming.create_session(
            self.db,
            "chat",
            self.app_settings_builder.default_model,
            session_id="second",
            app_settings=self.app_settings_builder.build(),
        )

        self.assertEqual(
            _m_memory_curator.load_session(
                self.db,
                "chat",
                first["session_id"],
                self.app_settings_builder.default_model,
                app_settings=self.app_settings_builder.build(),
            )["response_language"],
            "id",
        )
        self.assertEqual(second["response_language"], "auto")


if __name__ == "__main__":
    unittest.main()
