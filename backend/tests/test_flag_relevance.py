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
            "Lead Data Scientist": "personal_lead_founding",
            "Senior/Lead Data Engineer": "personal_lead_founding",
            "Data Science Lead": "personal_lead_founding",
            "Founding ML Engineer": "personal_lead_founding",
            "Team Lead Marketing Analytics": "personal_lead_founding",
            "Graduate Data Analyst": "personal_graduate",
            "Marketing Analytics Manager": "personal_commercial_analytics",
            "OnlineMarketing Data Analyst": "personal_commercial_analytics",
            "Data Scientist - E-Commerce": "personal_commercial_analytics",
            "B2B Analytics Specialist": "personal_commercial_analytics",
            "Data Analytics Controller": "personal_controlling",
            "Data Analyst HR Controlling": "personal_controlling",
            "Cost Controller Construction (m/w/d)": "personal_controlling",
            "Finanzcontroller (m/w/d)": "personal_controlling",
            "Projektcontroller Umweltberatung": "personal_controlling",
            "Business Partner SCO Controlling": "personal_controlling",
            "Marketingmanager:in": "personal_commercial_analytics",
            "Junior Marketing Manager": "personal_commercial_analytics",
            "Risikomanagerin und Datenanalystin": "personal_pricing_risk",
            "Insurance Risk & Actuarial Analyst": "personal_pricing_risk",
            "Liquidity Risk Data Analyst": "personal_pricing_risk",
            "Data Scientist, Computational Pathology": "personal_pathology",
            "MSAT Data Scientist": "personal_msat",
            "Data Analyst - Regulatory Reporting": "personal_regulatory_reporting",
            "Fraud Detection Manager": "personal_fraud_management",
            "AI Audit Specialist": "personal_audit",
            "IT Auditor | Data Scientist": "personal_audit",
            "Cloud FinOps Data Analyst": "personal_finops",
            "Cloud Cost Analytics Engineer": "personal_finops",
            "Wissenschaftliche*r Mitarbeiter*in Data Science": "academic_role",
            "Wissenschaftliche:r Mitarbeiter:in Data Science": "academic_role",
            "Wissenschaftliche(r) Mitarbeiter(in) Data Science": "academic_role",
            "Inhouse Consultant SAP Business Analytics": "personal_sap",
            "Consultant SAP Second-Level-Support": "personal_sap",
            "Senior Data Analyst – CRM": "personal_crm",
            "Data Analyst Customer Relationship Management": "personal_crm",
            "Consultant Accounting & Finance Beratung": "personal_accounting",
            "Sachbearbeiter Buchhaltung": "personal_accounting",
            "Associate Director Statistical Programming": "personal_seniority",
            "Senior Principal ML Engineer": "personal_seniority",
            "Head of Data Science": "personal_seniority",
            "Global Head of Applied AI": "personal_seniority",
            "Laboratory Data Analyst": "personal_lab_science",
            "Computational Biologist": "personal_lab_science",
            "Chemielaborantin": "personal_lab_science",
            "Data Scientist - Chemistry": "personal_lab_science",
            "Data Scientist - Clinical Research": "personal_clinical",
            "Medizinische Datenanalyse": "personal_clinical",
            "Cyber-Security Data Analyst": "personal_infrastructure",
            "Data Engineer - Kubernetes": "personal_infrastructure",
            "Data Analyst - Minijob": "personal_minijob",
            "Data Analyst - geringfügige Beschäftigung": "personal_minijob",
            "Quereinsteiger:in Data Analyst": "personal_career_change",
            "Wissenschaftliche/-r Mitarbeiter/-in": "academic_role",
            "Schülerpraktikum Data Science": "study_role",
            "ERP Traineeprogramm Business Analyst": "study_role",
            "Data Analyst - Traineeprogram": "study_role",
            "Data Scientist - befristet für zwei Jahre": "personal_fixed_term",
            "Data Engineer (9 months contract)": "personal_fixed_term",
            "Data Scientist - 12-Monatsvertrag": "personal_fixed_term",
            "Fixed-term Data Analyst": "personal_fixed_term",
            "Data Engineer / DevOps": "personal_software",
            "Data Scientist - Front-End": "personal_software",
            "Data Analyst - Backend": "personal_software",
            "Data Engineer / Softwareentwickler": "personal_software",
            "BI Architektin": "personal_software",
            "Technology Architect_ Azure": "personal_software",
            "Java Data Engineer": "personal_software",
            "Data Scientist - Robotik": "personal_automation_platforms",
            "Data Analyst - Dynamics-365": "personal_automation_platforms",
            "SPS-Programmierer": "personal_automation_platforms",
            "VP Data Science": "personal_seniority",
            "Gruppenleiterin Data Analytics": "personal_seniority",
            "NGS Data Analyst for Molecular Oncology": "personal_clinical",
            "Research Scientist, Drug Discovery": "personal_lab_science",
            "Funktionales Testen": "personal_testing",
            "Lead BI Analyst": "personal_lead_founding",
            "AI Value Stream Lead": "personal_lead_founding",
            "Data Scientist - Lead Optimization": "personal_lead_founding",
            "Abteilungsleiterin Data Science": "personal_seniority",
            "Bachelor-Absolvent*in Data Scientist": "personal_graduate",
            "Cloud Data Engineer": "personal_infrastructure",
            "Data Scientist (Azure ML)": "personal_infrastructure",
            "Kotlin Data Analyst": "personal_software",
            "Synera Specialist": "personal_automation_platforms",
            "Verification AI Expert": "personal_testing",
            "Machine Learning Engineer": "personal_ml_engineering",
            "Senior/Staff Machine Learning Engineer": "personal_ml_engineering",
            "Staff ML Engineer": "personal_ml_engineering",
            "AI/ML Engineer": "personal_ml_engineering",
            "ML / Data Engineer": "personal_ml_engineering",
            "Senior ML Data Engineer": "personal_ml_engineering",
            "Machine Learning (GenAI) Engineer": "personal_ml_engineering",
            "Machine-Learning Research Engineer": "personal_ml_engineering",
            "Machine Learning Ops Engineer": "personal_ml_engineering",
            "Engineer, Machine Learning": "personal_ml_engineering",
            "Data Scientist & ML Engineer": "personal_ml_engineering",
            "Consultant Data Science & ML Engineering": "personal_ml_engineering",
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

        # The existing AI Engineer keyword already excludes this combined title.
        self.assertEqual(self.classify("ML & AI Engineer").outcome, "excluded")

    def test_preferences_do_not_become_broad_domain_or_seniority_bans(self):
        cases = {
            "Senior Data Scientist": "target",
            "Senior Data Engineer": "unmatched",
            "Master Data Analyst": "target",
            "Data Scientist (Bachelor/Master)": "target",
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
            "Data Scientist, Finance": "target",
            "Senior / Staff Data Scientist": "target",
            "Data Scientist - Technical Leadership": "target",
            "Data Analyst - Founder's Office": "target",
            "Data Scientist - Market Data": "target",
            "Data Analyst for B2B products": "target",
            "Geospatial Data Analyst - GIS": "target",
            "GIS Portfolio Expert (w/m/d) U.S. Market": "unmatched",
            "Data Engineer - Audit Log Pipeline": "unmatched",
            "Scientific Data Analyst": "target",
            "Postgraduate Data Scientist": "target",
            "Data Scientist - Manufacturing": "target",
            "Data Scientist - AI Lab": "target",
            "Data Scientist - Physics-informed Machine Learning": "target",
            "Machine Learning Specialist": "unmatched",
            "Machine-Learning Specialist": "unmatched",
            "Deep Learning Specialist": "unmatched",
            "Data Analyst - Customer Service": "target",
            "Data Protection Specialist – Energy, Customer Service": "unmatched",
            "Data Quality Analyst": "unmatched",
            "Data Analyst - Quality Assurance": "target",
            "Data Engineer - ETL": "unmatched",
            "Teradata Data Engineer/ ETL": "unmatched",
            "Fachthemen-/Projektkoordinator (m/w/d)": "unmatched",
            "(Junior) Projektsteuer:in": "unmatched",
            "Data Scientist - Neural Architecture Search": "target",
            "Data Engineer (unbefristet)": "unmatched",
            "Data Analyst - Permanent Contract": "target",
            "Data Scientist - Contract Analytics": "target",
            "Data Scientist - JavaScript data visualization": "target",
            "Business Analyst Global Finance Technology & Data": "unmatched",
            "Senior Machine Learning Scientist": "target",
            "Senior ML Scientist, GenAI": "target",
            "Data Scientist - Machine Learning": "target",
            "Data Scientist - Model Evaluation": "target",
            "Data Analyst - SoundCloud": "target",
        }
        for title, outcome in cases.items():
            with self.subTest(title=title):
                # Company names are not personal title bans.
                result = self.classify(title, company="SAP CRM Pricing Bioinformatics GmbH")
                self.assertEqual(result.outcome, outcome)
                self.assertFalse(any(name.startswith("personal_") for name in result.negative_matches))

    def test_employer_opt_out_is_exact_and_manual_choices_win(self):
        result = self.classify("Ihre Aufgaben", company="University Positions")
        self.assertEqual(result.outcome, "excluded")
        self.assertIn("personal_academic_employer", result.negative_matches)
        for company in ("Berlin University", "University Positions Research GmbH", "Marketing Commerce Group",
                        "Medical AI Lab", "Chemistry Research Group", "Kubernetes Security GmbH",
                        "Software Robotics Dynamics 365 GmbH", "Azure Cloud Machine Learning Engineering GmbH"):
            self.assertEqual(self.classify("Data Analyst", company=company).outcome, "target")
        self.assertEqual(self.classify("Data Analyst", company="University Positions", manual_action="keep").outcome, "manual_keep")
        for title in ("Lead Data Scientist", "MSAT Data Expert", "AI Audit Specialist", "Graduate Data Analyst",
                      "Head of Data Science", "Clinical Data Scientist", "Lab Compute Analyst", "IT-Supportexperte",
                      "VP Data Analytics", "Data Engineer / DevOps", "Data Analyst (befristet)",
                      "Machine Learning Engineer", "ML Engineer", "Cloud Data Engineer"):
            self.assertEqual(self.classify(title, manual_action="keep").outcome, "manual_keep")

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
