"""Curated canonical food knowledge base aligned with USDA FDC (CC0), FoodOn (CC BY 4.0),
and INDB / Indian market & culinary nomenclature (CC BY 4.0).

Each entry separates category, food_state, is_prepared_food, pantry_eligible, and budget_treatment,
and includes regional, Hindi, and transliteration aliases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FoodCategory = Literal[
    "vegetable",
    "fruit",
    "pulse",
    "grain",
    "dairy",
    "spice",
    "oil",
    "nut",
    "packaged_grocery",
    "snack",
    "cooked_meal",
    "beverage",
    "meat_seafood",
    "bakery",
    "non_food",
    "other",
]

FoodState = Literal[
    "raw",
    "dried",
    "fresh",
    "frozen",
    "processed",
    "cooked",
    "ready_to_eat",
    "non_food",
    "unknown",
]

BudgetTreatment = Literal[
    "pantry_inventory",
    "outside_food_expenditure",
    "household_non_food",
    "budget_only_grocery",
    "requires_user_review",
]


@dataclass(frozen=True)
class CanonicalFoodRecord:
    food_id: str
    canonical_name: str
    category: FoodCategory
    secondary_category: str
    food_state: FoodState
    is_prepared_food: bool
    pantry_eligible: bool
    budget_treatment: BudgetTreatment
    default_base_unit: Literal["g", "ml", "pcs"]
    default_shelf_life_days: int | None
    source_dataset: str
    source_record_id: str
    aliases: tuple[str, ...] = ()
    regional_names: tuple[str, ...] = ()
    transliterations: tuple[str, ...] = ()
    language: str = "en"


CANONICAL_FOOD_CATALOGUE: tuple[CanonicalFoodRecord, ...] = (
    # --- Vegetables ---
    CanonicalFoodRecord(
        "veg_potato", "Potato", "vegetable", "root_tuber", "fresh", False, True, "pantry_inventory",
        "g", 21, "usda_fdc", "170026",
        aliases=("potatoes", "russet potato", "baby potato"), regional_names=("aloo", "batata", "urulai"),
        transliterations=("alu", "aaloo"),
    ),
    CanonicalFoodRecord(
        "veg_onion", "Onion", "vegetable", "bulb", "fresh", False, True, "pantry_inventory",
        "g", 21, "usda_fdc", "170000",
        aliases=("onions", "red onion", "white onion", "shallot"), regional_names=("pyaz", "kanda", "vengayam"),
        transliterations=("pyaaz", "piyaz"),
    ),
    CanonicalFoodRecord(
        "veg_tomato", "Tomato", "vegetable", "fruiting_veg", "fresh", False, True, "pantry_inventory",
        "g", 7, "usda_fdc", "170457",
        aliases=("tomatoes", "hybrid tomato", "desi tomato", "cherry tomato"), regional_names=("tamatar", "thakkali"),
        transliterations=("tamaatar",),
    ),
    CanonicalFoodRecord(
        "veg_okra", "Okra", "vegetable", "pod_veg", "fresh", False, True, "pantry_inventory",
        "g", 5, "usda_fdc", "169260",
        aliases=("lady finger", "ladies finger", "ladyfinger"), regional_names=("bhindi", "vendakkai", "bendakaya"),
        transliterations=("bhindee",),
    ),
    CanonicalFoodRecord(
        "veg_bottle_gourd", "Bottle Gourd", "vegetable", "gourd", "fresh", False, True, "pantry_inventory",
        "g", 7, "indb_ifct", "D014",
        aliases=("calabash", "opo squash"), regional_names=("lauki", "ghiya", "doodhi", "sorakkai"),
        transliterations=("loki", "dudhi"),
    ),
    CanonicalFoodRecord(
        "veg_spinach", "Spinach", "vegetable", "leafy_green", "fresh", False, True, "pantry_inventory",
        "g", 4, "usda_fdc", "168462",
        aliases=("baby spinach", "spinach leaves"), regional_names=("palak", "keerai"),
        transliterations=("paalak",),
    ),
    CanonicalFoodRecord(
        "veg_cauliflower", "Cauliflower", "vegetable", "cruciferous", "fresh", False, True, "pantry_inventory",
        "g", 7, "usda_fdc", "169986",
        aliases=("cauliflower florets",), regional_names=("gobhi", "phool gobhi", "gobi"),
        transliterations=("phoolgobi",),
    ),
    CanonicalFoodRecord(
        "veg_eggplant", "Eggplant", "vegetable", "fruiting_veg", "fresh", False, True, "pantry_inventory",
        "g", 6, "usda_fdc", "169228",
        aliases=("brinjal", "aubergine"), regional_names=("baingan", "vankaya", "kathirikai"),
        transliterations=("baigan",),
    ),
    CanonicalFoodRecord(
        "veg_green_peas", "Green Peas", "vegetable", "legume_fresh", "fresh", False, True, "pantry_inventory",
        "g", 7, "usda_fdc", "170419",
        aliases=("peas", "shelled peas", "frozen peas"), regional_names=("matar", "hara matar", "pattani"),
        transliterations=("mutter",),
    ),
    CanonicalFoodRecord(
        "veg_ginger", "Ginger", "vegetable", "rhizome", "fresh", False, True, "pantry_inventory",
        "g", 14, "usda_fdc", "169231",
        aliases=("fresh ginger", "ginger root"), regional_names=("adrak", "inji", "allam"),
        transliterations=("adrakh",),
    ),
    CanonicalFoodRecord(
        "veg_garlic", "Garlic", "vegetable", "bulb", "fresh", False, True, "pantry_inventory",
        "g", 30, "usda_fdc", "169230",
        aliases=("garlic pods", "peeled garlic"), regional_names=("lehsun", "lasun", "poondu"),
        transliterations=("lahsun", "lahsoon"),
    ),
    CanonicalFoodRecord(
        "veg_coriander", "Coriander Leaves", "vegetable", "herb", "fresh", False, True, "pantry_inventory",
        "g", 4, "usda_fdc", "169997",
        aliases=("cilantro", "fresh coriander", "coriander"), regional_names=("dhaniya", "kothimbir", "kothamalli"),
        transliterations=("dhania",),
    ),
    CanonicalFoodRecord(
        "veg_green_chilli", "Green Chilli", "vegetable", "pepper", "fresh", False, True, "pantry_inventory",
        "g", 10, "indb_ifct", "D021",
        aliases=("green chili", "green chilies"), regional_names=("hari mirch", "pachai milagai"),
        transliterations=("mirchi",),
    ),
    CanonicalFoodRecord(
        "veg_zucchini", "Zucchini", "vegetable", "squash", "fresh", False, True, "pantry_inventory",
        "g", 7, "usda_fdc", "169291",
        aliases=("zucchini green", "zuchinni green", "courgette"),
    ),
    CanonicalFoodRecord(
        "veg_lettuce", "Lettuce", "vegetable", "leafy_green", "fresh", False, True, "pantry_inventory",
        "g", 5, "usda_fdc", "169247",
        aliases=("green lettuce", "iceberg lettuce", "romaine lettuce"),
    ),
    # --- Fruits ---
    CanonicalFoodRecord(
        "fruit_apple", "Apple", "fruit", "pome", "fresh", False, True, "pantry_inventory",
        "g", 14, "usda_fdc", "171688",
        aliases=("apples", "daily apple", "red delicious", "fuji apple", "royal gala"), regional_names=("seb", "sebu"),
        transliterations=("saib",),
    ),
    CanonicalFoodRecord(
        "fruit_banana", "Banana", "fruit", "tropical", "fresh", False, True, "pantry_inventory",
        "g", 5, "usda_fdc", "173944",
        aliases=("bananas", "banana cavendish", "robusta banana", "yelakki banana"), regional_names=("kela", "vazhaipazham"),
        transliterations=("kelaa",),
    ),
    CanonicalFoodRecord(
        "fruit_dates", "Dates", "fruit", "dried_fruit", "dried", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "171726",
        aliases=("arabian dates", "kimia dates", "medjool dates", "seedless dates"), regional_names=("khajoor",),
        transliterations=("khajur",),
    ),
    CanonicalFoodRecord(
        "fruit_papaya", "Papaya", "fruit", "tropical", "fresh", False, True, "pantry_inventory",
        "g", 5, "usda_fdc", "169926",
        aliases=("papaya raw", "ripe papaya"), regional_names=("papita", "pappali"),
        transliterations=("papeeta",),
    ),
    # --- Pulses & Legumes ---
    CanonicalFoodRecord(
        "pulse_toor_dal", "Toor Dal", "pulse", "split_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "B018",
        aliases=("pigeon peas", "split pigeon peas", "toor dall", "tuvar dal"), regional_names=("arhar dal", "tur dal", "tuvaram paruppu"),
        transliterations=("toor daal", "arhar daal", "tuar dal"),
    ),
    CanonicalFoodRecord(
        "pulse_chana_dal", "Chana Dal", "pulse", "split_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "B006",
        aliases=("split bengal gram", "split chickpeas", "chana daal"), regional_names=("chana dal", "kadalai paruppu"),
        transliterations=("channa dal", "chanaa dal"),
    ),
    CanonicalFoodRecord(
        "pulse_black_chickpeas", "Black Chickpeas", "pulse", "whole_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "B005",
        aliases=("brown chickpeas", "desi chana"), regional_names=("kala chana", "chana kala", "kadalai"),
        transliterations=("kaala chana", "kala channa"),
    ),
    CanonicalFoodRecord(
        "pulse_kabuli_chana", "White Chickpeas", "pulse", "whole_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "173756",
        aliases=("chickpeas", "garbanzo beans", "chole"), regional_names=("kabuli chana", "safed chana"),
        transliterations=("kaabuli chana",),
    ),
    CanonicalFoodRecord(
        "pulse_moong_dal", "Moong Dal", "pulse", "split_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "B012",
        aliases=("split mung beans", "yellow moong dal", "green gram"), regional_names=("moong", "pasi paruppu"),
        transliterations=("mung dal", "moong daal"),
    ),
    CanonicalFoodRecord(
        "pulse_masoor_dal", "Masoor Dal", "pulse", "split_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "B010",
        aliases=("red lentils", "split red lentils"), regional_names=("masoor", "lal masoor"),
        transliterations=("masur dal",),
    ),
    CanonicalFoodRecord(
        "pulse_urad_dal", "Urad Dal", "pulse", "split_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "B020",
        aliases=("black gram", "split black gram"), regional_names=("urad", "ulundu"),
        transliterations=("udad dal",),
    ),
    CanonicalFoodRecord(
        "pulse_rajma", "Kidney Beans", "pulse", "whole_legume", "dried", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "173734",
        aliases=("red kidney beans", "rajma chitra", "kashmiri rajma"), regional_names=("rajma",),
        transliterations=("raajma",),
    ),
    # --- Grains, Flour & Millets ---
    CanonicalFoodRecord(
        "grain_basmati_rice", "Basmati Rice", "grain", "rice", "dried", False, True, "pantry_inventory",
        "g", 365, "indb_ifct", "A014",
        aliases=("long grain rice", "sona masoori rice", "steamed rice", "raw rice"), regional_names=("chawal", "arisi"),
        transliterations=("chaawal",),
    ),
    CanonicalFoodRecord(
        "grain_wheat_flour", "Whole Wheat Flour", "grain", "flour", "processed", False, True, "pantry_inventory",
        "g", 120, "indb_ifct", "A019",
        aliases=("chakki atta", "shudh chakki atta", "whole wheat atta", "wheat flour"), regional_names=("atta", "gehu ka atta", "godhumai maavu"),
        transliterations=("aata", "aatta"),
    ),
    CanonicalFoodRecord(
        "grain_gram_flour", "Gram Flour", "grain", "flour", "processed", False, True, "pantry_inventory",
        "g", 120, "indb_ifct", "B004",
        aliases=("chickpea flour", "bengal gram flour"), regional_names=("besan", "kadalai maavu"),
        transliterations=("baisan",),
    ),
    CanonicalFoodRecord(
        "grain_semolina", "Semolina", "grain", "flour", "processed", False, True, "pantry_inventory",
        "g", 120, "usda_fdc", "168934",
        aliases=("bombay rava", "sooji rava"), regional_names=("sooji", "suji", "rava"),
        transliterations=("sujee",),
    ),
    CanonicalFoodRecord(
        "grain_oats", "Rolled Oats", "grain", "cereal", "processed", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "173904",
        aliases=("oats", "organic oats", "instant oats"), regional_names=("jai",),
    ),
    CanonicalFoodRecord(
        "grain_poha", "Flattened Rice", "grain", "cereal", "processed", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "A015",
        aliases=("beaten rice", "thick poha"), regional_names=("poha", "aval", "chivda"),
        transliterations=("pohaa",),
    ),
    CanonicalFoodRecord(
        "grain_ragi", "Finger Millet", "grain", "millet", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "A006",
        aliases=("ragi flour", "nachni"), regional_names=("ragi", "kezhvaragu"),
    ),
    # --- Dairy & Eggs ---
    CanonicalFoodRecord(
        "dairy_milk", "Milk", "dairy", "fluid_dairy", "fresh", False, True, "pantry_inventory",
        "ml", 4, "usda_fdc", "171265",
        aliases=("toned milk", "full cream milk", "whole milk", "taaza milk", "goodlife milk", "tetra pack milk"),
        regional_names=("doodh", "paal"), transliterations=("dudh",),
    ),
    CanonicalFoodRecord(
        "dairy_paneer", "Paneer", "dairy", "fresh_cheese", "fresh", False, True, "pantry_inventory",
        "g", 7, "indb_ifct", "L007",
        aliases=("fresh paneer", "malai paneer", "cottage cheese", "low fat paneer"),
        regional_names=("paneer",), transliterations=("panir", "panner"),
    ),
    CanonicalFoodRecord(
        "dairy_curd", "Curd", "dairy", "fermented_dairy", "fresh", False, True, "pantry_inventory",
        "g", 7, "indb_ifct", "L003",
        aliases=("yogurt", "plain yogurt", "greek yogurt", "set curd"), regional_names=("dahi", "thayir"),
        transliterations=("dahee",),
    ),
    CanonicalFoodRecord(
        "dairy_butter", "Butter", "dairy", "dairy_fat", "processed", False, True, "pantry_inventory",
        "g", 90, "usda_fdc", "173410",
        aliases=("salted butter", "pasteurised butter", "unsalted butter"), regional_names=("makkhan", "vennai"),
        transliterations=("makhan",),
    ),
    CanonicalFoodRecord(
        "dairy_ghee", "Ghee", "dairy", "clarified_fat", "processed", False, True, "pantry_inventory",
        "ml", 180, "indb_ifct", "L005",
        aliases=("cow ghee", "pure ghee", "clarified butter", "desi ghee"), regional_names=("ghee", "nei"),
    ),
    CanonicalFoodRecord(
        "dairy_cheese", "Cheese", "dairy", "cheese", "processed", False, True, "pantry_inventory",
        "g", 45, "usda_fdc", "173414",
        aliases=("cheddar cheese", "mozzarella cheese", "cheese slices", "cheese cubes"),
    ),
    CanonicalFoodRecord(
        "egg_chicken", "Eggs", "meat_seafood", "eggs", "fresh", False, True, "pantry_inventory",
        "pcs", 14, "usda_fdc", "171287",
        aliases=("egg", "brown eggs", "white eggs", "farm eggs"), regional_names=("anda", "muttai"),
        transliterations=("ande",),
    ),
    # --- Meat & Seafood ---
    CanonicalFoodRecord(
        "meat_chicken", "Chicken", "meat_seafood", "poultry", "fresh", False, True, "pantry_inventory",
        "g", 3, "usda_fdc", "171077",
        aliases=("chicken breast", "chicken curry cut", "boneless chicken"), regional_names=("murgh",),
    ),
    CanonicalFoodRecord(
        "seafood_fish", "Fish", "meat_seafood", "seafood", "fresh", False, True, "pantry_inventory",
        "g", 2, "usda_fdc", "175167",
        aliases=("rohu fish", "salmon", "pomfret", "fish fillet"), regional_names=("machli", "meen"),
    ),
    # --- Spices, Herbs & Condiments ---
    CanonicalFoodRecord(
        "spice_turmeric", "Turmeric Powder", "spice", "ground_spice", "dried", False, True, "pantry_inventory",
        "g", 365, "indb_ifct", "G019",
        aliases=("turmeric", "haldi powder"), regional_names=("haldi", "manjal"),
    ),
    CanonicalFoodRecord(
        "spice_cumin", "Cumin Seeds", "spice", "whole_spice", "dried", False, True, "pantry_inventory",
        "g", 365, "usda_fdc", "170923",
        aliases=("cumin", "cumin powder", "jeera powder"), regional_names=("jeera", "zeera", "jeeragam"),
    ),
    CanonicalFoodRecord(
        "spice_mustard_seeds", "Mustard Seeds", "spice", "whole_spice", "dried", False, True, "pantry_inventory",
        "g", 365, "usda_fdc", "170929",
        aliases=("black mustard", "mustard small"), regional_names=("rai", "sarson", "kadugu"),
    ),
    CanonicalFoodRecord(
        "spice_red_chilli", "Red Chilli Powder", "spice", "ground_spice", "dried", False, True, "pantry_inventory",
        "g", 365, "indb_ifct", "G006",
        aliases=("chilli powder", "kashmiri mirch", "cayenne pepper"), regional_names=("lal mirch",),
    ),
    CanonicalFoodRecord(
        "spice_hing", "Asafoetida", "spice", "aromatic", "dried", False, True, "pantry_inventory",
        "g", 365, "indb_ifct", "G001",
        aliases=("compounded asafoetida", "premium hing"), regional_names=("hing", "perungayam"),
    ),
    CanonicalFoodRecord(
        "spice_tamarind", "Tamarind", "spice", "souring_agent", "dried", False, True, "pantry_inventory",
        "g", 180, "indb_ifct", "E058",
        aliases=("seedless tamarind", "tamarind paste"), regional_names=("imli", "puli"),
    ),
    CanonicalFoodRecord(
        "spice_salt", "Salt", "spice", "mineral_seasoning", "processed", False, True, "pantry_inventory",
        "g", 730, "usda_fdc", "173468",
        aliases=("iodized salt", "table salt", "rock salt", "sendha namak"), regional_names=("namak", "uppu"),
    ),
    # --- Oils & Cooking Ingredients ---
    CanonicalFoodRecord(
        "oil_sunflower", "Sunflower Oil", "oil", "cooking_oil", "processed", False, True, "pantry_inventory",
        "ml", 365, "usda_fdc", "171025",
        aliases=("refined sunflower oil",),
    ),
    CanonicalFoodRecord(
        "oil_soyabean", "Soyabean Oil", "oil", "cooking_oil", "processed", False, True, "pantry_inventory",
        "ml", 365, "usda_fdc", "171024",
        aliases=("refined soyabean oil", "soybean oil"),
    ),
    CanonicalFoodRecord(
        "oil_mustard", "Mustard Oil", "oil", "cooking_oil", "processed", False, True, "pantry_inventory",
        "ml", 365, "indb_ifct", "T004",
        aliases=("kachi ghani mustard oil",), regional_names=("sarson ka tel",),
    ),
    CanonicalFoodRecord(
        "oil_olive", "Olive Oil", "oil", "cooking_oil", "processed", False, True, "pantry_inventory",
        "ml", 365, "usda_fdc", "171413",
        aliases=("extra virgin olive oil", "olive oil"),
    ),
    CanonicalFoodRecord(
        "oil_coconut", "Coconut Oil", "oil", "cooking_oil", "processed", False, True, "pantry_inventory",
        "ml", 365, "usda_fdc", "171412",
        aliases=("virgin coconut oil", "cold pressed coconut oil"), regional_names=("nariyal tel",),
    ),
    # --- Nuts & Seeds ---
    CanonicalFoodRecord(
        "nut_almonds", "Almonds", "nut", "tree_nut", "dried", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "170567",
        aliases=("whole almond", "california almonds"), regional_names=("badam",),
    ),
    CanonicalFoodRecord(
        "nut_cashews", "Cashews", "nut", "tree_nut", "dried", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "170162",
        aliases=("cashew nuts", "whole cashew"), regional_names=("kaju",),
    ),
    CanonicalFoodRecord(
        "nut_peanuts", "Peanuts", "nut", "legume_nut", "dried", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "172430",
        aliases=("raw peanuts", "groundnuts"), regional_names=("moongphali", "shengdana"),
    ),
    # --- Packaged Groceries, Bakery, Snacks & Beverages ---
    CanonicalFoodRecord(
        "pkg_sugar", "Sugar", "packaged_grocery", "sweetener", "processed", False, True, "pantry_inventory",
        "g", 730, "usda_fdc", "169655",
        aliases=("brown sugar", "white sugar", "cane sugar", "jaggery"), regional_names=("cheeni", "shakkar", "gud"),
    ),
    CanonicalFoodRecord(
        "pkg_noodles", "Instant Noodles", "packaged_grocery", "pasta_noodles", "processed", False, True, "pantry_inventory",
        "g", 270, "foodon", "FOODON_03310306",
        aliases=("masala noodles", "hakka noodles", "maggi noodles"),
    ),
    CanonicalFoodRecord(
        "bakery_bread", "Whole Wheat Bread", "bakery", "bread", "processed", False, True, "pantry_inventory",
        "g", 5, "usda_fdc", "172688",
        aliases=("bread", "brown bread", "multigrain bread", "sandwich bread"), regional_names=("pav",),
    ),
    CanonicalFoodRecord(
        "snack_biscuits", "Biscuits", "snack", "cookies", "ready_to_eat", False, True, "pantry_inventory",
        "g", 180, "foodon", "FOODON_03304388",
        aliases=("marie biscuits", "digestive biscuits", "good day biscuits", "cookies"),
    ),
    CanonicalFoodRecord(
        "snack_chocolate", "Milk Chocolate", "snack", "confectionery", "ready_to_eat", False, True, "pantry_inventory",
        "g", 180, "usda_fdc", "167587",
        aliases=("dark chocolate", "chocolate bar"),
    ),
    CanonicalFoodRecord(
        "snack_namkeen", "Namkeen", "snack", "savory_snack", "ready_to_eat", False, True, "pantry_inventory",
        "g", 90, "indb_ifct", "S010",
        aliases=("soya katori", "namkeen masala", "bhujia", "mixture"),
    ),
    CanonicalFoodRecord(
        "bev_tea", "Tea", "beverage", "dry_tea", "dried", False, True, "pantry_inventory",
        "g", 365, "usda_fdc", "171917",
        aliases=("tea leaves", "assam tea", "green tea", "red label tea"), regional_names=("chai patti",),
    ),
    CanonicalFoodRecord(
        "bev_coffee", "Coffee", "beverage", "dry_coffee", "dried", False, True, "pantry_inventory",
        "g", 365, "usda_fdc", "171890",
        aliases=("instant coffee", "filter coffee powder"),
    ),
    # --- Cooked Dishes & Restaurant Meals (NOT pantry-eligible!) ---
    CanonicalFoodRecord(
        "meal_phulka", "Phulka", "cooked_meal", "indian_flatbread", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R001",
        aliases=("roti", "chapati", "chapathi", "tawa roti", "butter roti"),
    ),
    CanonicalFoodRecord(
        "meal_naan", "Naan", "cooked_meal", "indian_flatbread", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R002",
        aliases=("butter naan", "garlic naan", "tandoori naan", "kulcha", "paratha"),
    ),
    CanonicalFoodRecord(
        "meal_kadai_paneer", "Kadai Paneer", "cooked_meal", "prepared_curry", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R041",
        aliases=("patiala kadai paneer", "paneer butter masala", "shahi paneer", "palak paneer", "paneer tikka"),
    ),
    CanonicalFoodRecord(
        "meal_chole_masala", "Chole Masala", "cooked_meal", "prepared_curry", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R044",
        aliases=("chana masala", "pindi chole", "dal makhani", "dal tadka"),
    ),
    CanonicalFoodRecord(
        "meal_biryani", "Biryani", "cooked_meal", "rice_dish", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R025",
        aliases=("veg biryani", "chicken biryani", "hyderabadi biryani", "dum biryani", "pulao", "fried rice"),
    ),
    CanonicalFoodRecord(
        "meal_dosa", "Dosa", "cooked_meal", "south_indian_meal", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R012",
        aliases=("masala dosa", "plain dosa", "idli", "medu vada", "uttapam"),
    ),
    CanonicalFoodRecord(
        "meal_pizza_burger", "Fast Food Meal", "cooked_meal", "restaurant_meal", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "foodon", "FOODON_00001714",
        aliases=("pizza", "burger", "sandwich", "manchurian", "thali", "butter chicken", "samosa"),
    ),
    CanonicalFoodRecord(
        "pkg_dosa_batter", "Dosa Batter", "packaged_grocery", "batter", "processed", False, True, "pantry_inventory",
        "g", 7, "indb_ifct", "P010",
        aliases=("idli dosa batter", "idli batter", "dosa batter", "id dosa batter"),
    ),
    CanonicalFoodRecord(
        "meal_cooked_rice", "Cooked Rice Meal", "cooked_meal", "rice_dish", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R026",
        aliases=("cooked rice", "steamed rice", "jeera rice", "curd rice", "lemon rice", "tamarind rice", "veg fried rice", "cooked rice meal"),
    ),
    CanonicalFoodRecord(
        "meal_paneer_curry", "Paneer Curry", "cooked_meal", "curry", "cooked", True, False, "outside_food_expenditure",
        "pcs", None, "indb_ifct", "R027",
        aliases=("paneer curry", "paneer butter masala", "shahi paneer", "matar paneer", "paneer tikka masala"),
    ),
    CanonicalFoodRecord(
        "other_ice_cream", "Ice Cream", "snack", "frozen_dessert", "frozen", True, True, "pantry_inventory",
        "ml", 60, "usda_fdc", "167575",
        aliases=("ice cream cup", "choco chunk ice cream", "kulfi"),
    ),
)
