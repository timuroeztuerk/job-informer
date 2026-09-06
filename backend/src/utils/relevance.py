"""Explainable title relevance classification with no AI or numeric scoring."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Optional

from .filtering import (
    match_academic_title_pattern,
    match_keyword_filter,
    match_study_title_pattern,
    normalize_text,
)


RULESET_VERSION = "2026-09-06.1"
VALID_RELEVANCE_MODES = {"shadow", "enforce"}

CONSULTING_TITLE_PATTERN = (
    r"(?:\bconsult(?:ant|ants|ing|ancy)\w*\b|"
    r"\b\w*berater(?:(?::|/)?in)?\w*\b|"
    r"\b\w*beratung\w*\b|\badvisory\b)"
)
CONSULTING_IMPLEMENTATION_PATTERN = (
    r"(?:\b(?:sap|sac|workday|prism|qlik|cognos|cubeware|databricks|snowflake|denodo|"
    r"salesforce|servicenow|azure|aws|gcp)\b|"
    r"\b(?:power[\s/-]+platform|data[\s/-]+warehous\w*|data[\s/-]+lake|"
    r"data[\s/-]+platform\w*|data[\s/-]+model(?:ing|ling)|process[\s/-]+mining|"
    r"e[\s/-]*discovery|digital[\s/-]+forensics?)\b|"
    r"\b(?:olap|etl|sql|pl[\s/-]*sql)\b|"
    r"\btechnical[\s/-]+consult\w*\b|"
    r"\bsolutions?[\s/-]+consult\w*\b|"
    r"\bconsult\w*[\s/-]+solutions?\b|"
    r"\bforensic\w*\b|\bcloud\b)"
)


@dataclass(frozen=True)
class RelevanceRule:
    name: str
    family: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class RelevanceResult:
    outcome: str
    role_family: Optional[str]
    matched_value: Optional[str]
    reason: str
    ruleset_version: str = RULESET_VERSION
    matches: tuple[str, ...] = ()
    positive_matches: tuple[str, ...] = ()
    negative_matches: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


TARGET_RULES: tuple[RelevanceRule, ...] = (
    RelevanceRule(
        "data_science",
        "data_science",
        (
            r"\bdata[\s/-]*scientist\w*\b",
            r"\bdata[\s/-]+science\b",
            r"\bdecision[\s/-]+scientist\w*\b",
            r"\bapplied[\s/-]+scientist\w*\b",
            r"\b(?:machine[\s/-]+learning|ml)[\s/-]+scientist\w*\b",
            r"\b(?:bio)?statistiker\w*\b",
            r"\b(?:bio)?statistician\w*\b",
        ),
    ),
    RelevanceRule(
        "analytics_bi",
        "analytics_bi",
        (
            r"\bdata[\s/-]+analyst\w*\b",
            r"\bdata(?:\s*/\s*|[\s/-]+)business[\s/-]+analyst\w*\b",
            r"\bdata[\s/-]+analytics\b",
            r"\banalytics\b",
            r"\banalytics[\s/-]+engineer\w*\b",
            r"\bbusiness[\s/-]+intelligence\b",
            r"\bpower[\s/-]+bi\b",
            r"\bbi[\s/-]+(?:analyst|developer|engineer|consultant)\w*\b",
            r"\bbi[\s/-]+manager\w*\b",
            r"\bconsultant[\s/-]+bi\b",
            r"\breporting[\s/-]+analyst\w*\b",
            r"\binsights?[\s/-]+analyst\w*\b",
            r"\bdata[\s/-]+visuali[sz]ation[\s/-]+analyst\w*\b",
            r"\bweb(?:site)?[\s/-]*analyst\w*\b",
            r"\bdatenanalyst\w*\b",
            r"\bdatenanalyse\w*\b",
            r"\bdatenanalytik\w*\b",
        ),
    ),
)


ADJACENT_TECHNICAL_RULES: tuple[RelevanceRule, ...] = (
    RelevanceRule(
        "audited_target_engineering",
        "target_engineering",
        (
            r"\banalytics[\s/-]+engineer\w*\b",
            r"\bbusiness[\s/-]+intelligence[\s/-]+engineer\w*\b",
            r"\bbi[\s/-]+engineer\w*\b",
            r"\bbusiness[\s/-]+data[\s/-]+analyst\w*\b",
        ),
    ),
    RelevanceRule(
        "data_engineering_adjacent",
        "data_engineering",
        (
            r"\bdata[\s/-]+engin(?:eer|eering)\w*\b",
            r"\bdata[\s/-]+(?:platform|warehouse|lake)[\s/-]+engineer\w*\b",
        ),
    ),
    RelevanceRule(
        "ai_engineering_adjacent",
        "ai_engineering",
        (
            r"\bai[\s/-]+engineering\b",
            r"\bai[\s/-]+(?:delivery|solutions?|platform|computer[\s/-]+vision)[\s/-]+engineer\w*\b",
            r"\b(?:product|forward[\s/-]+deployed)[\s/-]+engineer\w*[^a-z0-9]+(?:ai|genai)\b",
            r"\bai[\s/-]+tech[\s/-]+consult\w*\b",
            r"\bconsult\w*[\s/-]+(?:genai|agentic[\s/-]+ai)\b",
        ),
    ),
)


# Reviewed personal preferences from the September 5 flagged examples. These
# match titles, override target/adjacent signals, and never read flag text
# at runtime. Adding a flag does not silently create a new blacklist rule.
# The one exact employer opt-out below was explicitly requested in a flag note.
PERSONAL_EXCLUDED_COMPANIES = {"university positions"}

# Employer context identifies the reviewed shop-floor Client Advisor role; it
# never excludes other roles at these employers or arbitrary advisory titles.
RETAIL_ADVISOR_EMPLOYERS = {"bucherer", "bucherer ag", "prada group"}
RETAIL_ADVISOR_RULE = RelevanceRule(
    "retail_client_advisor", "retail_hospitality", (r"\bclient[\s/-]+advis(?:or|er)\w*\b",),
)

PERSONAL_EXCLUSION_RULES: tuple[RelevanceRule, ...] = (
    RelevanceRule("personal_sap", "SAP-focused roles", (r"\bsap\b",)),
    RelevanceRule(
        "personal_crm", "CRM-focused roles",
        (r"\bcrm\b", r"\bcustomer[\s/-]+relationship[\s/-]+management\b"),
    ),
    RelevanceRule(
        "personal_seniority", "head, director, principal or executive roles",
        (
            r"\bhead[\s/-]+of\b",
            r"\b(?:president|prasident|vp|svp|evp)\b(?![\s/-]+office\b)",
            r"\b(?:gruppen|abteilungs)leiter\w*\b",
            r"\b(?:director|principal)\b(?![\s/-]+(?:office|support)\b)",
            r"\bchief(?:[\s/-]+[a-z]+){0,4}[\s/-]+officer\b(?![\s/-]+office\b)",
            r"^\W*(?:(?:co[\s-]?founder|founder)[\s&/,;:-]+)?(?:ceo|cto|cfo|coo|cdo|cio|cmo|cpo|cso|c[\s-]+level)\b(?![\s/-]+(?:office|services|advisory|analyst|specialist|support)\b)",
            r"\b(?:ceo|cto|cfo|coo|cdo|cio|cmo|cpo|cso)[\s&/,-]+(?:co[\s-]?founder|founder)\b",
            r"^(?:senior[\s/-]+)?executive\b",
        ),
    ),
    RelevanceRule(
        "personal_accounting", "accounting roles",
        (r"\b(?:accounting|accountant\w*|buchhalter\w*|buchhaltung|bilanzbuchhalter\w*)\b",),
    ),
    RelevanceRule(
        "personal_pricing_risk", "pricing or financial risk roles",
        (
            r"\bpricing\b",
            r"\brisk[\s/&,-]+(?:analyst\w*|analytics|analysis|modeller\w*|modeling|modelling|management|manager|consulting|performance)\b",
            r"\b(?:credit|market|investment|financial|insurance|liquidity|quant(?:itative)?)[\s/-]+risk\b",
            r"\boperational[\s/-]+risk[\s/-]+(?:associate|officer|analyst|manager)\w*\b",
            r"\b(?:preisanalys|risikoanalys|risikomodell|risikomanag)\w*\b",
        ),
    ),
    RelevanceRule(
        "personal_mathematician", "mathematician roles",
        (r"\b(?:mathematician|mathematiker)\w*\b",),
    ),
    RelevanceRule(
        "personal_bioinformatics", "bioinformatics roles",
        (r"\bbioinformatic(?:ian|s)\b", r"\bbioinformatik\w*\b"),
    ),
    RelevanceRule(
        "personal_role_labels", "Fachspezialist, Fachkraft or Kaufmann roles",
        (r"\bfachspezialist(?:(?::|/)?in)?\b", r"\bfachkraft\w*\b", r"\bkauf(?:mann|frau)\b"),
    ),
    RelevanceRule(
        "personal_demand_planning", "demand planning roles",
        (
            r"\bdemand[\s/-]+(?:(?:and|&)[\s/-]+supply[\s/-]+)?planner\b",
            r"\bdemand[\s/-]+planning[\s/-]+(?:expert|manager|specialist|lead|coordinator)\b",
            r"\b(?:manager|expert|specialist|head|lead|director)[\s/-]+(?:of[\s/-]+)?demand[\s/-]+planning\b",
        ),
    ),
    RelevanceRule(
        "personal_hot_forming_science", "hot-forming scientist roles",
        (r"^(?:(?:senior|lead|junior)[\s/-]+)?scientist\b.*\bhot[\s/-]+forming\b",),
    ),
    RelevanceRule(
        "personal_lead_founding", "lead or founding roles",
        (
            r"\blead\b",
            r"\bfounding[\s/-]+(?:data|quant\w*|research\w*|ai|ml|machine|analytics|business|software|product|engineer\w*)\b",
            r"\bleitend\w*(?:\([a-z]+\))?[\s/-]+mitarbeiter\w*\b",
        ),
    ),
    RelevanceRule("personal_graduate", "graduate roles", (
        r"\bgraduate[\s/-]+(?:data|quant\w*|research\w*|analyst\w*|scientist\w*|engineer\w*|consultant\w*)\b",
        r"\babsolvent\w*\b",
    )),
    RelevanceRule(
        "personal_commercial_analytics", "commerce, marketing or B2B analytics roles",
        (
            r"\b(?:e[\s-]?commerce|ecom|commerce|merchandising)\b",
            r"\b(?:online|digital|performance)?marketing\w*\b",
            r"\bb2b[\s/-]+(?:analytics|analysis|analyst\w*)\b",
        ),
    ),
    RelevanceRule("personal_controlling", "controller or controlling roles", (r"\b\w*(?:controller|controlling)\w*\b",)),
    RelevanceRule("personal_pathology", "pathology roles", (r"\bpatholog\w*\b",)),
    RelevanceRule("personal_msat", "manufacturing science and technology (MSAT) roles", (r"\bmsat\b",)),
    RelevanceRule(
        "personal_regulatory_reporting", "bank or regulatory reporting roles",
        (r"\bbanksteuer\w*\b", r"\b(?:regulatory|prudential|risk|aufsichtsrechtlich\w*)[\s/-]+reporting\b"),
    ),
    RelevanceRule(
        "personal_fraud_management", "fraud management roles",
        (r"\bfraud[\s/-]+(?:(?:detection|prevention)[\s/-]+)?(?:manager\w*|management|officer\w*|specialist\w*)\b",),
    ),
    RelevanceRule(
        "personal_audit", "audit and revision roles",
        (
            r"\baudit(?:ing)?[\s/-]+(?:specialist\w*|manager\w*|analyst\w*|consultant\w*)\b",
            r"\b(?:internal|it|ai|technical)[\s/-]+audit(?:ing)?\b",
            r"\b(?:auditor\w*|revisor\w*)\b",
            r"\binternal[\s/-]+controls?\b",
        ),
    ),
    RelevanceRule("personal_finops", "FinOps and cloud cost roles", (r"\bfinops\b", r"\bcloud[\s/-]+(?:cost|kosten)\w*\b")),
    RelevanceRule(
        "personal_lab_science", "laboratory, chemistry, biology or physicist roles",
        (
            r"\b(?:(?:geo)?physicist|(?:geo)?physiker|chemiker|chemist|biologist|biologe|mikrobiologe|microbiologist)\w*\b",
            r"\b(?:chemistry|chemie\w*|biology|biologie|microbiology|mikrobiologie)\b",
            r"\b\w*laborant\w*\b",
            r"\b(?:lab|laboratory)[\s/-]+(?:compute|technician|analyst|scientist|assistant|manager|automation|data)\b",
            r"\blabor(?:atorium)?[\s/-]*(?:mitarbeiter|analytik|automation|leiter)\w*\b",
            r"\blateral[\s/-]+flow[\s/-]+assay\w*\b",
            r"\b(?:natural[\s/-]+sciences?|naturwissenschaft\w*|molecular|molekular\w*|genomics?|genetik\w*|drug[\s/-]+discovery)\b",
        ),
    ),
    RelevanceRule(
        "personal_clinical", "medical or clinical roles",
        (
            r"\b(?:medical|clinical|klinisch\w*|medizinisch\w*|cardiology|kardiolog\w*|oncolog\w*|onkolog\w*)\b",
            r"\b(?:physiotherapeut|ergotherapeut|arzt|facharzt)\w*\b",
        ),
    ),
    RelevanceRule(
        "personal_infrastructure", "cybersecurity, Kubernetes, Cloud or Azure roles",
        (r"\bcyber[\s-]*security\b", r"\b(?:kubernetes|cloud|azure)\b"),
    ),
    RelevanceRule("personal_minijob", "minijobs", (r"\bmini[\s-]*jobs?\b", r"\bgeringfugig\w*\b")),
    RelevanceRule("personal_career_change", "Quereinsteiger roles", (r"\bquereinsteiger\w*\b",)),
    RelevanceRule(
        "personal_software", "software, DevOps, architecture or programming roles",
        (
            r"\b(?:software\w*|devops|mlops|java|kotlin|programmierer\w*|programmer\w*)\b",
            r"\b(?:back|front)[\s-]*end\b",
            r"\barchitects?(?=\b|_)",
            r"\barchitekt(?:in|en)?\b",
            r"\bdatenarchitekt(?:in|en)?\b",
        ),
    ),
    RelevanceRule(
        "personal_testing", "testing roles",
        (
            r"\btest(?:er\w*|ing|en|manager\w*|management|automatisierung|automation)?\b",
            r"\bverification[\s/-]+(?:ai[\s/-]+)?(?:expert|specialist|manager)\w*\b",
        ),
    ),
    RelevanceRule(
        "personal_automation_platforms", "robotics, SPS, Dynamics 365, Odoo, n8n or Synera roles",
        (r"\b(?:robotic\w*|roboter\w*|robotik\w*|sps|odoo|n8n|synera)\b", r"\bdynamics[\s/-]*365\b"),
    ),
    RelevanceRule(
        "personal_fixed_term", "fixed-term contracts",
        (
            r"\b(?:fixed[\s-]+term|befristet\w*)\b",
            r"\b\d+[\s-]*(?:months?|monat(?:en?|s)?)[\s-]*(?:contract|vertrag)\b",
        ),
    ),
    RelevanceRule(
        "personal_ml_engineering", "machine learning engineering roles",
        (
            r"\b(?:machine[\s/-]*learning|ml)(?:[\s/-]+\([^)]*\))?(?:[\s/&-]+(?:data|ai|ops|research|platform|infrastructure)){0,2}[\s/-]+engineer\w*\b",
            r"\bengineer\w*[\s,:/-]+(?:(?:for|in)[\s/-]+)?(?:machine[\s/-]*learning|ml)\b",
        ),
    ),
)


SCOPE_EXCLUSION_RULES: tuple[RelevanceRule, ...] = (
    RelevanceRule(
        "restricted_intern_conversion",
        "intern-only conversion programme",
        (
            r"\b(?:applicable|open|available)[\s/-]+(?:for|to)\b[^)]{0,100}\binterns?[\s/-]+only\b",
            r"\b(?:former|returning)[\s/-]+interns?[\s/-]+only\b",
        ),
    ),
    RelevanceRule(
        "legacy_placeholder",
        "legacy_placeholder",
        (r"\barchived[\s/-]+placeholder\b",),
    ),
    RelevanceRule(
        "engineering_role",
        "engineering",
        (
            r"\bengin(?:eer|er)\w*\b",
            r"\bengineering\b",
            r"\b\w*ingenieur\w*\b",
            r"\b(?:devops|mlops)\b",
            r"\bsite[\s/-]+reliability\b",
            r"\b(?:software|application|app|web|mobile|frontend|front[\s-]+end|backend|back[\s-]+end|full[\s-]+stack|data|database|cloud|platform|python|java|javascript|typescript|scala|sql|sap|salesforce)[\s/-]+developer\w*\b",
            # A bare technical "Developer" title is an engineering role. Keep
            # "Business Developer" unmatched because it is an adjacent
            # commercial title rather than a software-engineering title.
            r"(?<!business )\bdeveloper\w*\b",
            r"\b\w*entwickler\w*\b",
            r"\b(?:data|software|cloud|solutions?|enterprise|system|technical|tech|it|ai)[\s/-]+architect\w*\b",
            r"\b\w*techniker\w*\b",
            r"\b\w*informatiker\w*\b",
            r"\bprogrammer\w*\b",
            r"\bkonstrukteur\w*\b",
            r"\b(?:software[\s/-]+test|test[\s/-]+automation|penetrationstest)\w*\b",
        ),
    ),
    RelevanceRule(
        "training_recruitment",
        "training_recruitment",
        (
            r"\bmasterclass\b",
            r"\bbootcamp\b",
            r"\b(?:train(?:er|ing)|instructor)\w*\b",
            r"\b(?:educator|teacher)\w*\b",
            r"\b(?:ausbilder|weiterbildung|schulung)\w*\b",
            r"\blearning[\s/&-]+development\b",
            r"^(?:(?:senior|junior)[\s/-]+)?learning[\s/-]+specialist\w*\b",
            r"\bpersonalentwickl\w*\b",
            r"\b(?:graduate|rotation|entry|development)[\s/-]+program(?:me)?\b",
            r"\bprogram(?:me)?[\s/-]+data[\s/-]+science\b",
            r"\bmentor\w*\b",
            r"\b(?:career|recruiting|recruitment|hiring)[\s/-]+(?:event|day|fair)\b",
            r"\b(?:(?:talent|people)[\s/-]+(?:community|pool|network|sourcer|acquisition))\b",
            r"\b(?:recruiter|recruiting|recruitment|headhunter)\w*\b",
            r"\b(?:open|unsolicited)[\s/-]+application\b",
            r"\binitiativbewerbung\b",
        ),
    ),
)


UNRELATED_RULES: tuple[RelevanceRule, ...] = (
    RelevanceRule(
        "logistics_dispatch",
        "logistics_dispatch",
        (
            r"\b(?:haupt|material)?disponent(?:(?::|/)?in)?\b",
            r"\b(?:truck|transport|freight)[\s/-]+dispatcher\b",
            r"\bfuhrparkdisponent\w*\b",
            r"\beinsatzplaner\w*\b",
        ),
    ),
    RelevanceRule(
        "administrative_assistance",
        "administrative_assistance",
        (
            r"\bteamassistent\w*\b",
            r"\bteamassistenz\w*\b",
            r"\bit[\s/-]+projektassistenz\w*\b",
            r"\b(?:kaufmannisch\w*[\s/-]+)?assistenz\b",
            r"\bassistenz[\s/-]+der[\s/-]+geschaftsleitung\b",
            r"\bexecutive[\s/-]+assistant\b",
            r"\boffice[\s/-]+assistant\b",
            r"\bsekretari(?:at|atskraft)\b",
            r"\bassistent\w*[\s/-]+(?:der[\s/-]+)?geschaftsleitung\b",
        ),
    ),
    RelevanceRule(
        "clerical_processing",
        "clerical_processing",
        (
            r"\bsachbearbeit\w*\b",
            r"\bvertragsbearbeit\w*\b",
            r"\bauftrag(?:s)?bearbeit\w*\b",
            r"\b(?:administrative|administration)[\s/-]+(?:assistant|officer|specialist|coordinator)\w*\b",
            r"\bback[\s-]?office\b",
            r"\boffice[\s/-]+manager\w*\b",
            r"\brechnungspruf\w*\b",
            r"\bpost[\s/-]+und[\s/-]+posterfassung\b",
        ),
    ),
    RelevanceRule(
        "operational_logistics",
        "operational_logistics",
        (
            r"\blogistiker\w*\b",
            r"\blogistikmitarbeiter\w*\b",
            r"\bmitarbeiter\w*[\s/-]+(?:in[\s/-]+der[\s/-]+)?logistik\b",
            r"\bmitarbeiter(?:in|innen|[:*/_]in|/-in|\(in\))?[\s/-]+(?:in[\s/-]+der[\s/-]+)?logistik\b",
            r"\bversandmitarbeiter\w*\b",
            r"\bkommissionierer\w*\b",
            r"\bstockist\b",
            r"\b(?:shipping|logistics?)[\s/-]+(?:clerk|coordinator|operative|worker|associate)\w*\b",
            r"\bmitarbeiter\b.*\b(?:lager|versand|transportmanagement)\b",
            r"^(?:package[\s/-]+|material[\s/-]+)?handler(?:\s*\([^)]*\))?$",
            r"\b(?:global[\s/-]+)?manager[\s/-]+inventor(?:y|ies)\b",
            r"\binventor(?:y|ies)[\s/-]+manager\w*\b",
            r"\bwarehouse[\s/-]+process[\s/-]+specialist\w*\b",
            r"\bkapazitatsmanager\w*[\s/-]+fahrplan\b",
        ),
    ),
    RelevanceRule(
        "purchasing_procurement",
        "purchasing_procurement",
        (
            r"\beinkaufer\w*\b",
            r"\bbuyer\w*\b",
            r"\bprocurement[\s/-]+(?:manager|specialist|officer|lead|coordinator)\w*\b",
            r"\bpurchasing[\s/-]+(?:manager|specialist|officer|lead|coordinator)\w*\b",
        ),
    ),
    RelevanceRule(
        "finance_controlling",
        "finance_controlling",
        (
            r"\b\w*(?:controller|controlling)\w*\b",
            r"\b(?:mitarbeiter|sachbearbeit|teamleiter|fachreferent|professional|specialist)\w*.*\bcontrolling\b",
            r"\baccountant\w*\b",
            r"\bbuchhalt\w*\b",
            r"\baccounts?[\s/-]+(?:payable|receivable)\b",
            r"\bbilling[\s/-]+assistant\w*\b",
            r"\bdatev[\s/-]+spezialist\w*\b",
            r"\bfp\s*&\s*a\b",
            r"\btax[\s/-]+analyst\w*\b",
            r"\bwissensmanagement[\s/-]+tax\b",
        ),
    ),
    RelevanceRule(
        "financial_operations_compliance", "financial_operations_compliance",
        (
            r"\bexport[\s/-]+control[\s&/-]+customs\b",
            r"\bfinancial[\s/-]+crime[\s/-]+prevention[\s/-]+officer\w*\b",
            r"\bportfoliomanager\w*\b",
            r"\bportfolio[\s/-]+manager\w*\b",
            r"\bswift[\s/-]+specialist\w*\b",
        ),
    ),
    RelevanceRule(
        "human_resources",
        "human_resources",
        (
            r"\bhuman[\s/-]+resources?\b",
            r"\bhr\b",
            r"\bpayroll\b",
            r"\bcompensation\b",
            r"\bpeople[\s/-]+(?:business[\s/-]+partner|operations|manager)\b",
            r"\bpersonal(?:referent|sachbearbeit|management|leitung|wesen|administration)\w*\b",
        ),
    ),
    RelevanceRule(
        "customer_technical_support",
        "customer_technical_support",
        (
            r"\b(?:it|technical|customer|product|business)[\s/-]+support\w*\b",
            r"\b(?:l[123]|[123](?:st|nd|rd)?[\s-]+level)[\s/-]+support\w*\b",
            r"\bcustomer[\s/-]+service[\s/-]+(?:assistant|coordinator|agent|representative|advisor|specialist|manager)\w*\b",
            r"\bapplication[\s/-]+specialist\b.*\bcustomer[\s/-]+service\b",
            r"\bcall[\s/-]+cent(?:er|re)\b",
            r"\bkundenbetreuer\w*\b",
            r"\b(?:service|help|support)[\s/-]*desk\b",
            r"\bkundenservice\b",
            r"\bsupport[\s/-]+(?:agent|specialist|officer)\w*\b",
            r"\bdeskside[\s/-]+support\b",
        ),
    ),
    RelevanceRule(
        "it_operations",
        "it_operations",
        (
            r"\b(?:database|system|network)[\s/-]*administrator\w*\b",
            r"\b(?:it|cloud|linux)[\s/-]+admin(?:istrator)?\w*\b",
            r"\badmin(?:istrator)?\b.*\bit[\s/-]+landschaft\b",
            r"\bdatenbankadministrator\w*\b",
            r"\b(?:it|sap|erp|m365)[\s/-]+consultant\w*\b",
            r"\btechnical[\s/-]+consultant\w*[\s/-]+(?:it|sap|erp|m365)\b",
            r"\bconsultant\w*[\s/-]+(?:it|sap|erp|m365)\b",
            r"\bit[\s/-]+(?:system|onsite|operations?)[\s/-]+specialist\w*\b",
            r"\bsystembetreuer\w*\b",
            r"\b(?:cybersecurity|security)[\s/-]+analyst\w*\b",
            r"\bsoc[\s/-]+analyst\w*\b",
            r"\btechnical[\s/-]+security[\s/-]+expert\w*\b",
            r"\bpatch[\s-]*management[\s/-]+(?:expert|specialist|spezialist)\w*\b",
            r"\b(?:it[\s/-]+)?anwendungsbetreuer\w*\b",
            r"\bkis[\s/-]+admin(?:istrator)?\w*\b",
            r"\binformatik[\s/-]+allrounder\w*\b",
            r"\b(?:junior[\s/-]+)?it[\s/-]+specialist\w*\b",
            r"\bkey[\s/-]+user\b.*\b(?:csb|mes|verwaltungs|kundenmanagement)\b",
        ),
    ),
    RelevanceRule(
        "production_trades",
        "production_trades",
        (
            r"\bproduktionsmitarbeiter\w*\b",
            r"\bmaschinenfuhrer\w*\b",
            r"\bfacharbeiter\w*\b",
            r"\b(?:production|manufacturing)[\s/-]+operator\w*\b",
            r"\b(?:arbeitsvorbereiter|arbeitsplaner|teilereinigung)\w*\b",
            r"\b(?:forstwirt|gartner|vegetationspfleger)\b",
            r"\bmittelspannungsschaltanlagen\b",
            r"\boptical[\s/-]+designer\w*\b",
            r"\b(?:cnc[\s/-]+)?maschi(?:e)?nenbediener\w*\b",
            r"\bproduktionsplaner\w*\b",
            r"\bschichtleiter\w*\b",
        ),
    ),
    RelevanceRule(
        "construction_infrastructure", "construction_infrastructure",
        (
            r"\bprojektleiter\w*(?:[:*/]in)?[\s/-]+oberbau\w*\b",
            r"\bbim[\s/-]+modellierer\w*\b",
            r"\bdigitalisierungsmanager\w*[\s/-]+gis[\s/-]+bim\b",
            r"\bcalculateur\w*[\s/-]+construction\b",
            r"\bforensic[\s/-]+delay[\s/-]+planner\w*\b",
        ),
    ),
    RelevanceRule(
        "specialist_science", "specialist_science",
        (r"\bmeteorolog(?:ist|e|in)\w*\b", r"\bspacecraft[\s/-]+analyst\w*\b"),
    ),
    RelevanceRule(
        "physical_warehouse",
        "physical_warehouse",
        (
            r"\blagermitarbeiter\w*\b",
            r"\blagerhelfer\w*\b",
            r"\bwarehouse[\s/-]+(?:operative|worker|associate)\w*\b",
            r"\bpick(?:er|ing)[\s/-]*(?:and|&)?[\s/-]*pack(?:er|ing)?\b",
            r"\bverlader\w*\b",
        ),
    ),
    RelevanceRule(
        "driving_delivery",
        "driving_delivery",
        (
            r"\b(?:lkw|bus|taxi)?[\s/-]*fahrer(?:(?::|/)?in)?\b",
            r"\bdelivery[\s/-]+driver\b",
            r"\bcourier\b",
            r"\bzusteller(?:(?::|/)?in)?\b",
        ),
    ),
    RelevanceRule(
        "clinical_care",
        "clinical_care",
        (
            r"\bpflegefachkraft\w*\b",
            r"\b(?:registered[\s/-]+)?nurse\b",
            r"\bcaregiver\b",
            r"\baltenpfleger\w*\b",
        ),
    ),
    RelevanceRule(
        "retail_hospitality",
        "retail_hospitality",
        (
            r"\bcashier\b",
            r"\bverkaufer(?:(?::|/)?in)?\b",
            r"\brestaurant[\s/-]+service\b",
            r"\b(?:koch|kochin|cook)\b",
            r"\bhotel[\s/-]+reception\w*\b",
            r"\bservicemitarbeiter\w*\b",
            r"\bhauswirtschaft\w*\b",
            r"\bgastronomie\b",
            r"\bmitarbeiter[\s/-]+verkauf\b",
            r"\bcenter[\s/-]+mitarbeiter[\s/-]+esports\b",
            r"\bbartender\w*\b",
            r"\bbar[\s-]*mitarbeiter\w*\b",
            r"\bfront[\s/-]+office[\s/-]+agent\w*\b",
            r"\bnight[\s/-]+audit(?:or)?\b",
            r"\breservierungsmitarbeiter\w*\b",
            r"\bconference[\s&/-]+guest[\s/-]+service\b",
            r"\bmagaziner\w*\b",
            r"\bcounter[\s/-]+manager\w*[\s/-]+(?:parfums?|cosmetics?)\b",
            r"\b(?:outlet|boutique|retail|jewell?ery)[\s/-]+client[\s/-]+advis(?:or|er)\w*\b",
        ),
    ),
    RelevanceRule(
        "sales_acquisition",
        "sales_acquisition",
        (
            r"\bsales[\s/-]+(?:development[\s/-]+)?representative\b",
            r"\baccount[\s/-]+executive\b",
            r"\bbusiness[\s/-]+development[\s/-]+representative\b",
            r"\bfield[\s/-]+sales\b",
            r"\baußendienstmitarbeiter\w*\b",
            r"\b(?:sales|account)[\s/-]+manager\w*\b",
            r"\bvertriebs(?:mitarbeiter|spezialist|manager|innendienst)\w*\b",
            r"\bverkaufsinnendienst\w*\b",
            r"\boffice[\s/-]+mitarbeiter[\s/-]+(?:sales|vertrieb)\b",
            r"\bcustomer[\s/-]+success[\s/-]+(?:manager|specialist)\w*\b",
            r"\bmarktmanager\w*\b",
            r"\b(?:pos[\s/-]+)?promoter(?:in|[:*/]in)?\b",
            r"\bretention[\s/-]+agent\w*\b",
            r"\binside[\s/-]+sales\b",
            r"\bmitarbeiter(?:in|innen|[:*/_]in|/-in|\(in\))?[\s/-]+sales[\s&/-]+operations\b",
        ),
    ),
    RelevanceRule(
        "quality_assurance", "quality_assurance",
        (
            r"\bquality[\s/-]+assurance[\s/-]+(?:specialist|manager|inspector)\w*\b",
            r"\bqc[\s/-]+analyst\w*\b",
            r"\bquality[\s/-]+assurance[\s&/-]+sample[\s/-]+technician\w*\b",
            r"\bprobenregistrierung\b",
        ),
    ),
    RelevanceRule(
        "product_information_management", "product_information_management",
        (r"\bpim[\s/-]+(?:specialist|manager|administrator)\w*\b",),
    ),
    RelevanceRule(
        "marketing_content",
        "marketing_content",
        (
            r"\b\w*marketing(?:manager|specialist|lead)\w*\b",
            r"\b\w*marketing\w*[\s/-]+(?:manager|specialist|lead)\w*\b",
            r"\b(?:social[\s/-]+media|content)\b.*\b(?:creator|manager|specialist|producer|designer)\w*\b",
            r"\b(?:manager|specialist|lead)\w*[\s/-]+(?:paid[\s/-]+social|social[\s/-]+media|content)\b",
            r"\b(?:seo|sea|sem|geo)[\s/&-]+(?:manager|specialist|lead)\w*\b",
            r"\btravel[\s/-]+deals?[\s/-]+(?:expert|specialist|editor)\w*\b",
            r"\bsocial[\s/-]+affiliate[\s/-]+specialist\w*\b",
        ),
    ),
    RelevanceRule(
        "general_consulting",
        "general_consulting",
        (CONSULTING_TITLE_PATTERN,),
    ),
)


def _all_matches(title: str, rules: Iterable[RelevanceRule]) -> list[tuple[RelevanceRule, str]]:
    matches: list[tuple[RelevanceRule, str]] = []
    for rule in rules:
        for pattern in rule.patterns:
            matched = re.search(pattern, title)
            if matched:
                matches.append((rule, matched.group(0)))
                break
    return matches


def classify_relevance(
    title: str,
    *,
    company: str = "",
    unwanted_keywords: Optional[list[str]] = None,
    manual_action: Optional[str] = None,
) -> RelevanceResult:
    """Classify one title using explicit precedence and inspectable reasons."""
    if manual_action == "keep":
        return RelevanceResult("manual_keep", None, "manual", "Kept by manual override")
    if manual_action == "archive":
        return RelevanceResult("manual_archive", None, "manual", "Archived by manual override")

    if normalize_text(company) in PERSONAL_EXCLUDED_COMPANIES:
        return RelevanceResult(
            "excluded", None, company, f"Excluded by reviewed employer preference: {company}",
            matches=("personal_academic_employer",), negative_matches=("personal_academic_employer",),
        )

    normalized = normalize_text(title)
    keyword = match_keyword_filter(title, unwanted_keywords or [])
    if keyword:
        return RelevanceResult("excluded", None, keyword, f"Excluded by title keyword: {keyword}")

    study = match_study_title_pattern(title)
    if study:
        return RelevanceResult(
            "excluded", None, study, "Excluded as a student or study-track role",
            matches=("study_role",), negative_matches=("study_role",),
        )

    academic = match_academic_title_pattern(title, company)
    if academic:
        return RelevanceResult(
            "excluded", None, academic, "Excluded as an academic role",
            matches=("academic_role",), negative_matches=("academic_role",),
        )

    target_matches = _all_matches(normalized, TARGET_RULES)
    adjacent_matches = _all_matches(normalized, ADJACENT_TECHNICAL_RULES)
    scope_matches = _all_matches(normalized, SCOPE_EXCLUSION_RULES)
    unrelated_matches = _all_matches(normalized, UNRELATED_RULES)
    if normalize_text(company) in RETAIL_ADVISOR_EMPLOYERS:
        unrelated_matches.extend(_all_matches(normalized, (RETAIL_ADVISOR_RULE,)))
    personal_matches = _all_matches(normalized, PERSONAL_EXCLUSION_RULES)
    if personal_matches:
        primary_rule, matched_value = personal_matches[0]
        positive_rules = tuple(rule.name for rule, _ in target_matches)
        negative_rules = tuple(rule.name for rule, _ in personal_matches)
        return RelevanceResult(
            "excluded", None, matched_value,
            f"Excluded by reviewed personal preference: {primary_rule.family} ({matched_value})",
            matches=(*positive_rules, *negative_rules),
            positive_matches=positive_rules,
            negative_matches=negative_rules,
        )

    blocking_scope_matches = [
        match for match in scope_matches if match[0].name != "engineering_role"
    ]
    if blocking_scope_matches:
        primary_rule, matched_value = blocking_scope_matches[0]
        rule_names = tuple(rule.name for rule, _ in blocking_scope_matches)
        return RelevanceResult(
            "excluded",
            None,
            matched_value,
            f"Excluded as {primary_rule.family.replace('_', ' ')}: {matched_value}",
            matches=rule_names,
            negative_matches=rule_names,
        )

    consulting_match = re.search(CONSULTING_TITLE_PATTERN, normalized)
    implementation_match = re.search(CONSULTING_IMPLEMENTATION_PATTERN, normalized)
    if consulting_match and implementation_match and not adjacent_matches:
        matched_value = implementation_match.group(0)
        return RelevanceResult(
            "excluded",
            None,
            matched_value,
            f"Excluded as implementation-heavy consulting: {matched_value}",
            matches=("consulting_implementation",),
            negative_matches=("consulting_implementation",),
        )

    engineering_scope_matches = [
        match for match in scope_matches if match[0].name == "engineering_role"
    ]
    if engineering_scope_matches and not adjacent_matches:
        primary_rule, matched_value = engineering_scope_matches[0]
        rule_names = tuple(rule.name for rule, _ in engineering_scope_matches)
        return RelevanceResult(
            "excluded",
            None,
            matched_value,
            f"Excluded as {primary_rule.family.replace('_', ' ')}: {matched_value}",
            matches=rule_names,
            negative_matches=rule_names,
        )

    positive_rule_names = tuple(rule.name for rule, _ in target_matches)
    negative_rule_names = tuple(rule.name for rule, _ in unrelated_matches)
    if target_matches:
        primary_rule, matched_value = target_matches[0]
        return RelevanceResult(
            "target",
            primary_rule.family,
            matched_value,
            f"Kept as {primary_rule.family.replace('_', ' ')}: {matched_value}",
            matches=(*positive_rule_names, *negative_rule_names),
            positive_matches=positive_rule_names,
            negative_matches=negative_rule_names,
        )

    if adjacent_matches:
        primary_rule, matched_value = adjacent_matches[0]
        rule_names = tuple(rule.name for rule, _ in adjacent_matches)
        return RelevanceResult(
            "unmatched",
            None,
            matched_value,
            f"Retained as adjacent {primary_rule.family.replace('_', ' ')}: {matched_value}",
            matches=rule_names,
        )

    if unrelated_matches:
        primary_rule, matched_value = unrelated_matches[0]
        return RelevanceResult(
            "unrelated",
            None,
            primary_rule.family,
            f"Unrelated role family: {primary_rule.family.replace('_', ' ')}",
            matches=negative_rule_names,
            negative_matches=negative_rule_names,
        )

    return RelevanceResult(
        "unmatched",
        None,
        None,
        "No approved target-role signal matched the title",
    )


def decision_for_result(
    job_id: str,
    result: RelevanceResult,
    *,
    mode: str,
    include_unmatched: bool = False,
    details: Optional[dict[str, object]] = None,
) -> Optional[dict[str, object]]:
    """Translate a classification into an append-only audit decision."""
    if result.outcome in {"manual_keep", "manual_archive"}:
        return None

    should_archive = result.outcome in {"excluded", "unrelated"} or (
        result.outcome == "unmatched" and include_unmatched
    )
    action = "archive" if mode == "enforce" and should_archive else "keep" if result.outcome == "target" else "shadow"
    filter_name = {
        "excluded": "scope_exclusion",
        "target": "target_role",
        "unrelated": "unrelated_role",
        "unmatched": "no_target_role",
    }[result.outcome]
    decision_details = dict(details or {})
    decision_details.update(
        {
            "outcome": result.outcome,
            "role_family": result.role_family,
            "ruleset_version": result.ruleset_version,
            "matches": list(result.matches),
            "positive_matches": list(result.positive_matches),
            "negative_matches": list(result.negative_matches),
        }
    )
    return {
        "job_id": job_id,
        "decision_source": "rule",
        "decision_action": action,
        "filter_name": filter_name,
        "matched_value": result.matched_value,
        "reason": result.reason,
        "details": decision_details,
    }
