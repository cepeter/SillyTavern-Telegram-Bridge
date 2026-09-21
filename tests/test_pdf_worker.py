from pathlib import Path
import tempfile
import unittest

import bridge.config as config
import bridge.runtime as rt


class PdfWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.DB_FILE = Path(self.tmp.name) / "bridge.sqlite3"
        self.db = rt.db_connect()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_pdf_extraction_uses_bounded_worker(self):
        text = "Hello PDF worker"
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
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

        self.assertIn(text, rt.extract_data_bank_text("fixture.pdf", bytes(pdf)))
        with self.assertRaises(ValueError):
            rt.extract_data_bank_text("fixture.pdf", b"not a pdf")

        first = rt.ensure_session(self.db, "chat", rt.DEFAULT_MODEL)
        rt.set_response_language(self.db, "chat", first["session_id"], "id")
        second = rt.create_session(self.db, "chat", rt.DEFAULT_MODEL, session_id="second")

        self.assertEqual(
            rt.load_session(self.db, "chat", first["session_id"], rt.DEFAULT_MODEL)["response_language"],
            "id",
        )
        self.assertEqual(second["response_language"], "auto")


if __name__ == "__main__":
    unittest.main()
