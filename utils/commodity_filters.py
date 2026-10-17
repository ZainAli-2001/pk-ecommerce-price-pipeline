"""
Category-specific exclusion rules for filtering products that match a
search keyword but are not the target forecasting commodity (e.g.
hair oil, instant tea, milk powder). Used after title normalization
to improve dataset quality before analysis.
"""

import re

# Regex-based exclusions, for patterns plain substring matching can't express
# safely (word boundaries, lookaheads).
REGEX_EXCLUSION_PATTERNS = {
    "tea": [
        re.compile(r"\bT\s*/\s*B\b", re.IGNORECASE),  # e.g. "TAPAL DANEDAR ROUND TEA T/B 200 GM"
    ],
    "dairy": [
        # Exclude cream products / skincare, but keep "Full Cream Milk"
        # (cream followed by up to 2 words and then "milk").
        re.compile(r"\bcream\b(?!\s+(?:[\w'’]+\s+){0,2}milk)", re.IGNORECASE),
        # Pet treats ("Creamy Treat for Cats"). Word boundary so "UHT Treated"
        # milk is not excluded.
        re.compile(r"\btreats?\b", re.IGNORECASE),
    ],
    "flour": [
        # Grocery bundles ("package beans, sugar, atta, ..."). Word boundary so
        # "packaged" / "packaging" in normal flour listings are not excluded.
        re.compile(r"\bpackage\b", re.IGNORECASE),
        # Wheat bran. Word boundary so "brand" (e.g. "Sunridage brand atta") is
        # not excluded.
        re.compile(r"\bbran\b", re.IGNORECASE),
    ],
}

EXCLUSION_KEYWORDS = {
    "oil": [
        "hair oil",
        "massage oil",
        "essential oil",
        "body oil",
        "baby oil",
        "argan",
        "castor",
        "jojoba",
        "pumpkin seed",
        "rosemary",
        "anti lice",
        "fofo",
        "reetha",
        "shikakai",
        "amla",
        "onion oil",
        "nagarmotha",
        "spray",
        "mites",
        "alkanet",
        "ratanjot",
        "sabja",
        "tukhmalanga",
        "kalonji",
        "malkangni",
        "egg oil",
        "rogan",
        "roghan",
        "germinal",
        "hair growth",
        "makeup",
        "purge",
        "removal",
        "serum",
        "mct",
        "oil press machine",
        "oil extract machine",
        # herbal-shop / cosmetic oils
        "akseer",
        "zulf",
        "neem",
        "rosehip",
        "aloe vera",
        "clove oil",
        "ajwain",
        "fish oil",
        "glycerin",
        "ylang",
        "orange oil",
        "lemon oil",
        "carrier oil",
        "black seed",
        "blackseed",
        "kilwanji",
        "ushn",
        "tukhum",  # e.g. "12 Tukhum seeds Oil"
        "argon",  # argan oil misspelling
        "buckthorn",
        "avocado",  # edible but not a commodity cooking oil
        "pansar",  # also matches "pansari"; ~20 edible-labelled oils get caught, drop if unwanted
    ],
    "tea": [
        "tea bag",
        "tea bags",
        "instant tea",
        "3 in 1",
        "3in1",
        "2 in 1",
        "2in1",
        "premix",
        "pre mix",
        "latte",
        "cappuccino",
        "coffee",
        "joshanda",
        "kharal",
        "dasta",
        "imam dasta",
        "danda",
        "mortar",
        "pestle",
        "grinder",
        "toy",
        "anime",
        "keychain",
        "plush",
        "hoodie",
        "pen",
        "pencil",
        "paperback",
        "poster",
        "notebook",
        "stationery",
        "gift set",
        "doremon",
        "tokyo",
        "bottle",
        # manga / books / merch
        "vol.",
        "manga",
        "author",
        "novel",
        "book",
        "dragon ball",
        "demon slayer",
        "naruto",
        "pokemon",
        "puzzle",
        "playdough",
        "board game",
        "card",
        "sticker",
        "tattoo",
        "tarot",
        # prayer beads
        "tasbeeh",
        "tasbih",
        "tasibh",
        "beads",
        # apparel / electronics / misc
        "shirt",
        "tablet",
        "tumbler",
        "perfume",
        "travel mug",
        "denali",  # bottled water
    ],
    "dairy": [
        "milk powder",
        "powder milk",
        "flavoured milk",
        "flavored milk",
        "chocolate milk",
        "strawberry milk",
        "banana milk",
        "kitten milk",
        "cat milk",
        "coffee creamer",
        "tea whitener",
        "cadbury dairy milk",
        "dairy milk bar",
        "infant formula",
        "follow-up formula",
        "maternal formula",
        "toddler milk",
        "weight gain powder",
        "lactogen",
        "pediasure",
        "mamalac",
        "rusk",
        "baby cereal",
        "ovaltine",
        "malt drink",
        "soya milk",
        "soy milk",
        "soya drink",
        "cleansing milk",
        "feeding bottle",
        "porridge",
        "horlicks",
        "nesquik",
        "nesquick",
        "chocomalt",
        "pedialac",
        "dog food",
        "pedigree",
        "milk replacer",
        "puppies",
        "semolina",
        "couscous",
        "biscuit",
        "pakola juice",
        "pakola yum milk",
        "lipton",
        "milka",
        "complan",
        "milo",
        "cocoa malt",
        "lactoogen",
        "lactometer",
        "oleanolic acid",
        "lactogrow",
        "lactol",
        "azolac",
        "olivola",
        "oat milk",
        "coconut milk drink",
        "flavour milk",
        "olives",
        "face wash",
        # "cream" removed -> handled by regex above (keeps Full Cream Milk)
        "lotion",
        "olive",
        "cat food",
        "dog",
        "pet",
        "ors",
        "calf",
        # baby / pet / cattle items
        "feeder",
        "teat",
        "nipple",
        "kitten",
        "cerelac",
        "cattle",
        "booster",
        "collector",
        "drinker",
        # non-milk products
        "sharbat",
        "peanut butter",
        "cocoa butter",
        "moringa",
        "flour",
        "evaporated",  # canned evaporated milk, not fresh/UHT
        "condensed",  # canned condensed milk, same logic as evaporated
        "creamer",  # milk tea powders / whiteners ("Olpers Creamer")
        "nido",  # powdered milk brand sold without "milk powder" in the title
        # non-dairy items found in outlier review
        "olbas",
        "inhalant",
        "decongestant",
        "rosehip",
        "nesfruta",
        "limopani",
        "milk can",
    ],
    "ghee": [
        "body butter",
        "lobia",
        "kala chana",
        "flaxseed",
        "soyabean",
        "banaspati",  # vegetable ghee, different product from desi ghee
        # ghee-based sweets
        "halwa",
        "panjeeri",
        "barfi",
    ],
    "sugar": [
        "sweetener",
        "sweetner",
        "stevia",
        "erythritol",
        "monk fruit",
        "xylitol",
        "keto",
        "neutraved",
        "gugal",
        "sun dried tomato",
        "alum",
        "molasses",
        "jaggery",
        "gur",
        "gud",
        # kitchenware / non-food
        "hob",
        "burner",
        "knob",
        "stove",
        "sugar pot",
        "sugar bowl",
        "candy jar",
        "fertilizer",
        "epsom",
        "husk",
        # thickeners / additives / supplements
        "xanthan",
        "zanthan",
        "thickener",
        "stabilizer",
        "glucose",
        "moringa",
        # diabetic / zero-sugar / substitutes
        "sugar free",
        "zero sugar",
        "no added sugar",
        "sugar control",
        "sugar care",
        "calorie",
        "calarie",
        "substitute",
        "allulose",
        "sweetex",
        # rock sugar / candy / specialty sugars (not granulated white sugar)
        "misri",
        "mishri",
        "nibat",
        "nabat",
        "candy",
        "icing",
        "caster",
        "muscovado",
        "coconut sugar",
        "date sugar",
        "rock sugar",
        "pearl sugar",
        "sugar powder",  # also matches "sugar powdered" / "brown sugar powder"
        "powdered sugar",
        # sweetener typos / non-food found in outlier review
        "sweetenir",
        "subsitute",
        "aspartame",
        "sweet'n low",
        "shopping bag",
        "cream of tartar",
        "goat weed",
    ],
    "flour": [
        "cake mix",
        "pancake mix",
        "corn flour",
        "cornflour",
        "custard powder",
        "keto flour",
        "keto",
        "almond flour",
        "coconut flour",
        "diet",
        "diabetic",
        "diabetes",
        "weight loss",
        "gluten free",
        "sugar watchers",
        "sugar free",
        "sugar control",
        "low gi",
        "buckwheat",
        "soya flour",
        "soyabean",
        "ragi",
        "rice flour",
        "rice powder",
        "rice atta",
        "cereal",
        "rolled wheat",
        "rolled barley",
        "tapioca flour",
        "sagudana",
        "sabudana",
        "amaranth",
        "rajgira",
        "jowar",
        "jawar",
        "sorghum",
        "oat flour",
        "oats flour",
        "bajra atta",
        "bajra flour",
        "pearl millet",
        "millet flour",
        "emmer",
        "chana flour",
        "chickpea flour",
        # "bran" removed -> handled by word-boundary regex above (keeps "brand")
        "flaxseed flour",
        "tempura flour",
        "halwa",
        "sattu",
        "falahari",
        "falhari",
        "farali",
        "self rising",
        "self-rising",
        "self raising",
        "self-raising",
        "daal",
        "besan",
        "chana",
        "urad",
        "maash",
        # non-wheat flours (policy choice, drop if multigrain/barley should stay)
        "barley",
        "multigrain",
        "multi grain",
        "soya bean",
        # more non-wheat flours
        "quinoa",
        "flax seed",
        "singhara",
        "chestnut",
        "barly",  # barley misspelling
        "semolina",
    ],
    "rice": [
        "rice flour",
        "rice paper",
        "rice noodles",
        "sushi",
        "risotto",
        "arborio",
        "paella",
        "poha",
        "murmura",
        "puffed rice",
        "suchi",  # misspelled sushi rice
        "slim rice",  # keto rice
        "ready to heat",  # ready meals
    ],
    "pulses": [
        "flour",
        "besan",
        "wrinkles",
        "acne",
        "pegion",
        "murga",
        "nashpal",
        "panjiri",
    ],
}


def get_commodity_filter_reason(name: str, category: str):
    """
    Returns the exclusion keyword responsible for rejection.
    Returns None when the product is valid.
    """
    title = name.lower()

    for keyword in EXCLUSION_KEYWORDS.get(category, []):
        if keyword in title:
            return keyword

    for pattern in REGEX_EXCLUSION_PATTERNS.get(category, []):
        if pattern.search(name):
            return pattern.pattern

    return None


def is_valid_commodity(name: str, category: str) -> bool:
    """
    Returns False when the product belongs to a known non-commodity
    variant.
    """
    return get_commodity_filter_reason(name, category) is None