# -*- coding: utf-8 -*-
"""Model- and network-free core tests: text normalization, masking, Claude Code session parsing, and injection markers."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor"))
os.environ.setdefault("KNOWLENS_HOME",
                      tempfile.mkdtemp(prefix="knowlens-test-"))

from knowlens import cc, privacy, textutil  # noqa: E402


class TestTextUtil(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(textutil.norm_text("Hello, 世界!"), "hello世界")
        self.assertEqual(textutil.candidate_key("rule", "A B"),
                         textutil.candidate_key("rule", "a-b"))

    def test_privacy(self):
        fake_key = "sk-" + "abcdefghijkl" + "1234567890"
        s = privacy.mask(f"api_key={fake_key} 电话 13812345678 邮箱 a@b.com")
        self.assertNotIn("abcdefghijkl", s)
        self.assertNotIn("13812345678", s)
        self.assertNotIn("a@b.com", s)


class TestCC(unittest.TestCase):
    def test_encode_path(self):
        self.assertTrue(cc.encode_path("D:/work/my-project").startswith("D--work"))

    def test_extract_session(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                         encoding="utf-8") as f:
            f.write(json.dumps({"type": "user",
                                "message": {"content": "请记住：部署前先跑测试"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "明白，部署流程加一道测试关卡。"}]}}) + "\n")
            f.write(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": "ignored"}]}}) + "\n")
            path = f.name
        try:
            msgs = cc._extract(path)
            self.assertEqual(len(msgs), 2)
            self.assertIn("部署", msgs[0][1])
        finally:
            os.unlink(path)

    def test_is_injected(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            sp = cc.settings_path(d)
            with open(sp, "w", encoding="utf-8") as f:
                json.dump({"hooks": {"SessionStart": [{"hooks": [{
                    "type": "command",
                    "command": "python scripts/knowlens_digest.py --space sp-test"
                }]}]}}, f)
            self.assertTrue(cc.is_injected(d, "sp-test"))
            self.assertFalse(cc.is_injected(d, "sp-other"))


if __name__ == "__main__":
    unittest.main()