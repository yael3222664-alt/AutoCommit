"""Closed reference data used by domain validation."""

# Canonical names of the approved sending entities (the closed authority list).
SENDING_ENTITIES: tuple[str, ...] = (
    # HMOs
    "כללית",
    "מכבי",
    "מאוחדת",
    "לאומית",
    # Hospitals and medical institutions
    "שיבא (תל השומר)",
    "שמיר (אסף הרופא)",
    'רמב"ם',
    "איכילוב (תל אביב)",
    "הדסה (עין כרם / הר הצופים)",
    "שערי צדק",
    "בלינסון (רבין)",
    "מאיר",
    "סורוקה",
    "כרמל",
    "בני ציון (רוטשילד)",
    "שמואל הרופא",
    "פוריה (פדה)",
    "המרכז הלאומי לרפואה משפטית (אבו כביר)",
    'מרפאות צה"ל (חר"פ / מדיתיק)',
    "מעבדת רוזנבלט",
    "הרשות הפלסטינית",
)

# Additional spellings accepted for a canonical name. The canonical name itself is
# always accepted and is not repeated here. An alias may belong to only one entity.
SENDING_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "שיבא (תל השומר)": ("שיבא", "תל השומר"),
    "שמיר (אסף הרופא)": ("שמיר", "אסף הרופא"),
    "איכילוב (תל אביב)": ("איכילוב",),
    "הדסה (עין כרם / הר הצופים)": ("הדסה", "עין כרם", "הר הצופים"),
    "בלינסון (רבין)": ("בלינסון", "רבין"),
    "בני ציון (רוטשילד)": ("בני ציון", "רוטשילד"),
    "פוריה (פדה)": ("פוריה", "פדה"),
    "המרכז הלאומי לרפואה משפטית (אבו כביר)": ("המרכז הלאומי לרפואה משפטית", "אבו כביר"),
    'מרפאות צה"ל (חר"פ / מדיתיק)': ('מרפאות צה"ל', 'חר"פ', "מדיתיק"),
    "מעבדת רוזנבלט": ("רוזנבלט",),
}

# Fuzzy sender matching (difflib.SequenceMatcher ratio, 0.0-1.0) is used only when no
# exact or whole-phrase match exists. A fuzzy match is accepted (with medium confidence)
# only if the best entity scores at least SENDER_MATCH_THRESHOLD AND beats the
# runner-up entity by at least SENDER_AMBIGUITY_MARGIN. Otherwise the sender is
# treated as unknown/ambiguous and sent to manual review with its raw text preserved.
SENDER_MATCH_THRESHOLD: float = 0.8
SENDER_AMBIGUITY_MARGIN: float = 0.1
