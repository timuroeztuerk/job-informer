"""Canonical structured output and deterministic evidence validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .evidence import resolve_evidence


class Structure(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Structure):
    source_ref: str
    quote: str = Field(min_length=1)


Strength = Literal["required", "preferred", "mentioned", "not_required", "unclear"]
State = Literal["stated", "not_stated", "conflicting", "not_assessable"]


class QualifiedClaim(Structure):
    strength: Strength
    condition: str | None = Field(description="Explicit applicability condition, e.g. for India; null if unconditional.")
    alternative_group: str | None = Field(description="Shared ID for an explicit OR choice; null otherwise.")
    alternative_option: str | None = Field(description="Within an OR group, items in the same option are required together. Null without a group.")
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def paired_alternative(self):
        if (self.alternative_group is None) != (self.alternative_option is None):
            raise ValueError("An alternative needs both a group and an option.")
        return self


class Requirement(QualifiedClaim):
    term: str = Field(min_length=1, description="Concise English name of ONE concept; retain product names. No proficiency adjectives or lists.")
    category: Literal["skill", "tool", "programming_language", "certification", "clearance", "other"]
    proficiency: str | None = Field(description="Explicit proficiency wording, kept out of the term.")


class EducationRequirement(QualifiedClaim):
    qualification: str = Field(min_length=1, description="Stated qualification, in the source language; one alternative per item.")
    level: Literal["vocational", "bachelor", "master", "doctorate", "degree_unspecified", "other"]
    fields_of_study: list[str] = Field(description="Explicitly acceptable disciplines; [] when unspecified. Do not infer a degree level from Studium.")


class LanguageRequirement(QualifiedClaim):
    language: str = Field(description="English language name, e.g. German or Japanese; proficiency and evidence retain source wording.")
    proficiency: str | None
    cefr: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None


class DescriptionLanguage(Structure):
    code: str = Field(pattern=r"^[a-z]{2,3}$", description="ISO 639-1 code, or ISO 639-3 when no two-letter code exists.")
    evidence: list[Evidence] = Field(min_length=1)


class ExperienceYears(Structure):
    kind: Literal["minimum", "maximum", "target_range", "ambiguous_minimum", "exact"]
    lower: float | None = Field(ge=0)
    upper: float | None = Field(ge=0)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.kind == "minimum" and (self.lower is None or self.upper is not None):
            raise ValueError("A minimum needs a lower bound and no upper bound.")
        if self.kind == "maximum" and (self.upper is None or self.lower is not None):
            raise ValueError("A maximum needs an upper bound and no lower bound.")
        if self.kind in {"target_range", "ambiguous_minimum", "exact"}:
            if self.lower is None or self.upper is None or self.lower > self.upper:
                raise ValueError("A range needs ordered lower and upper bounds.")
            if self.kind == "exact" and self.lower != self.upper:
                raise ValueError("An exact duration needs equal bounds.")
        return self


class ExperienceRequirement(QualifiedClaim):
    wording: str
    years: ExperienceYears | None = Field(description="Only explicit numbers. 'At least 1–2' is ambiguous_minimum, 'ideally 2–4' target_range, '5+' minimum. Several years => null.")
    scope: str | None


class WorkArrangement(Structure):
    mode: Literal["remote", "on_site", "hybrid", "remote_possible", "unspecified"] = Field(description="Hybrid requires an explicit hybrid label or office/remote split. Mobile working alone is remote_possible.")
    wording: str
    office_attendance: str | None
    geographic_restrictions: str | None
    condition: str | None
    evidence: list[Evidence] = Field(min_length=1)


class Claim(Structure):
    value: str
    evidence: list[Evidence] = Field(min_length=1)


class Conflict(Structure):
    field: Literal["requirements", "education", "languages", "experience", "work_arrangement", "responsibilities", "seniority", "employment_type"]
    explanation: str = Field(description="Why two explicit claims cannot both hold for the same scope. Broad seniority labels, preferences and different locations are not contradictions by themselves.")
    evidence: list[Evidence] = Field(min_length=2)


class FieldStates(Structure):
    requirements: State
    education: State
    languages: State
    experience: State
    work_arrangement: State
    responsibilities: State
    seniority: State
    employment_type: State


class JobExtraction(Structure):
    description_languages: list[DescriptionLanguage] = Field(description="Actual description languages, dominant first. Ignore isolated borrowed terms and metadata labels.")
    states: FieldStates
    requirements: list[Requirement]
    education: list[EducationRequirement]
    languages: list[LanguageRequirement]
    experience: list[ExperienceRequirement]
    work_arrangement: list[WorkArrangement]
    responsibilities: list[Claim]
    seniority: list[Claim]
    employment_type: list[Claim]
    conflicts: list[Conflict]

    @model_validator(mode="after")
    def coherent_states(self):
        if len({item.code for item in self.description_languages}) != len(self.description_languages):
            raise ValueError("Duplicate description language.")
        if any(not evidence.source_ref.startswith("description.") for item in self.description_languages for evidence in item.evidence):
            raise ValueError("Description languages require evidence from description text.")
        for name, state in self.states.model_dump().items():
            values = getattr(self, name)
            if state == "not_stated" and values:
                raise ValueError(f"{name} has values but is marked not stated.")
            if state in {"stated", "conflicting"} and not values:
                raise ValueError(f"{name} is marked stated without claims.")
            if (state == "conflicting") != any(c.field == name for c in self.conflicts):
                raise ValueError(f"{name} conflict state is inconsistent.")
        return self


ALIASES = {"powerbi": "Power BI", "power bi": "Power BI", "python": "Python", "sql": "SQL",
           "r": "R", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn",
           "t-sql": "T-SQL", "transact-sql": "T-SQL", "transact-sql (t-sql)": "T-SQL",
           "excel": "Excel", "microsoft excel": "Excel", "ms excel": "Excel",
           "aws": "AWS", "amazon web services": "AWS", "azure": "Azure", "microsoft azure": "Azure",
           "gcp": "GCP", "google cloud platform": "GCP", "java": "Java", "scala": "Scala"}
LANGUAGES = {"Python", "SQL", "T-SQL", "R", "Java", "Scala"}
TOOLS = {"Power BI", "Excel", "AWS", "Azure", "GCP", "PyTorch", "TensorFlow", "scikit-learn"}


def validate_evidence(parsed: JobExtraction, sources: dict[str, str]) -> dict:
    """Resolve quotations to original spans, accepting harmless typography."""
    payload = parsed.model_dump()

    def visit(value):
        if isinstance(value, dict):
            if "source_ref" in value and "quote" in value:
                value.update(resolve_evidence(value["source_ref"], value["quote"], sources))
            for child in list(value.values()):
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    for item in payload["requirements"]:
        item["canonical_term"] = ALIASES.get(item["term"].strip().casefold(), item["term"].strip())
        if item["canonical_term"] in LANGUAGES:
            item["category"] = "programming_language"
        elif item["canonical_term"] in TOOLS:
            item["category"] = "tool"
    payload["description_language"] = "/".join(item["code"] for item in payload["description_languages"]) or "und"
    return payload
