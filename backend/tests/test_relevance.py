"""Deterministic relevance policy and precedence tests."""

from __future__ import annotations

import unittest
import json
from pathlib import Path

from backend.src.config.settings import DEFAULT_UNWANTED_KEYWORDS
from backend.src.utils.relevance import classify_relevance, decision_for_result


UNWANTED = DEFAULT_UNWANTED_KEYWORDS.split(",")
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "relevance_cases.json"


class TestRelevanceClassifier(unittest.TestCase):
    def assert_outcome(self, title: str, outcome: str, family: str | None = None) -> None:
        result = classify_relevance(title, unwanted_keywords=UNWANTED)
        self.assertEqual(result.outcome, outcome, title)
        self.assertEqual(result.role_family, family, title)
        self.assertTrue(result.reason)

    def test_core_target_families(self) -> None:
        cases = {
            "Senior Data Scientist": "data_science",
            "Biostatistician": "data_science",
            "Data Analyst (m/w/d)": "analytics_bi",
            "Business Intelligence Consultant": "analytics_bi",
            "DataScientist Logistik (w/m/d)": "data_science",
            "Expert, Analytics & Data Science, Procurement RX": "data_science",
            "Team Lead Marketing Analytics": "analytics_bi",
            "Junior Consultant BI": "analytics_bi",
            "Sachbearbeiter/in Datenanalytik (w/m/d)": "analytics_bi",
        }
        for title, family in cases.items():
            with self.subTest(title=title):
                self.assert_outcome(title, "target", family)

    def test_balanced_relevance_fixture_is_stable_and_explainable(self) -> None:
        with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
            cases = json.load(fixture_file)["cases"]
        self.assertGreaterEqual(len(cases), 25)
        self.assertEqual(
            {case["outcome"] for case in cases},
            {"target", "excluded", "unrelated", "unmatched"},
        )
        for case in cases:
            with self.subTest(title=case["title"]):
                result = classify_relevance(
                    case["title"],
                    company=case["company"],
                    unwanted_keywords=UNWANTED,
                )
                self.assertEqual(result.outcome, case["outcome"])
                self.assertEqual(result.role_family, case.get("role_family"))
                self.assertTrue(result.reason)

    def test_tightly_bounded_unrelated_families_and_german_variants(self) -> None:
        for title in (
            "Disponent:in LKW-Transporte",
            "Disponent/in LKW-Transporte",
            "Hauptdisponentin",
            "Teamassistent (m/w/x) im Bereich Weiße Ware Haushaltskleingeräte",
            "Lagermitarbeiter",
            "LKW-Fahrer:in",
            "Pflegefachkraft",
            "Verkäufer/in",
            "Köchin",
            "Account Executive",
        ):
            with self.subTest(title=title):
                self.assert_outcome(title, "unrelated")

    def test_confirmed_clear_noise_families(self) -> None:
        cases = {
            "Sachbearbeiter (m/w/d)": "clerical_processing",
            "Vertragsbearbeiter (m/w/d)": "clerical_processing",
            "Logistiker:in (m/w/d)": "operational_logistics",
            "Versandmitarbeiter (m/w/d)": "operational_logistics",
            "Technischer Einkäufer (m/w/d)": "purchasing_procurement",
            "Procurement Specialist": "purchasing_procurement",
            "Cost Controller Construction (m/w/d)": "finance_controlling",
            "Sachbearbeiter Buchhaltung": "finance_controlling",
            "HR Payroll & Reporting": "human_resources",
            "People Business Partner": "human_resources",
            "Technical Support Specialist": "customer_technical_support",
            "Service Desk Agent": "customer_technical_support",
            "Datenbankadministrator PostgreSQL": "it_operations",
            "Produktionsmitarbeiter (m/w/d)": "production_trades",
            "Maschinenführer:in": "production_trades",
            "Account Manager Life Science": "sales_acquisition",
            "Junior Marketing Manager": "marketing_content",
        }
        for title, family in cases.items():
            with self.subTest(title=title):
                result = classify_relevance(title, unwanted_keywords=UNWANTED)
                self.assertEqual(result.outcome, "unrelated")
                self.assertIn(family, result.negative_matches)

    def test_data_and_analytics_targets_override_contextual_noise(self) -> None:
        for title in (
            "Data Analyst - Procurement",
            "Marketing Analytics Manager",
            "HR Data Analyst",
            "Data Analytics Controller",
            "Customer Support Data Scientist",
        ):
            with self.subTest(title=title):
                result = classify_relevance(title, unwanted_keywords=UNWANTED)
                self.assertEqual(result.outcome, "target")
                self.assertIn(result.role_family, {"data_science", "analytics_bi"})

    def test_scope_exclusions_win_before_target_rules(self) -> None:
        for title in (
            "AI Engineer",
            "Working Student Data Analyst",
            "Postdoctoral Data Scientist",
            "Professorin für Data Science",
            "Analytics Engineer",
            "Data Warehouse Engineer",
            "Scientific Software Developer",
            "AI Developer",
            "Data Warehouse Developer",
            "Business Intelligence Developer",
            "Entwickler (m/w/d)",
            "Systementwickler (m/w/d)",
            "Fertigungsingenieur:in",
            "Senior IT Architect Analytics",
            "Wirtschaftsinformatiker:in Data Analytics",
            "Engineering Manager",
            "Modern Data Management Masterclass - Dein Einstieg als Consultant",
            "Learning & Development Manager",
            "Data Science Trainer",
            "Ausbilderin für digitale Berufe",
            "Technical Recruiter",
            "Senior Headhunter",
            "Specialist People Acquisition & Analytics",
            "DHBW-Studium B.Sc. Data Science",
            "Commercial Graduate Programme",
            "Werkstudenten Business Development",
            "Werkstudierende Data Management",
            "Bachelorand Customer Analytics",
            "Wiss. MA - Survey Research",
            "Forschungsassistenz IREM",
        ):
            with self.subTest(title=title):
                self.assert_outcome(title, "excluded")

        academic_assistant = classify_relevance(
            "Research Assistant",
            company="Berlin University",
            unwanted_keywords=UNWANTED,
        )
        commercial_assistant = classify_relevance(
            "Research Assistant",
            company="Consumer Insights GmbH",
            unwanted_keywords=UNWANTED,
        )
        self.assertEqual(academic_assistant.outcome, "excluded")
        self.assertEqual(commercial_assistant.outcome, "unmatched")
        academic_associate = classify_relevance(
            "Research Associate - Data Analytics and Decision Making",
            company="UNSW",
            unwanted_keywords=UNWANTED,
        )
        self.assertEqual(academic_associate.outcome, "excluded")

    def test_target_phrases_win_over_contextual_noise(self) -> None:
        result = classify_relevance(
            "Data Analyst / Account Executive",
            unwanted_keywords=UNWANTED,
        )
        self.assertEqual(result.outcome, "target")
        self.assertEqual(result.role_family, "analytics_bi")
        self.assertIn("analytics_bi", result.positive_matches)
        self.assertIn("sales_acquisition", result.negative_matches)

        self.assert_outcome("Data Warehouse Engineer", "excluded")
        self.assert_outcome("Sales Analyst", "unmatched")
        self.assert_outcome("Research Scientist, Drug Discovery", "unmatched")
        self.assert_outcome("Junior Business Developer", "unmatched")

    def test_existing_noise_families_cover_compounds_without_hiding_adjacent_roles(self) -> None:
        for title, family in {
            "Finanzcontroller (m/w/d)": "finance_controlling",
            "Projektcontroller Umweltberatung": "finance_controlling",
            "Business Partner SCO Controlling": "finance_controlling",
            "Marketingmanager:in": "marketing_content",
            "Senior Social Media Performance Manager": "marketing_content",
            "Junior Manager Paid Social": "marketing_content",
        }.items():
            with self.subTest(title=title):
                result = classify_relevance(title, unwanted_keywords=UNWANTED)
                self.assertEqual(result.outcome, "unrelated")
                self.assertIn(family, result.negative_matches)

        for title in (
            "Business Analyst",
            "Junior Business Developer",
            "Data Project Manager",
            "Research Scientist, Drug Discovery",
        ):
            with self.subTest(title=title):
                self.assert_outcome(title, "unmatched")

    def test_non_target_consulting_is_unrelated(self) -> None:
        for title in (
            "Business Consultant",
            "Consultant Accounting & Finance Beratung",
            "Berater Energiewende und Elektromobilität",
            "Data & AI Strategy Consultant",
            "Research Consultant",
            "Senior Analyst – Data & AI Advisory Germany",
        ):
            with self.subTest(title=title):
                result = classify_relevance(title, unwanted_keywords=UNWANTED)
                self.assertEqual(result.outcome, "unrelated")
                self.assertIn("general_consulting", result.negative_matches)

    def test_explicit_target_consulting_stays_in_scope(self) -> None:
        for title, family in {
            "Data Science Consultant": "data_science",
            "Data Analytics Consultant": "analytics_bi",
            "Analytics Consultant": "analytics_bi",
            "Business Intelligence Consultant": "analytics_bi",
            "Senior Data Scientist im Consulting": "data_science",
        }.items():
            with self.subTest(title=title):
                result = classify_relevance(title, unwanted_keywords=UNWANTED)
                self.assertEqual(result.outcome, "target")
                self.assertEqual(result.role_family, family)
                self.assertIn("general_consulting", result.negative_matches)

    def test_implementation_heavy_consulting_cannot_be_rescued_by_analytics(self) -> None:
        for title in (
            "Inhouse Consultant SAP Business Analytics",
            "Consultant SAP Second-Level-Support",
            "Senior Consultant Databricks",
            "Sr. Prism Analytics Consultant - Workday Success Plans",
            "Senior Consultant Forensic eDiscovery - Digital Forensics & Analytics",
            "Senior Qlik Business Intelligence Consultant",
            "Consultant Business Intelligence SQL / PL / SQL",
            "Junior Consultant BI | OLAP, SQL, ETL",
            "Senior Consultant Data Lake & Analytics Solutions",
            "Senior Consultant Data & Analytics - Process Mining",
            "Technical Consultant Digital Analytics",
            "Data Science & Solutions Consultant",
            "AI Strategy Consultant – Data, Analytics, Cloud",
        ):
            with self.subTest(title=title):
                result = classify_relevance(title, unwanted_keywords=UNWANTED)
                self.assertEqual(result.outcome, "excluded")
                self.assertIn("consulting_implementation", result.negative_matches)

    def test_manual_decisions_are_highest_precedence(self) -> None:
        keep = classify_relevance(
            "Disponent:in LKW-Transporte",
            unwanted_keywords=UNWANTED,
            manual_action="keep",
        )
        archive = classify_relevance(
            "Data Scientist",
            unwanted_keywords=UNWANTED,
            manual_action="archive",
        )
        self.assertEqual(keep.outcome, "manual_keep")
        self.assertEqual(archive.outcome, "manual_archive")

    def test_shadow_and_enforcement_actions_are_explicit(self) -> None:
        unrelated = classify_relevance("Teamassistent", unwanted_keywords=UNWANTED)
        unmatched = classify_relevance("Sales Analyst", unwanted_keywords=UNWANTED)
        target = classify_relevance("Data Scientist", unwanted_keywords=UNWANTED)
        excluded = classify_relevance("Data Engineer", unwanted_keywords=UNWANTED)

        self.assertEqual(decision_for_result("1", unrelated, mode="shadow")["decision_action"], "shadow")
        self.assertEqual(decision_for_result("1", unrelated, mode="enforce")["decision_action"], "archive")
        self.assertEqual(decision_for_result("2", unmatched, mode="enforce")["decision_action"], "shadow")
        self.assertEqual(
            decision_for_result("2", unmatched, mode="enforce", include_unmatched=True)["decision_action"],
            "archive",
        )
        self.assertEqual(decision_for_result("3", target, mode="shadow")["decision_action"], "keep")
        self.assertEqual(decision_for_result("4", excluded, mode="shadow")["decision_action"], "shadow")
        self.assertEqual(decision_for_result("4", excluded, mode="enforce")["decision_action"], "archive")
        self.assertEqual(decision_for_result("4", excluded, mode="enforce")["filter_name"], "scope_exclusion")


if __name__ == "__main__":
    unittest.main()
