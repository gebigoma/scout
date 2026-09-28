"""Direct coverage of textutil.elide_middle - the shared helper both llm.py
and digest.py use so a truncated diagnostic doesn't lose whichever end its
cause happens to be on. See the module docstring for the 2026-09-28 incident
this exists to prevent."""
import unittest

from pipeline import textutil


class ElideMiddleTest(unittest.TestCase):
    def test_short_text_is_returned_unchanged(self):
        self.assertEqual(textutil.elide_middle("short"), "short")

    def test_text_exactly_at_the_limit_is_unchanged(self):
        text = "x" * 500
        self.assertEqual(textutil.elide_middle(text), text)

    def test_long_text_is_capped_at_the_limit(self):
        self.assertEqual(len(textutil.elide_middle("x" * 5000)), 500)

    def test_both_head_and_tail_survive(self):
        text = "HEAD-CONTENT" + ("x" * 5000) + "TAIL-CONTENT"
        result = textutil.elide_middle(text)
        self.assertTrue(result.startswith("HEAD-CONTENT"))
        self.assertTrue(result.endswith("TAIL-CONTENT"))

    def test_a_tracebacks_final_line_is_the_part_that_must_survive(self):
        """git's own fatal: lines come first; a Python traceback's cause
        comes last. A long traceback-shaped string must keep both."""
        traceback_like = (
            'Traceback (most recent call last):\n'
            + "  File \"hygiene.py\", line 1, in <module>\n" * 100
            + "subprocess.CalledProcessError: Command '...' returned "
              "non-zero exit status 128."
        )
        result = textutil.elide_middle(traceback_like)
        self.assertIn("Traceback (most recent call last):", result)
        self.assertTrue(result.endswith(
            "non-zero exit status 128."))

    def test_a_custom_limit_is_honoured(self):
        self.assertEqual(len(textutil.elide_middle("x" * 200, limit=50)), 50)

    def test_the_elision_marker_is_present_when_truncated(self):
        self.assertIn("elided", textutil.elide_middle("x" * 5000))

    def test_no_marker_when_nothing_was_elided(self):
        self.assertNotIn("elided", textutil.elide_middle("short"))


if __name__ == "__main__":
    unittest.main()
