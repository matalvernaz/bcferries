ROUTES = {
    # Major routes (Route 01)
    "tsa-swb": {"from": "tsa", "to": "swb", "id": "01"},
    "swb-tsa": {"from": "swb", "to": "tsa", "id": "01"},
    # Horseshoe Bay — Nanaimo (Route 02)
    "hsb-nan": {"from": "hsb", "to": "nan", "id": "02"},
    "nan-hsb": {"from": "nan", "to": "hsb", "id": "02"},
    # Horseshoe Bay — Langdale (Route 03)
    "hsb-lng": {"from": "hsb", "to": "lng", "id": "03"},
    "lng-hsb": {"from": "lng", "to": "hsb", "id": "03"},
    # Swartz Bay — Fulford Harbour (Route 04)
    "swb-ful": {"from": "swb", "to": "ful", "id": "04"},
    "ful-swb": {"from": "ful", "to": "swb", "id": "04"},
    # Swartz Bay — Southern Gulf Islands (Route 05)
    "swb-sgi": {"from": "swb", "to": "sgi", "id": "05"},
    "sgi-swb": {"from": "sgi", "to": "swb", "id": "05"},
    # Crofton — Vesuvius Bay (Route 06)
    "cft-ves": {"from": "cft", "to": "ves", "id": "06"},
    "ves-cft": {"from": "ves", "to": "cft", "id": "06"},
    # Earls Cove — Saltery Bay (Route 07)
    "erl-slt": {"from": "erl", "to": "slt", "id": "07"},
    "slt-erl": {"from": "slt", "to": "erl", "id": "07"},
    # Horseshoe Bay — Bowen Island (Route 08)
    "hsb-bow": {"from": "hsb", "to": "bow", "id": "08"},
    "bow-hsb": {"from": "bow", "to": "hsb", "id": "08"},
    # Tsawwassen — Southern Gulf Islands (Route 09)
    "tsa-sgi": {"from": "tsa", "to": "sgi", "id": "09"},
    "sgi-tsa": {"from": "sgi", "to": "tsa", "id": "09"},
    # Port Hardy — Prince Rupert / Inside Passage (Route 10)
    "pph-ppr": {"from": "pph", "to": "ppr", "id": "10"},
    "ppr-pph": {"from": "ppr", "to": "pph", "id": "10"},
    # Prince Rupert — Skidegate / Haida Gwaii (Route 11)
    "ppr-psk": {"from": "ppr", "to": "psk", "id": "11"},
    "psk-ppr": {"from": "psk", "to": "ppr", "id": "11"},
    # Brentwood Bay — Mill Bay (Route 12)
    "btw-mil": {"from": "btw", "to": "mil", "id": "12"},
    "mil-btw": {"from": "mil", "to": "btw", "id": "12"},
    # Powell River — Comox / Little River (Route 17)
    "pwr-cmx": {"from": "pwr", "to": "cmx", "id": "17"},
    "cmx-pwr": {"from": "cmx", "to": "pwr", "id": "17"},
    # Powell River — Texada Island (Route 18)
    "pwr-tex": {"from": "pwr", "to": "tex", "id": "18"},
    "tex-pwr": {"from": "tex", "to": "pwr", "id": "18"},
    # Nanaimo Harbour — Gabriola Island (Route 19)
    "nah-gab": {"from": "nah", "to": "gab", "id": "19"},
    "gab-nah": {"from": "gab", "to": "nah", "id": "19"},
    # Chemainus — Thetis / Penelakut (Route 20)
    "chm-pen": {"from": "chm", "to": "pen", "id": "20"},
    "pen-chm": {"from": "pen", "to": "chm", "id": "20"},
    "chm-tht": {"from": "chm", "to": "tht", "id": "20"},
    "tht-chm": {"from": "tht", "to": "chm", "id": "20"},
    # Buckley Bay — Denman Island (Route 21)
    "bky-dnm": {"from": "bky", "to": "dnm", "id": "21"},
    "dnm-bky": {"from": "dnm", "to": "bky", "id": "21"},
    # Denman Island East — Hornby Island (Route 22)
    "dne-hrn": {"from": "dne", "to": "hrn", "id": "22"},
    "hrn-dne": {"from": "hrn", "to": "dne", "id": "22"},
    # Campbell River — Quadra Island (Route 23)
    "cam-qdr": {"from": "cam", "to": "qdr", "id": "23"},
    "qdr-cam": {"from": "qdr", "to": "cam", "id": "23"},
    # Heriot Bay — Cortes Island (Route 24)
    "hrb-cor": {"from": "hrb", "to": "cor", "id": "24"},
    "cor-hrb": {"from": "cor", "to": "hrb", "id": "24"},
    # Port McNeill — Alert Bay — Sointula (Route 25)
    "mcn-alr": {"from": "mcn", "to": "alr", "id": "25"},
    "alr-mcn": {"from": "alr", "to": "mcn", "id": "25"},
    "mcn-soi": {"from": "mcn", "to": "soi", "id": "25"},
    "soi-mcn": {"from": "soi", "to": "mcn", "id": "25"},
    # Skidegate — Alliford Bay (Route 26)
    "psk-alf": {"from": "psk", "to": "alf", "id": "26"},
    "alf-psk": {"from": "alf", "to": "psk", "id": "26"},
    # Port Hardy — Bella Coola / Central Coast (Route 28)
    "pph-bec": {"from": "pph", "to": "bec", "id": "28"},
    "bec-pph": {"from": "bec", "to": "pph", "id": "28"},
    # Tsawwassen — Duke Point (Route 30)
    "tsa-duk": {"from": "tsa", "to": "duk", "id": "30"},
    "duk-tsa": {"from": "duk", "to": "tsa", "id": "30"},
}

# SGI return: multiple island terminals feeding into one route
SGI_RETURN_TERMINALS = [
    {"from": "plh", "id": "09", "name": "Salt Spring"},
    {"from": "psb", "id": "09", "name": "Galiano"},
    {"from": "pvb", "id": "09", "name": "Mayne"},
    {"from": "pob", "id": "09", "name": "Pender"},
]

TERMINALS = {
    "hsb": "Horseshoe Bay",
    "bow": "Bowen Island",
    "lng": "Langdale",
    "nan": "Nanaimo (Departure Bay)",
    "tsa": "Tsawwassen",
    "swb": "Swartz Bay",
    "duk": "Nanaimo (Duke Point)",
    "ful": "Fulford Harbour",
    "sgi": "Southern Gulf Islands",
    "plh": "Salt Spring Island (Long Harbour)",
    "psb": "Galiano Island (Sturdies Bay)",
    "pvb": "Mayne Island (Village Bay)",
    "pob": "Pender Island (Otter Bay)",
    "cft": "Crofton",
    "ves": "Vesuvius Bay",
    "erl": "Earls Cove",
    "slt": "Saltery Bay",
    "btw": "Brentwood Bay",
    "mil": "Mill Bay",
    "pwr": "Powell River",
    "cmx": "Comox (Little River)",
    "tex": "Texada Island",
    "nah": "Nanaimo Harbour",
    "gab": "Gabriola Island",
    "chm": "Chemainus",
    "pen": "Penelakut Island",
    "tht": "Thetis Island",
    "bky": "Buckley Bay",
    "dnm": "Denman Island",
    "dne": "Denman Island East",
    "hrn": "Hornby Island",
    "cam": "Campbell River",
    "qdr": "Quadra Island",
    "hrb": "Heriot Bay (Quadra)",
    "cor": "Cortes Island",
    "mcn": "Port McNeill",
    "alr": "Alert Bay",
    "soi": "Sointula",
    "pph": "Port Hardy",
    "ppr": "Prince Rupert",
    "psk": "Skidegate",
    "alf": "Alliford Bay",
    "bec": "Bella Coola",
}

ISLAND_SHORT_NAMES = {
    "plh": "Salt Spring",
    "psb": "Galiano",
    "pvb": "Mayne",
    "pob": "Pender",
}

TERMINALS_LIST = [
    {"slug": "horseshoe-bay", "name": "Horseshoe Bay"},
    {"slug": "tsawwassen", "name": "Tsawwassen"},
    {"slug": "swartz-bay", "name": "Swartz Bay"},
    {"slug": "nanaimo", "name": "Nanaimo"},
    {"slug": "crofton", "name": "Crofton"},
    {"slug": "chemainus", "name": "Chemainus"},
    {"slug": "buckley-bay", "name": "Buckley Bay"},
    {"slug": "denman-island", "name": "Denman Island"},
    {"slug": "campbell-river", "name": "Campbell River"},
    {"slug": "port-mcneill", "name": "Port McNeill"},
    {"slug": "earls-cove", "name": "Earls Cove"},
    {"slug": "powell-river", "name": "Powell River"},
    {"slug": "brentwood-bay", "name": "Brentwood Bay"},
    {"slug": "port-hardy", "name": "Port Hardy"},
    {"slug": "prince-rupert", "name": "Prince Rupert"},
    {"slug": "skidegate", "name": "Skidegate"},
]

# ---- Corridor definitions ----

HSB_CORRIDORS = [
    {
        "slug": "horseshoe-bay-bowen-island",
        "name": "Horseshoe Bay and Bowen Island",
        "outbound": "hsb-bow", "inbound": "bow-hsb",
        "outboundLabel": "Horseshoe Bay", "inboundLabel": "Bowen Island",
    },
    {
        "slug": "horseshoe-bay-langdale",
        "name": "Horseshoe Bay and Langdale",
        "outbound": "hsb-lng", "inbound": "lng-hsb",
        "outboundLabel": "Horseshoe Bay", "inboundLabel": "Langdale",
    },
    {
        "slug": "horseshoe-bay-nanaimo",
        "name": "Horseshoe Bay and Nanaimo (Departure Bay)",
        "outbound": "hsb-nan", "inbound": "nan-hsb",
        "outboundLabel": "Horseshoe Bay", "inboundLabel": "Nanaimo (Departure Bay)",
    },
]

TSA_CORRIDORS = [
    {
        "slug": "tsawwassen-swartz-bay",
        "name": "Tsawwassen and Swartz Bay (Victoria)",
        "outbound": "tsa-swb", "inbound": "swb-tsa",
        "outboundLabel": "Tsawwassen", "inboundLabel": "Swartz Bay",
    },
    {
        "slug": "tsawwassen-duke-point",
        "name": "Tsawwassen and Duke Point (Nanaimo)",
        "outbound": "tsa-duk", "inbound": "duk-tsa",
        "outboundLabel": "Tsawwassen", "inboundLabel": "Duke Point",
    },
    {
        "slug": "tsawwassen-southern-gulf-islands",
        "name": "Tsawwassen and Southern Gulf Islands",
        "outbound": "tsa-sgi", "inbound": "sgi-tsa",
        "outboundLabel": "Tsawwassen", "inboundLabel": "Southern Gulf Islands",
    },
]

SWB_CORRIDORS = [
    {
        "slug": "swartz-bay-fulford-harbour",
        "name": "Swartz Bay and Fulford Harbour (Salt Spring)",
        "outbound": "swb-ful", "inbound": "ful-swb",
        "outboundLabel": "Swartz Bay", "inboundLabel": "Fulford Harbour",
    },
    {
        "slug": "swartz-bay-southern-gulf-islands",
        "name": "Swartz Bay and Southern Gulf Islands",
        "outbound": "swb-sgi", "inbound": "sgi-swb",
        "outboundLabel": "Swartz Bay", "inboundLabel": "Southern Gulf Islands",
    },
]

GULF_ISLAND_CORRIDORS = [
    {
        "slug": "crofton-vesuvius-bay",
        "name": "Crofton and Vesuvius Bay (Salt Spring)",
        "outbound": "cft-ves", "inbound": "ves-cft",
        "outboundLabel": "Crofton", "inboundLabel": "Vesuvius Bay",
    },
    {
        "slug": "chemainus-thetis-penelakut",
        "name": "Chemainus and Thetis / Penelakut Island",
        "outbound": "chm-pen", "inbound": "pen-chm",
        "outboundLabel": "Chemainus", "inboundLabel": "Thetis / Penelakut",
    },
    {
        "slug": "nanaimo-gabriola",
        "name": "Nanaimo Harbour and Gabriola Island",
        "outbound": "nah-gab", "inbound": "gab-nah",
        "outboundLabel": "Nanaimo Harbour", "inboundLabel": "Gabriola Island",
    },
]

SUNSHINE_COAST_CORRIDORS = [
    {
        "slug": "earls-cove-saltery-bay",
        "name": "Earls Cove and Saltery Bay",
        "outbound": "erl-slt", "inbound": "slt-erl",
        "outboundLabel": "Earls Cove", "inboundLabel": "Saltery Bay",
    },
    {
        "slug": "powell-river-comox",
        "name": "Powell River and Comox (Little River)",
        "outbound": "pwr-cmx", "inbound": "cmx-pwr",
        "outboundLabel": "Powell River", "inboundLabel": "Comox (Little River)",
    },
    {
        "slug": "powell-river-texada",
        "name": "Powell River and Texada Island",
        "outbound": "pwr-tex", "inbound": "tex-pwr",
        "outboundLabel": "Powell River", "inboundLabel": "Texada Island",
    },
]

MID_ISLAND_CORRIDORS = [
    {
        "slug": "brentwood-bay-mill-bay",
        "name": "Brentwood Bay and Mill Bay",
        "outbound": "btw-mil", "inbound": "mil-btw",
        "outboundLabel": "Brentwood Bay", "inboundLabel": "Mill Bay",
    },
    {
        "slug": "buckley-bay-denman-island",
        "name": "Buckley Bay and Denman Island",
        "outbound": "bky-dnm", "inbound": "dnm-bky",
        "outboundLabel": "Buckley Bay", "inboundLabel": "Denman Island",
    },
    {
        "slug": "denman-island-hornby-island",
        "name": "Denman Island and Hornby Island",
        "outbound": "dne-hrn", "inbound": "hrn-dne",
        "outboundLabel": "Denman Island", "inboundLabel": "Hornby Island",
    },
]

NORTH_ISLAND_CORRIDORS = [
    {
        "slug": "campbell-river-quadra-island",
        "name": "Campbell River and Quadra Island",
        "outbound": "cam-qdr", "inbound": "qdr-cam",
        "outboundLabel": "Campbell River", "inboundLabel": "Quadra Island",
    },
    {
        "slug": "heriot-bay-cortes-island",
        "name": "Heriot Bay and Cortes Island",
        "outbound": "hrb-cor", "inbound": "cor-hrb",
        "outboundLabel": "Heriot Bay (Quadra)", "inboundLabel": "Cortes Island",
    },
    {
        "slug": "port-mcneill-alert-bay-sointula",
        "name": "Port McNeill and Alert Bay / Sointula",
        "outbound": "mcn-alr", "inbound": "alr-mcn",
        "outboundLabel": "Port McNeill", "inboundLabel": "Alert Bay",
    },
]

NORTH_COAST_CORRIDORS = [
    {
        "slug": "port-hardy-prince-rupert",
        "name": "Port Hardy and Prince Rupert (Inside Passage)",
        "outbound": "pph-ppr", "inbound": "ppr-pph",
        "outboundLabel": "Port Hardy", "inboundLabel": "Prince Rupert",
    },
    {
        "slug": "port-hardy-bella-coola",
        "name": "Port Hardy and Bella Coola (Central Coast)",
        "outbound": "pph-bec", "inbound": "bec-pph",
        "outboundLabel": "Port Hardy", "inboundLabel": "Bella Coola",
    },
    {
        "slug": "prince-rupert-haida-gwaii",
        "name": "Prince Rupert and Skidegate (Haida Gwaii)",
        "outbound": "ppr-psk", "inbound": "psk-ppr",
        "outboundLabel": "Prince Rupert", "inboundLabel": "Skidegate",
    },
    {
        "slug": "skidegate-alliford-bay",
        "name": "Skidegate and Alliford Bay",
        "outbound": "psk-alf", "inbound": "alf-psk",
        "outboundLabel": "Skidegate", "inboundLabel": "Alliford Bay",
    },
]

ALL_CORRIDORS = (
    HSB_CORRIDORS + TSA_CORRIDORS + SWB_CORRIDORS
    + GULF_ISLAND_CORRIDORS + SUNSHINE_COAST_CORRIDORS
    + MID_ISLAND_CORRIDORS + NORTH_ISLAND_CORRIDORS
    + NORTH_COAST_CORRIDORS
)

# ---- Regions (grouped by destination) ----

REGIONS = [
    {
        "name": "Vancouver Island",
        "corridors": [
            TSA_CORRIDORS[0],          # Tsawwassen — Swartz Bay
            TSA_CORRIDORS[1],          # Tsawwassen — Duke Point
            HSB_CORRIDORS[2],          # Horseshoe Bay — Nanaimo
            SUNSHINE_COAST_CORRIDORS[1],  # Powell River — Comox
        ],
    },
    {
        "name": "Gulf Islands",
        "corridors": [
            TSA_CORRIDORS[2],          # Tsawwassen — SGI
            *SWB_CORRIDORS,            # Swartz Bay — Fulford, Swartz Bay — SGI
            *GULF_ISLAND_CORRIDORS,    # Crofton — Vesuvius, Chemainus — Thetis, Nanaimo — Gabriola
        ],
    },
    {
        "name": "Sunshine Coast",
        "corridors": [
            HSB_CORRIDORS[1],          # Horseshoe Bay — Langdale
            HSB_CORRIDORS[0],          # Horseshoe Bay — Bowen Island
            SUNSHINE_COAST_CORRIDORS[0],  # Earls Cove — Saltery Bay
            SUNSHINE_COAST_CORRIDORS[2],  # Powell River — Texada
        ],
    },
    {
        "name": "Central Vancouver Island",
        "corridors": MID_ISLAND_CORRIDORS,
    },
    {
        "name": "Northern Vancouver Island",
        "corridors": NORTH_ISLAND_CORRIDORS,
    },
    {
        "name": "North Coast and Haida Gwaii",
        "corridors": NORTH_COAST_CORRIDORS,
    },
]

# ---- Cameras ----

EXTRA_CAMERAS = {
    "bow-hsb": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_BOW.jpg", "toDestination": None},
    "ful-swb": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_FUL.jpg", "toDestination": None},
    "sgi-tsa": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PLH.jpg", "toDestination": None},
    "sgi-swb": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PLH.jpg", "toDestination": None},
    "plh-tsa": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PLH.jpg", "toDestination": None},
    "psb-tsa": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_STB.jpg", "toDestination": None},
    "pvb-tsa": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PVB.jpg", "toDestination": None},
    "pob-tsa": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_POB.jpg", "toDestination": None},
    "ves-cft": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_VES.jpg", "toDestination": None},
    "gab-nah": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_GAB.jpg", "toDestination": None},
    "pen-chm": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_THT.jpg", "toDestination": None},
    "tht-chm": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_THT.jpg", "toDestination": None},
    "mil-btw": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_MIL.jpg", "toDestination": None},
    "slt-erl": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_SLT.jpg", "toDestination": None},
    "tex-pwr": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_TEX.jpg", "toDestination": None},
    "cmx-pwr": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_CMX.jpg", "toDestination": None},
    "dnm-bky": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_DNM.jpg", "toDestination": None},
    "hrn-dne": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_HRN.jpg", "toDestination": None},
    "dne-hrn": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_DNE.jpg", "toDestination": None},
    "qdr-cam": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_QDR.jpg", "toDestination": None},
    "cor-hrb": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_COR.jpg", "toDestination": None},
    "alr-mcn": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_ALR.jpg", "toDestination": None},
    "soi-mcn": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_ALR.jpg", "toDestination": None},
    "ppr-pph": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PPR.jpg", "toDestination": None},
    "bec-pph": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_BEC.jpg", "toDestination": None},
    "psk-ppr": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PSK.jpg", "toDestination": None},
    "alf-psk": {"outsideTerminal": "https://ccimg.bcferries.com/cc/support/terminals/cam1_ALF.jpg", "toDestination": None},
}

SGI_CAMERAS = [
    {"name": "Salt Spring Island", "url": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PLH.jpg"},
    {"name": "Galiano Island", "url": "https://ccimg.bcferries.com/cc/support/terminals/cam1_STB.jpg"},
    {"name": "Mayne Island", "url": "https://ccimg.bcferries.com/cc/support/terminals/cam1_PVB.jpg"},
    {"name": "Pender Island", "url": "https://ccimg.bcferries.com/cc/support/terminals/cam1_POB.jpg"},
]

# MMSI (Maritime Mobile Service Identity) numbers for AIS vessel tracking.
# All BC Ferries vessels are Canadian-flagged (MID 316).
VESSEL_MMSI = {
    # Major vessels
    "sbc": 316001268,   # Spirit of British Columbia
    "svi": 316001269,   # Spirit of Vancouver Island
    "cel": 316011409,   # Coastal Celebration
    "ins": 316011408,   # Coastal Inspiration
    "ren": 316011407,   # Coastal Renaissance
    "alb": 316001245,   # Queen of Alberni
    "coq": 316001249,   # Queen of Coquitlam
    "cow": 316001251,   # Queen of Cowichan
    "nwm": 316001255,   # Queen of New Westminster
    "oak": 316001257,   # Queen of Oak Bay
    "sur": 316001262,   # Queen of Surrey
    # Salish class
    "eag": 316030626,   # Salish Eagle
    "orc": 316030627,   # Salish Orca
    "rav": 316030628,   # Salish Raven
    "her": 316047943,   # Salish Heron
    # Medium vessels
    "ske": 316001267,   # Skeena Queen
    "cap": 316001247,   # Queen of Capilano
    "cum": 316001252,   # Queen of Cumberland
    "bow": 316001232,   # Bowen Queen
    "mqn": 316001238,   # Mayne Queen
    "bur": 316001246,   # Queen of Burnaby
    "sky": 316012774,   # Malaspina Sky (formerly Island Sky)
    "kah": 316001236,   # Kahloke
    "qni": 316001265,   # Quinitsa
    "qsa": 316001266,   # Quinsam
    "bsc": 316030644,   # Baynes Sound Connector
    "how": 316001234,   # Howe Sound Queen
    "kli": 316001235,   # Klitsa
    "kup": 316009547,   # Kuper (renamed Pune'luxutth)
    "kwu": 316001237,   # Kwuna
    "prq": 316001243,   # Powell River Queen
    "qq2": 316001244,   # Quadra Queen II
    "nip": 316001242,   # North Island Princess
    # Northern vessels
    "nv1": 316194000,   # Northern Adventure
    "nv2": 316014054,   # Northern Expedition
    "nsw": 316036676,   # Northern Sea Wolf
    # Small / retired vessels (MMSI still registered)
    "nim": 316001241,   # Nimpkish
    "tac": 316001271,   # Tachek
    "ten": 316001272,   # Tenaka
}

VESSELS = {
    "alb": {"code": "alb", "name": "Queen of Alberni"},
    "bog": {"code": "bog", "name": "Unassigned Vessel"},
    "bow": {"code": "bow", "name": "Bowen Queen"},
    "bsc": {"code": "bsc", "name": "Baynes Sound Connector"},
    "bur": {"code": "bur", "name": "Queen of Burnaby"},
    "cap": {"code": "cap", "name": "Queen of Capilano"},
    "cel": {"code": "cel", "name": "Coastal Celebration"},
    "chi": {"code": "chi", "name": "Queen of Chilliwack"},
    "coq": {"code": "coq", "name": "Queen of Coquitlam"},
    "cow": {"code": "cow", "name": "Queen of Cowichan"},
    "cum": {"code": "cum", "name": "Queen of Cumberland"},
    "dog": {"code": "dog", "name": "Dogwood Princess II"},
    "eag": {"code": "eag", "name": "Salish Eagle"},
    "esq": {"code": "esq", "name": "Queen of Esquimalt"},
    "her": {"code": "her", "name": "Salish Heron"},
    "how": {"code": "how", "name": "Howe Sound Queen"},
    "ins": {"code": "ins", "name": "Coastal Inspiration"},
    "kah": {"code": "kah", "name": "Kahloke"},
    "kli": {"code": "kli", "name": "Klitsa"},
    "kup": {"code": "kup", "name": "Kuper"},
    "kwu": {"code": "kwu", "name": "Kwuna"},
    "mil": {"code": "mil", "name": "Mill Bay"},
    "mqn": {"code": "mqn", "name": "Mayne Queen"},
    "nan": {"code": "nan", "name": "Queen of Nanaimo"},
    "nim": {"code": "nim", "name": "Nimpkish"},
    "nip": {"code": "nip", "name": "North Island Princess"},
    "nv1": {"code": "nv1", "name": "Northern Adventure"},
    "nv2": {"code": "nv2", "name": "Northern Expedition"},
    "nsw": {"code": "nsw", "name": "Northern Sea Wolf"},
    "nwm": {"code": "nwm", "name": "Queen of New Westminster"},
    "oak": {"code": "oak", "name": "Queen of Oak Bay"},
    "orc": {"code": "orc", "name": "Salish Orca"},
    "prq": {"code": "prq", "name": "Powell River Queen"},
    "qni": {"code": "qni", "name": "Quinitsa"},
    "qpr": {"code": "qpr", "name": "Queen of Prince Rupert"},
    "qq2": {"code": "qq2", "name": "Quadra Queen II"},
    "qsa": {"code": "qsa", "name": "Quinsam"},
    "rav": {"code": "rav", "name": "Salish Raven"},
    "ren": {"code": "ren", "name": "Coastal Renaissance"},
    "saa": {"code": "saa", "name": "Queen of Saanich"},
    "sbc": {"code": "sbc", "name": "Spirit of British Columbia"},
    "ske": {"code": "ske", "name": "Skeena Queen"},
    "sky": {"code": "sky", "name": "Malaspina Sky"},
    "sur": {"code": "sur", "name": "Queen of Surrey"},
    "svi": {"code": "svi", "name": "Spirit of Vancouver Island"},
    "tac": {"code": "tac", "name": "M.V. Tachek"},
    "ten": {"code": "ten", "name": "M. V. Tenaka"},
    "tsa": {"code": "tsa", "name": "Queen of Tsawwassen"},
    "van": {"code": "van", "name": "Queen of Vancouver"},
}

WEEKDAYS = {
    "sunday": 7, "monday": 1, "tuesday": 2, "wednesday": 3,
    "thursday": 4, "friday": 5, "saturday": 6,
}

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

MONTH_NAMES = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
