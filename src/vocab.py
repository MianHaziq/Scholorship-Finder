"""Controlled vocabularies.

Keeping these fixed is what makes Phase-2 filters reliable: the AI is told to map
each scholarship onto these exact tags rather than free-text, so a query for
"computer_science" catches every CS scholarship regardless of how the source worded it.
"""

# Broad field families. The AI maps each scholarship to one or more of these.
FIELDS = [
    "computer_science",     # CS, software, AI/ML, data science, IT, cybersecurity
    "engineering",          # mechanical, electrical, civil, chemical, aerospace...
    "natural_sciences",     # physics, chemistry, biology, maths, environmental
    "medicine_health",      # medicine, nursing, public health, pharmacy
    "business_economics",   # business, management, finance, economics, MBA
    "social_sciences",      # sociology, politics, psychology, international relations
    "law",
    "arts_humanities",      # literature, history, philosophy, languages, design, music
    "education",
    "agriculture",
    "any_field",            # open to all disciplines
]

DEGREE_LEVELS = ["bachelors", "masters", "phd"]

FUNDING_TYPES = ["fully_funded", "partial", "unknown"]

# Normalized country -> region. Region is derived from country in code.
COUNTRY_REGION = {
    # Germany + Europe
    "Germany": "Europe",
    "Netherlands": "Europe",
    "Sweden": "Europe",
    "France": "Europe",
    "Switzerland": "Europe",
    "Austria": "Europe",
    "Belgium": "Europe",
    "Italy": "Europe",
    "Spain": "Europe",
    "Denmark": "Europe",
    "Norway": "Europe",
    "Finland": "Europe",
    "Ireland": "Europe",
    "Poland": "Europe",
    "Portugal": "Europe",
    "Europe": "Europe",          # multi-country EU programmes (e.g. Erasmus Mundus)
    # UK
    "UK": "UK",
    "United Kingdom": "UK",
    # Canada + Australia
    "Canada": "North America",
    "Australia": "Oceania",
    "New Zealand": "Oceania",
}


def region_for(country: str | None) -> str | None:
    if not country:
        return None
    return COUNTRY_REGION.get(country.strip())


# Different pages name the same destination differently ("UK" / "United Kingdom",
# "Holland" / "The Netherlands"). Left alone they split one destination into two
# entries in every filter, so the AI's answer is mapped onto one canonical name.
COUNTRY_ALIASES = {
    "united kingdom": "UK",
    "great britain": "UK",
    "britain": "UK",
    "england": "UK",
    "scotland": "UK",
    "wales": "UK",
    "northern ireland": "UK",
    "u.k.": "UK",
    "the netherlands": "Netherlands",
    "holland": "Netherlands",
    "deutschland": "Germany",
    "federal republic of germany": "Germany",
    "the united states": "USA",
    "united states": "USA",
    "united states of america": "USA",
    "u.s.a.": "USA",
    "us": "USA",
    "european union": "Europe",
    "eu": "Europe",
    "republic of ireland": "Ireland",
    "czechia": "Czech Republic",
}


def canonical_country(name: str | None) -> str | None:
    """Map a country name onto the single spelling used everywhere else."""
    if not name:
        return None
    cleaned = " ".join(name.split()).strip(" .,")
    if not cleaned:
        return None
    return COUNTRY_ALIASES.get(cleaned.lower(), cleaned)
