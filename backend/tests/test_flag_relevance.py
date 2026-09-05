"""Reviewed mismatch examples and boundaries of personal title preferences."""

import json
import unittest
from pathlib import Path

from backend.src.config.settings import DEFAULT_UNWANTED_KEYWORDS
from backend.src.utils.relevance import classify_relevance, decision_for_result


class TestFlagRelevance(unittest.TestCase):
    def classify(self, title, **kwargs):
        return classify_relevance(
            title, unwanted_keywords=DEFAULT_UNWANTED_KEYWORDS.split(","), **kwargs,
        )

    def test_reviewed_flags_and_unresolved_examples(self):
        fixture = Path(__file__).parent / "fixtures" / "flag_relevance_cases.json"
        for case in json.loads(fixture.read_text())["cases"]:
            with self.subTest(job_id=case["job_id"], title=case["title"]):
                result = self.classify(case["title"], company=case["company"])
                self.assertEqual(result.outcome, case["outcome"])
                if case["rule"]:
                    self.assertIn(case["rule"], result.negative_matches)

    def test_close_variants_of_reviewed_preferences(self):
        cases = {
            "Senior Data Scientist SAP & AI": "personal_sap",
            "Inhouse Consultant SAP Business Analytics": "personal_sap",
            "Consultant SAP Second-Level-Support": "personal_sap",
            "Senior Data Analyst – CRM": "personal_crm",
            "Data Analyst Customer Relationship Management": "personal_crm",
            "Consultant Accounting & Finance Beratung": "personal_accounting",
            "Sachbearbeiter Buchhaltung": "personal_accounting",
            "Associate Director Statistical Programming": "personal_seniority",
            "Senior Principal ML Engineer": "personal_seniority",
            "Chief Technology Officer (CTO)": "personal_seniority",
            "Co-founder & CTO — AI-Native Consumer Platform": "personal_seniority",
            "CEO & Founder": "personal_seniority",
            '"CTO" – Data Platform': "personal_seniority",
            "Senior Credit Risk Modeling Manager": "personal_pricing_risk",
            "Consultant Data Science Credit Risk": "personal_pricing_risk",
            "Quant Analyst Risikomodell": "personal_pricing_risk",
            "Senior Mathematiker:in / Aktuar:in": "personal_mathematician",
            "Senior Bioinformatician": "personal_bioinformatics",
            "Fachspezialist/-in Reporting": "personal_role_labels",
            "Demand & Supply Planner": "personal_demand_planning",
            "(Senior) Manager Demand Planning Europe": "personal_demand_planning",
            "Masterandin Data Science": "study_role",
            "Pre-Master Programm Data Science": "study_role",
            "Bachelorstudium Angewandte Informatik": "study_role",
            "Bachelor of Science — Data Science": "study_role",
            "Juniorprofessur Data Science": "academic_role",
        }
        for title, rule in cases.items():
            with self.subTest(title=title):
                result = self.classify(title)
                self.assertEqual(result.outcome, "excluded")
                self.assertIn(rule, result.negative_matches)

    def test_preferences_do_not_become_broad_domain_or_seniority_bans(self):
        cases = {
            "Senior Data Scientist": "target",
            "Senior Data Engineer": "unmatched",
            "Staff ML Engineer": "unmatched",
            "Lead Data Scientist": "target",
            "Head of Data Science": "target",
            "Team Lead Marketing Analytics": "target",
            "Master Data Analyst": "target",
            "Data Scientist (Bachelor/Master)": "target",
            "Bachelor-Absolvent*in Data Scientist": "target",
            "Sustainability Data Analyst im Chief Sustainability Office (f/m/x)": "target",
            "Data Analyst, Director Office": "target",
            "Data Analyst, Chief Data Officer Office": "target",
            "Data Specialist – CDO Global Black Belt": "unmatched",
            "Business Performance Analyst, CEO Office": "unmatched",
            "Manager / Senior Manager Data Analytics & Digital CFO Services": "target",
            "Data Analyst / Account Executive": "target",
            "Senior Applied Scientist - Demand Forecast": "target",
            "Data Scientist, Demand Planning": "target",
            "Data Scientist - Hot Forming": "target",
            "IT Demand & Business Analyst": "unmatched",
            "Data Engineer, AWS Fraud Prevention": "unmatched",
            "Fraud Data Analyst": "target",
            "Research Data Engineer": "unmatched",
            "Senior Biostatistician": "target",
            "Data Scientist, Mathematical Modeling": "target",
            "Research Scientist, Drug Discovery": "unmatched",
            "Data Analyst HR Controlling": "target",
            "Data Scientist, Finance": "target",
        }
        for title, outcome in cases.items():
            with self.subTest(title=title):
                # Company names are not personal title bans.
                result = self.classify(title, company="SAP CRM Pricing Bioinformatics GmbH")
                self.assertEqual(result.outcome, outcome)
                self.assertFalse(any(name.startswith("personal_") for name in result.negative_matches))

    def test_preference_precedence_and_audit_evidence(self):
        title = "Principal Data Analyst SAP CRM"
        result = self.classify(title)
        self.assertEqual(result.outcome, "excluded")
        self.assertEqual(result.positive_matches, ("analytics_bi",))
        self.assertEqual(set(result.negative_matches), {
            "personal_sap", "personal_crm", "personal_seniority",
        })
        for mode, action in (("shadow", "shadow"), ("enforce", "archive")):
            decision = decision_for_result("linkedin:1", result, mode=mode)
            self.assertEqual(decision["decision_action"], action)
            self.assertEqual(decision["details"]["negative_matches"], list(result.negative_matches))
            self.assertEqual(decision["details"]["ruleset_version"], result.ruleset_version)
        for action in ("keep", "archive"):
            result = self.classify(title, manual_action=action)
            self.assertEqual(result.outcome, f"manual_{action}")
            self.assertIsNone(decision_for_result("linkedin:1", result, mode="enforce"))
