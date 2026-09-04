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


RULESET_VERSION = "2026-09-04.6"
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
            r"\b(?:bio)?statistiker\w*\b",
            r"\b(?:bio)?statistician\w*\b",
        ),
    ),
    RelevanceRule(
        "analytics_bi",
        "analytics_bi",
        (
            r"\bdata[\s/-]+analyst\w*\b",
            r"\bdata[\s/-]+analytics\b",
            r"\banalytics\b",
            r"\banalytics[\s/-]+engineer\w*\b",
            r"\bbusiness[\s/-]+intelligence\b",
            r"\bpower[\s/-]+bi\b",
            r"\bbi[\s/-]+(?:analyst|developer|engineer|consultant)\w*\b",
            r"\bconsultant[\s/-]+bi\b",
            r"\breporting[\s/-]+analyst\w*\b",
            r"\binsights?[\s/-]+analyst\w*\b",
            r"\bdatenanalyst\w*\b",
            r"\bdatenanalyse\w*\b",
            r"\bdatenanalytik\w*\b",
        ),
    ),
)


SCOPE_EXCLUSION_RULES: tuple[RelevanceRule, ...] = (
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
            r"\b(?:ausbilder|weiterbildung|schulung)\w*\b",
            r"\blearning[\s/&-]+development\b",
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
            r"\b(?:kaufmannisch\w*[\s/-]+)?assistenz\b",
            r"\bassistenz[\s/-]+der[\s/-]+geschaftsleitung\b",
            r"\bexecutive[\s/-]+assistant\b",
            r"\boffice[\s/-]+assistant\b",
            r"\bsekretari(?:at|atskraft)\b",
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
        ),
    ),
    RelevanceRule(
        "operational_logistics",
        "operational_logistics",
        (
            r"\blogistiker\w*\b",
            r"\blogistikmitarbeiter\w*\b",
            r"\bmitarbeiter\w*[\s/-]+(?:in[\s/-]+der[\s/-]+)?logistik\b",
            r"\bversandmitarbeiter\w*\b",
            r"\bkommissionierer\w*\b",
            r"\bstockist\b",
            r"\b(?:shipping|logistics?)[\s/-]+(?:clerk|coordinator|operative|worker|associate)\w*\b",
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
            r"\b(?:it|technical|customer|product|business)[\s/-]+support\b",
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
            r"\b(?:database|system|network)[\s/-]+administrator\w*\b",
            r"\bdatenbankadministrator\w*\b",
            r"\b(?:it|sap|erp|m365)[\s/-]+consultant\w*\b",
            r"\btechnical[\s/-]+consultant\w*[\s/-]+(?:it|sap|erp|m365)\b",
            r"\bconsultant\w*[\s/-]+(?:it|sap|erp|m365)\b",
            r"\bit[\s/-]+(?:system|onsite|operations?)[\s/-]+specialist\w*\b",
            r"\bsystembetreuer\w*\b",
            r"\b(?:cybersecurity|security)[\s/-]+analyst\w*\b",
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
        ),
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
        ),
    ),
    RelevanceRule(
        "sales_acquisition",
        "sales_acquisition",
        (
            r"\bsales[\s/-]+representative\b",
            r"\baccount[\s/-]+executive\b",
            r"\bbusiness[\s/-]+development[\s/-]+representative\b",
            r"\bfield[\s/-]+sales\b",
            r"\baußendienstmitarbeiter\w*\b",
            r"\b(?:sales|account)[\s/-]+manager\w*\b",
            r"\bvertriebs(?:mitarbeiter|spezialist|manager|innendienst)\w*\b",
            r"\bverkaufsinnendienst\w*\b",
            r"\bcustomer[\s/-]+success[\s/-]+(?:manager|specialist)\w*\b",
        ),
    ),
    RelevanceRule(
        "marketing_content",
        "marketing_content",
        (
            r"\b\w*marketing(?:manager|specialist|lead)\w*\b",
            r"\b\w*marketing\w*[\s/-]+(?:manager|specialist|lead)\w*\b",
            r"\b(?:social[\s/-]+media|content)\b.*\b(?:creator|manager|specialist)\w*\b",
            r"\b(?:manager|specialist|lead)\w*[\s/-]+(?:paid[\s/-]+social|social[\s/-]+media|content)\b",
            r"\b(?:seo|sea|sem|geo)[\s/&-]+(?:manager|specialist|lead)\w*\b",
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

    normalized = normalize_text(title)
    keyword = match_keyword_filter(title, unwanted_keywords or [])
    if keyword:
        return RelevanceResult("excluded", None, keyword, f"Excluded by title keyword: {keyword}")

    study = match_study_title_pattern(title)
    if study:
        return RelevanceResult("excluded", None, study, "Excluded as a student or study-track role")

    academic = match_academic_title_pattern(title, company)
    if academic:
        return RelevanceResult("excluded", None, academic, "Excluded as an academic role")

    scope_matches = _all_matches(normalized, SCOPE_EXCLUSION_RULES)
    if scope_matches:
        primary_rule, matched_value = scope_matches[0]
        rule_names = tuple(rule.name for rule, _ in scope_matches)
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
    if consulting_match and implementation_match:
        matched_value = implementation_match.group(0)
        return RelevanceResult(
            "excluded",
            None,
            matched_value,
            f"Excluded as implementation-heavy consulting: {matched_value}",
            matches=("consulting_implementation",),
            negative_matches=("consulting_implementation",),
        )

    target_matches = _all_matches(normalized, TARGET_RULES)
    unrelated_matches = _all_matches(normalized, UNRELATED_RULES)
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

    if result.outcome == "excluded":
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
            "decision_action": "archive" if mode == "enforce" else "shadow",
            "filter_name": "scope_exclusion",
            "matched_value": result.matched_value,
            "reason": result.reason,
            "details": decision_details,
        }

    should_archive = result.outcome == "unrelated" or (
        result.outcome == "unmatched" and include_unmatched
    )
    action = "archive" if mode == "enforce" and should_archive else "keep" if result.outcome == "target" else "shadow"
    filter_name = {
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
