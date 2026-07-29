#!/usr/bin/env python3
"""
Platform Agent Skills Integrity Test Suite.

Verifies that all platform agent skills in `agents/platform/skills/`:
1. Contain a valid SKILL.md file.
2. Have non-empty YAML frontmatter containing 'name:' and 'description:'.
"""

import os
import unittest


class TestPlatformSkillsIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cls.skills_dir = os.path.join(cls.repo_root, "agents", "platform", "skills")

    def test_skills_directory_exists(self):
        self.assertTrue(os.path.exists(self.skills_dir), f"Skills directory not found: {self.skills_dir}")

    def test_all_skills_have_valid_skill_md(self):
        skill_dirs = [
            d for d in os.listdir(self.skills_dir)
            if os.path.isdir(os.path.join(self.skills_dir, d))
        ]
        self.assertGreater(len(skill_dirs), 0, "No skill subdirectories found in agents/platform/skills")

        for skill_name in skill_dirs:
            skill_md = os.path.join(self.skills_dir, skill_name, "SKILL.md")
            with self.subTest(skill=skill_name):
                self.assertTrue(os.path.exists(skill_md), f"SKILL.md missing for skill '{skill_name}' at {skill_md}")

                with open(skill_md, "r", encoding="utf-8") as f:
                    content = f.read()

                self.assertTrue(content.startswith("---"), f"SKILL.md for '{skill_name}' missing YAML frontmatter start marker")
                self.assertIn("name:", content, f"SKILL.md for '{skill_name}' missing 'name:' in frontmatter")
                self.assertIn("description:", content, f"SKILL.md for '{skill_name}' missing 'description:' in frontmatter")


if __name__ == "__main__":
    unittest.main()
