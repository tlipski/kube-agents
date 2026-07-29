#!/usr/bin/env python3
import os
import unittest
import re

class TestStockoutHandlerSkill(unittest.TestCase):
    def setUp(self):
        # Resolve path relative to repository root
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        container_path = os.path.join(base_dir, "skills", "gke-stockout-handler", "SKILL.md")
        ext_path = os.path.join(base_dir, "extensions", "gke-stockout-handler", "files", "skills", "gke-stockout-handler", "SKILL.md")
        host_path = os.path.join(base_dir, "agents", "platform", "skills", "gke-stockout-handler", "SKILL.md")
        if os.path.exists(container_path):
            self.skill_path = container_path
        elif os.path.exists(ext_path):
            self.skill_path = ext_path
        else:
            self.skill_path = host_path

    def test_skill_file_exists(self):
        self.assertTrue(os.path.exists(self.skill_path), f"Skill file not found at {self.skill_path}")

    def test_skill_frontmatter(self):
        with open(self.skill_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("name: gke-stockout-handler", content)
        self.assertIn("description: Act upon GKE cluster-autoscaler stockout alerts", content)

    def test_platform_agent_notification_on_investigation_start(self):
        with open(self.skill_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check Section 1 exists
        self.assertIn("### 1. Notify User of Investigation Start", content)

        # Extract Section 1 content
        section_2_match = re.search(r"### 1\. Notify User of Investigation Start(.*?)(?:\n###|\Z)", content, re.DOTALL)
        self.assertIsNotNone(section_2_match, "Could not extract Section 2 from SKILL.md")
        section_2_text = section_2_match.group(1)

        # Verify notification instruction requirements
        self.assertIn("send_notification", section_2_text, "Section 2 must reference the send_notification tool.")
        self.assertIn("confirmed", section_2_text, "Section 2 must mention that the stockout is confirmed.")
        self.assertIn("investigation", section_2_text, "Section 2 must mention that an investigation has started.")

    def test_platform_agent_notification_on_pr_creation(self):
        with open(self.skill_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check Section 5 exists
        self.assertIn("### 5. Submit Suggestion & Open PR", content)

        # Extract Section 5 content
        section_5_match = re.search(r"### 5\. Submit Suggestion & Open PR(.*?)(?:\n###|\Z)", content, re.DOTALL)
        self.assertIsNotNone(section_5_match, "Could not extract Section 5 from SKILL.md")
        section_5_text = section_5_match.group(1)

        # Verify notification instruction requirements
        self.assertIn("send_notification", section_5_text, "Section 5 must reference the send_notification tool.")
        self.assertIn("remediation", section_5_text.lower(), "Section 5 must mention proposing remediation.")

if __name__ == "__main__":
    unittest.main()
