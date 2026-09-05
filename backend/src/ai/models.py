"""Canonical structured output and deterministic evidence validation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Structure(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Structure):
    source_ref: str
    quote: str = Field(min_length=1)


Strength = Literal["required", "preferred", "mentioned", "not_required", "unclear"]
State = Literal["stated", "not_stated", "conflicting", "not_assessable"]


class Requirement(Structure):
    term: str = Field(min_length=1)
    category: Literal["skill", "tool", "programming_language"]
    strength: Strength
    alternative_group: str | None = Field(description="Same non-null ID for alternatives, e.g. Python OR R.")
    evidence: list[Evidence] = Field(min_length=1)


class LanguageRequirement(Structure):
    language: str
    proficiency: str | None
    cefr: Literal["A1", "A2", "B1", "B2", "C1", "C2"] | None
    strength: Strength
    alternative_group: str | None
    evidence: list[Evidence] = Field(min_length=1)


class DescriptionLanguage(Structure):
    code: str = Field(pattern=r"^[a-z]{2,3}$", description="ISO 639-1 code, or ISO 639-3 when no two-letter code exists.")
    evidence: list[Evidence] = Field(min_length=1)


class ExperienceRequirement(Structure):
    wording: str
    minimum_years: float | None = Field(ge=0)
    maximum_years: float | None = Field(ge=0)
    scope: str | None
    strength: Strength
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_range(self):
        if self.minimum_years is not None and self.maximum_years is not None and self.minimum_years > self.maximum_years:
            raise ValueError("Minimum experience exceeds maximum.")
        return self


class WorkArrangement(Structure):
    mode: Literal["remote", "on_site", "hybrid", "unspecified"]
    wording: str
    office_attendance: str | None
    geographic_restrictions: str | None
    evidence: list[Evidence] = Field(min_length=1)


class Claim(Structure):
    value: str
    evidence: list[Evidence] = Field(min_length=1)


class Conflict(Structure):
    field: Literal["requirements", "languages", "experience", "work_arrangement", "responsibilities", "seniority", "employment_type"]
    explanation: str
    evidence: list[Evidence] = Field(min_length=2)


class FieldStates(Structure):
    requirements: State
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
           "r": "R", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn"}


def validate_evidence(parsed: JobExtraction, sources: dict[str, str]) -> dict:
    """Resolve exact quotations; never repair or invent evidence with another model."""
    payload = parsed.model_dump()

    def visit(value):
        if isinstance(value, dict):
            if "source_ref" in value and "quote" in value:
                text = sources.get(value["source_ref"])
                start = text.find(value["quote"]) if text is not None else -1
                if start < 0:
                    raise ValueError(f"Evidence does not match source {value['source_ref']}.")
                value.update(start=start, end=start + len(value["quote"]))
            for child in list(value.values()):
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    for item in payload["requirements"]:
        item["canonical_term"] = ALIASES.get(item["term"].strip().casefold(), item["term"].strip())
    payload["description_language"] = "/".join(item["code"] for item in payload["description_languages"]) or "und"
    return payload
