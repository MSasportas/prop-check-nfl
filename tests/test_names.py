import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import names


class NickTest(unittest.TestCase):
    def setUp(self):
        self.m = names.Matcher([
            {"id": "a", "first": "Marquise", "last": "Brown", "team": "PHI", "pos": "WR"},
            {"id": "b", "first": "Andrew", "last": "Ogletree", "team": "IND", "pos": "TE"},
            {"id": "c", "first": "A.J.", "last": "Brown", "team": "PHI", "pos": "WR"},
        ])

    def test_nicknames(self):
        self.assertEqual(self.m.find("Hollywood Brown")["id"], "a")
        self.assertEqual(self.m.find("Drew Ogletree")["id"], "b")
        self.assertEqual(self.m.find("AJ Brown")["id"], "c")
        self.assertEqual(self.m.find("Marquise Brown")["id"], "a")

    def test_no_guess(self):
        self.assertIsNone(self.m.find("James Jordan"))
        self.assertIsNone(self.m.find("Drew Brown"))


if __name__ == "__main__":
    unittest.main()
