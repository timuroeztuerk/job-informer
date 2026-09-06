"""Resolve typographic citation differences to exact saved source spans."""

import re
import unicodedata


EVIDENCE_VERSION = "1"
_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
})
_SPACING_PUNCTUATION = set(".,:;!?()[]{}-/\\'\"")
_QUALIFIERS = {"required", "preferred", "optional", "mandatory", "minimum", "maximum",
               "notwendig", "erforderlich", "zwingend", "mindestens", "höchstens", "wünschenswert"}


def normalized_span(text):
    """Normalize typography while retaining offsets into the untouched source."""
    chars, offsets = [], []
    # Normalize combining sequences together so decomposed accents retain their span.
    for match in re.finditer(r"[^\u0300-\u036f][\u0300-\u036f]*|[\u0300-\u036f]+", text):
        value = unicodedata.normalize("NFKC", match.group()).casefold().translate(_PUNCTUATION)
        for char in value:
            if char in "\u00ad\u200b\ufeff":
                continue
            char = " " if char.isspace() else char
            if char == " " and chars and chars[-1] == " ":
                offsets[-1] = (offsets[-1][0], match.end())
            else:
                chars.append(char)
                offsets.append((match.start(), match.end()))
    keep = [i for i, char in enumerate(chars) if char != " " or (
        i > 0 and i + 1 < len(chars)
        and chars[i-1] not in _SPACING_PUNCTUATION
        and chars[i+1] not in _SPACING_PUNCTUATION)]
    return "".join(chars[i] for i in keep), [offsets[i] for i in keep]


def _variants(quote):
    # A decorative bullet or terminal full stop does not establish a job claim.
    plain = re.sub(r"^\s*(?:[•*]\s*|[-–—]\s+(?!\d)|\.{3}\s*|…\s*)", "", quote).strip()
    return list(dict.fromkeys([quote, plain, plain.rstrip(".").rstrip()]))


def _one_typo(left, right):
    if not left.isalpha() or not right.isalpha() or min(len(left), len(right)) < 7:
        return False
    if left in _QUALIFIERS or right in _QUALIFIERS:
        return False
    if len(left) == len(right):
        differences = [i for i, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(differences) == 1 or (len(differences) == 2
            and differences[1] == differences[0] + 1
            and left[differences[0]:differences[1]+1] == right[differences[0]:differences[1]+1][::-1])
    short, long = sorted((left, right), key=len)
    return len(long) == len(short) + 1 and any(long[:i] + long[i+1:] == short for i in range(len(long)))


def _typo_matches(quote, source):
    words = list(re.finditer(r"\w+", quote))
    # One misspelled long word, anchored by at least four unchanged words.
    # No gap matching, paraphrases, changed numbers, or changed qualifications.
    if len(words) < 5:
        return []
    matches = set()
    for word in words:
        if len(word.group()) < 7 or word.group() in _QUALIFIERS:
            continue
        pattern = re.escape(quote[:word.start()]) + r"(\w+)" + re.escape(quote[word.end():])
        for match in re.finditer(pattern, source):
            if _one_typo(word.group(), match.group(1)):
                matches.add((match.start(), match.end()))
    return sorted(matches)


def resolve_evidence(source_ref, quote, sources):
    """Tolerate typography and bounded spelling errors; preserve original spans."""
    source = sources.get(source_ref)
    if source is not None and (start := source.find(quote)) >= 0:
        return {"source_ref": source_ref, "quote": quote, "start": start, "end": start + len(quote)}

    variants = [normalized_span(value)[0] for value in _variants(quote) if value.strip()]

    def match(ref, *, typos=False):
        text = sources[ref]
        normalized, offsets = normalized_span(text)
        for value in variants:
            if not value:
                continue
            if typos:
                matches = _typo_matches(value, normalized)
                if len(matches) != 1:
                    continue
                start, end = matches[0]
            else:
                start = normalized.find(value)
                end = start + len(value)
            if start >= 0:
                end = offsets[end - 1][1]
                start = offsets[start][0]
                return {"source_ref": ref, "quote": text[start:end], "start": start, "end": end}
        return None

    resolved = match(source_ref) if source is not None else None
    if resolved is None:
        # Correct a misplaced paragraph reference only within the same source kind
        # and only when exactly one other source contains the quotation.
        family = source_ref.split(".")[0]
        candidates = [value for ref in sources if ref != source_ref and ref.split(".")[0] == family
                      if (value := match(ref)) is not None]
        if len(candidates) == 1:
            resolved = candidates[0]
    method = "typography" if resolved and resolved["source_ref"] == source_ref else "source_reference"
    if resolved is None and source is not None:
        resolved = match(source_ref, typos=True)
        method = "spelling"
    if resolved is None:
        raise ValueError(f"Evidence does not match source {source_ref}.")
    resolved["repair"] = {"version": EVIDENCE_VERSION, "original_source_ref": source_ref,
                          "original_quote": quote,
                          "method": method}
    return resolved
