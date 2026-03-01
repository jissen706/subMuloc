"""
Mechanism vocabulary v1 for drug-disease vectorization.

ORDER OF MECH_NODES IS FIXED within v1 — never reorder, never remove.
Adding new nodes requires a new vocab version (mech_vocab_v2, etc.).
"""
from __future__ import annotations

import hashlib

MECH_VOCAB_VERSION = "mech_vocab_v1"

# fmt: off
MECH_NODES: list[str] = [
    "cGAS_STING",            # 0  innate DNA sensing
    "typeI_interferon",      # 1  IFN-α/β signalling
    "JAK_STAT",              # 2  cytokine receptor transduction
    "NFkB",                  # 3  canonical inflammatory master TF
    "TNF_IL6",               # 4  pro-inflammatory cytokines
    "NLRP3_inflammasome",    # 5  IL-1β / IL-18 maturation
    "complement",            # 6  complement cascade
    "mTOR",                  # 7  nutrient / growth sensor
    "MAPK",                  # 8  RAS-RAF-MEK-ERK cascade
    "PI3K_AKT",              # 9  survival / proliferation
    "apoptosis",             # 10 programmed cell death
    "cell_cycle",            # 11 CDK / cyclin checkpoints
    "DNA_damage",            # 12 DSB repair / genome stability
    "autophagy",             # 13 self-degradation / mitophagy
    "lysosome",              # 14 lysosomal storage & biogenesis
    "proteasome_ubiquitin",  # 15 UPS protein clearance
    "ER_stress_UPR",         # 16 unfolded protein response
    "mitochondria",          # 17 OXPHOS / membrane potential
    "oxidative_stress",      # 18 ROS / redox balance
    "lipid_metabolism",      # 19 cholesterol, FA, sphingolipids
    "glucose_metabolism",    # 20 glycolysis / insulin signalling
    "ion_channels",          # 21 cardiac/neuronal channelopathies
    "sarcomere",             # 22 cardiac contractile apparatus
    "ECM_collagen",          # 23 extracellular matrix integrity
    "fibrosis_TGFb",         # 24 TGF-β / SMAD fibrotic axis
]
# fmt: on

# Stable hash of the ordered node list — stored in DB to detect vocab drift.
MECH_NODES_HASH: str = hashlib.sha256("|".join(MECH_NODES).encode()).hexdigest()

# ---------------------------------------------------------------------------
# MECH_ALIASES
# All strings MUST be lowercase.  Order within each list does not matter.
# Matching: alias in normalize(term)  OR  normalize(term) in alias
# ---------------------------------------------------------------------------
MECH_ALIASES: dict[str, list[str]] = {
    "cGAS_STING": [
        "cgas", "sting", "cgas-sting", "cgas sting", "cgas/sting",
        "tmem173", "innate dna sensing", "cyclic gmp-amp", "cgamp",
        "sting pathway", "interferonopathy", "type i interferonopathy",
        "nucleic acid sensing", "dna sensor", "cdgamp", "dna sensing",
        "cytosolic dna", "innate immune dna",
    ],
    "typeI_interferon": [
        "interferon", "ifn-alpha", "ifn-beta", "type i interferon",
        "ifna", "ifnb", "ifn alpha", "ifn beta", "isg",
        "interferon stimulated", "isre", "mx1", "oas1", "ifit",
        "ifnar", "interferon response", "interferon signaling",
        "antiviral response", "interferon pathway", "type 1 interferon",
    ],
    "JAK_STAT": [
        "jak", "stat", "jak-stat", "jak stat", "jak/stat", "jak1", "jak2",
        "tyk2", "stat1", "stat2", "stat3", "stat4", "stat6",
        "janus kinase", "tofacitinib", "ruxolitinib", "baricitinib",
        "upadacitinib", "filgotinib", "jak inhibitor", "jak-stat pathway",
        "jak stat signaling",
    ],
    "NFkB": [
        "nfkb", "nf-kb", "nf-kappab", "nf kappab", "nf kb",
        "nuclear factor kappa", "ikk", "ikba", "ikbkb", "rela", "p65",
        "canonical nfkb", "nfkb signaling", "nfkb pathway",
        "nf-kb pathway", "nuclear factor b",
    ],
    "TNF_IL6": [
        "tnf", "tumor necrosis factor", "tnf-alpha", "tnfalpha",
        "il-6", "il6", "interleukin-6", "interleukin 6",
        "il-1", "il1", "il1b", "interleukin-1", "interleukin 1",
        "il-17", "il17", "il17a", "il-23", "il23", "il-4", "il4",
        "il-13", "il13", "tocilizumab", "sarilumab", "anti-tnf",
        "cytokine storm", "pro-inflammatory cytokine",
        "inflammatory cytokine", "cytokine release",
    ],
    "NLRP3_inflammasome": [
        "nlrp3", "inflammasome", "il-18", "il18", "il18",
        "pyroptosis", "caspase-1", "caspase 1", "pyrin",
        "nalp3", "nlrp", "inflammasome activation", "gasdermin",
        "nlrp3 inflammasome", "asc", "pycard",
    ],
    "complement": [
        "complement", "c3", "c5", "c5a", "factor h", "cfh",
        "membrane attack complex", "mac", "complement activation",
        "lectin pathway", "alternative pathway", "classical pathway",
        "complement system", "c1q", "c4b", "complement cascade",
        "complement pathway",
    ],
    "mTOR": [
        "mtor", "rapamycin", "sirolimus", "everolimus", "temsirolimus",
        "torc1", "mtorc1", "mtorc2", "p70 s6k", "s6k1", "4e-bp1",
        "mtor pathway", "mtor signaling", "mtor inhibitor", "rapalog",
        "mtor complex", "pi3k mtor", "pi3k/mtor",
    ],
    "MAPK": [
        "mapk", "erk", "mek", "raf", "braf", "v600e",
        "kras", "nras", "hras", "ras-raf", "map kinase",
        "extracellular signal", "erk1/2", "erk1 2", "erk1 erk2",
        "p38", "jnk", "sapk", "ras pathway", "ras/mapk",
        "ras raf mek erk", "mitogen-activated", "ras signaling",
        "mapk pathway", "mapk signaling",
    ],
    "PI3K_AKT": [
        "pi3k", "akt", "pten", "pik3ca", "pi3k/akt", "pi3k akt",
        "phosphoinositide", "pkb", "akt1", "akt2",
        "pi3 kinase", "phosphatidylinositol", "pi3k pathway",
        "pi3k signaling", "pi3k/akt/mtor", "phosphoinositide 3-kinase",
    ],
    "apoptosis": [
        "apoptosis", "apoptotic", "caspase", "bcl-2", "bcl2",
        "bax", "bak", "cytochrome c", "programmed cell death",
        "pcd", "apaf", "pro-apoptotic", "anti-apoptotic",
        "p53", "tp53", "cell death", "intrinsic apoptosis",
        "extrinsic apoptosis", "death receptor", "apoptose",
    ],
    "cell_cycle": [
        "cell cycle", "cdk", "cyclin", "g1", "g2", "s phase",
        "rb", "retinoblastoma", "e2f", "cdk4", "cdk6",
        "p21", "p16", "cdkn2a", "mitosis", "proliferation",
        "cell division", "cell growth", "g1/s", "g2/m",
        "checkpoint", "cell cycle arrest",
    ],
    "DNA_damage": [
        "dna damage", "dna repair", "atm", "atr", "chk1", "chk2",
        "brca", "brca1", "brca2", "homologous recombination", "nhej",
        "base excision", "nucleotide excision", "dna double strand",
        "dna strand break", "dna replication stress",
        "genome instability", "genotoxic", "dna methylation",
        "parp", "dna integrity", "dna damage response",
    ],
    "autophagy": [
        "autophagy", "autophagic", "atg", "beclin", "lc3",
        "p62", "sqstm1", "autophagosome", "selective autophagy",
        "mitophagy", "xenophagy", "autophagic flux",
        "atg5", "atg7", "ulk1", "autophagy pathway",
    ],
    "lysosome": [
        "lysosome", "lysosomal", "cathepsin", "lamp1", "lamp2",
        "v-atpase", "gba", "glucocerebrosidase", "npc1",
        "sphingolipid", "glycosphingolipid", "lysosomal storage",
        "lysosomal biogenesis", "tfeb", "lysosomal degradation",
        "lysosomal function", "lysosomal pathway",
    ],
    "proteasome_ubiquitin": [
        "proteasome", "ubiquitin", "ubiquitination", "26s proteasome",
        "psmb", "ubiquitin-proteasome", "protein degradation",
        "erad", "cullin", "e3 ligase", "deubiquitinase",
        "sumoylation", "protein quality control", "proteasomal",
        "ups", "ubiquitin proteasome system",
    ],
    "ER_stress_UPR": [
        "er stress", "unfolded protein response", "upr",
        "xbp1", "atf6", "eif2", "chop", "grp78", "bip",
        "perk", "ire1", "endoplasmic reticulum stress",
        "protein folding", "chaperone", "protein misfolding",
        "endoplasmic reticulum", "er dysfunction",
    ],
    "mitochondria": [
        "mitochondria", "mitochondrial", "electron transport",
        "oxidative phosphorylation", "atp synthase",
        "cytochrome c oxidase", "complex i", "complex ii",
        "complex iii", "complex iv", "membrane potential",
        "mtdna", "mitochondrial dna", "mitochondrial dysfunction",
        "mitochondrial biogenesis", "atp production", "oxphos",
    ],
    "oxidative_stress": [
        "oxidative stress", "ros", "reactive oxygen", "antioxidant",
        "nrf2", "sod", "catalase", "glutathione", "gsh", "h2o2",
        "superoxide", "lipid peroxidation", "redox", "oxidant",
        "free radical", "nadph oxidase", "oxidative damage",
    ],
    "lipid_metabolism": [
        "lipid", "cholesterol", "fatty acid", "triglyceride",
        "ldl", "hdl", "hmg-coa", "statin", "lipogenesis",
        "lipolysis", "adipogenesis", "ceramide", "sphingomyelin",
        "phospholipid", "lipid metabolism", "fatty acid oxidation",
        "beta-oxidation", "lipoprotein", "lipid pathway",
    ],
    "glucose_metabolism": [
        "glucose", "glycolysis", "gluconeogenesis", "insulin",
        "insulin resistance", "glut", "hexokinase", "glycogen",
        "diabetes", "hyperglycemia", "warburg", "glucose uptake",
        "glucose metabolism", "glycemic", "insulin signaling",
    ],
    "ion_channels": [
        "ion channel", "sodium channel", "potassium channel",
        "calcium channel", "scn5a", "kcnq1", "kcnh2", "herg",
        "ryr2", "channelopathy", "long qt", "lqts", "brugada",
        "arrhythmia", "action potential", "depolarization",
        "cardiac ion", "electrophysiology", "qt prolongation",
    ],
    "sarcomere": [
        "sarcomere", "myosin", "actin", "titin", "troponin",
        "tropomyosin", "myh7", "mybpc3", "tnni3", "tnnt2",
        "tpm1", "cardiac muscle", "hypertrophic cardiomyopathy",
        "hcm", "cardiomyopathy", "sarcomeric", "myofibril",
        "contractile", "cardiac contractile",
    ],
    "ECM_collagen": [
        "extracellular matrix", "ecm", "collagen", "fibronectin",
        "laminin", "integrin", "matrix metalloprotease", "mmp",
        "collagen type", "elastin", "fbn1", "marfan",
        "connective tissue", "basement membrane", "proteoglycan",
        "extracellular", "matrix remodeling",
    ],
    "fibrosis_TGFb": [
        "fibrosis", "fibrotic", "tgf-beta", "tgfb", "tgf-b",
        "transforming growth factor", "smad", "smad3",
        "tgfbr1", "tgfbr2", "connective tissue remodeling",
        "scar tissue", "organ fibrosis", "pulmonary fibrosis",
        "hepatic fibrosis", "cardiac fibrosis", "renal fibrosis",
        "tgf beta", "tgf signaling",
    ],
}

# ---------------------------------------------------------------------------
# GENE_TO_NODES
# Keys: uppercase HGNC gene symbols.
# Values: list of MECH_NODES names (must exist in MECH_NODES).
# Curated; keep small and accurate.
# ---------------------------------------------------------------------------
GENE_TO_NODES: dict[str, list[str]] = {
    # ---- Interferonopathies / innate DNA sensing ----
    "TREX1":    ["cGAS_STING", "DNA_damage"],
    "RNASEH2A": ["cGAS_STING", "DNA_damage"],
    "RNASEH2B": ["cGAS_STING", "DNA_damage"],
    "RNASEH2C": ["cGAS_STING", "DNA_damage"],
    "IFIH1":    ["cGAS_STING", "typeI_interferon"],
    "ADAR":     ["cGAS_STING", "typeI_interferon"],
    "TMEM173":  ["cGAS_STING"],              # STING itself
    "SAMHD1":   ["cGAS_STING", "DNA_damage"],
    "IRF3":     ["typeI_interferon", "cGAS_STING"],
    "IRF7":     ["typeI_interferon"],

    # ---- JAK-STAT / interferon signalling ----
    "STAT1":    ["typeI_interferon", "JAK_STAT"],
    "STAT2":    ["typeI_interferon", "JAK_STAT"],
    "STAT3":    ["JAK_STAT", "TNF_IL6"],
    "STAT6":    ["JAK_STAT"],
    "JAK1":     ["JAK_STAT"],
    "JAK2":     ["JAK_STAT"],
    "TYK2":     ["JAK_STAT", "typeI_interferon"],

    # ---- NFkB ----
    "RELA":     ["NFkB"],
    "NFKB1":    ["NFkB"],
    "NFKB2":    ["NFkB"],
    "IKBKB":    ["NFkB"],
    "IKBKG":    ["NFkB"],

    # ---- TNF / IL-6 axis ----
    "TNF":      ["TNF_IL6", "NFkB"],
    "IL6":      ["TNF_IL6", "JAK_STAT"],
    "IL6R":     ["TNF_IL6", "JAK_STAT"],
    "IL1B":     ["TNF_IL6", "NLRP3_inflammasome"],
    "IL17A":    ["TNF_IL6"],
    "IL23A":    ["TNF_IL6"],

    # ---- NLRP3 / inflammasome ----
    "NLRP3":    ["NLRP3_inflammasome"],
    "CASP1":    ["NLRP3_inflammasome", "apoptosis"],
    "PYCARD":   ["NLRP3_inflammasome"],      # ASC adaptor

    # ---- Complement ----
    "C3":       ["complement"],
    "C5":       ["complement"],
    "CFH":      ["complement"],
    "C1QA":     ["complement"],

    # ---- mTOR pathway ----
    "MTOR":     ["mTOR"],
    "TSC1":     ["mTOR"],
    "TSC2":     ["mTOR"],
    "RPTOR":    ["mTOR"],
    "RICTOR":   ["mTOR"],
    "ULK1":     ["autophagy", "mTOR"],

    # ---- PI3K / AKT ----
    "PIK3CA":   ["PI3K_AKT", "mTOR"],
    "PIK3R1":   ["PI3K_AKT"],
    "AKT1":     ["PI3K_AKT"],
    "AKT2":     ["PI3K_AKT"],
    "PTEN":     ["PI3K_AKT"],

    # ---- MAPK / RAS ----
    "KRAS":     ["MAPK", "PI3K_AKT"],
    "NRAS":     ["MAPK"],
    "HRAS":     ["MAPK"],
    "BRAF":     ["MAPK"],
    "MAP2K1":   ["MAPK"],                   # MEK1
    "MAP2K2":   ["MAPK"],                   # MEK2
    "MAPK1":    ["MAPK"],                   # ERK2
    "MAPK3":    ["MAPK"],                   # ERK1

    # ---- Apoptosis ----
    "BCL2":     ["apoptosis"],
    "BCL2L1":   ["apoptosis"],              # BCL-XL
    "BAX":      ["apoptosis"],
    "BAK1":     ["apoptosis"],
    "TP53":     ["apoptosis", "cell_cycle", "DNA_damage"],
    "CASP3":    ["apoptosis"],
    "CASP9":    ["apoptosis"],

    # ---- Cell cycle ----
    "RB1":      ["cell_cycle"],
    "CDK4":     ["cell_cycle"],
    "CDK6":     ["cell_cycle"],
    "CDKN2A":   ["cell_cycle", "apoptosis"],
    "CDKN1A":   ["cell_cycle"],
    "CCND1":    ["cell_cycle"],
    "E2F1":     ["cell_cycle"],

    # ---- DNA damage response ----
    "ATM":      ["DNA_damage", "apoptosis"],
    "ATR":      ["DNA_damage"],
    "BRCA1":    ["DNA_damage"],
    "BRCA2":    ["DNA_damage"],
    "CHEK1":    ["DNA_damage"],
    "CHEK2":    ["DNA_damage"],
    "PARP1":    ["DNA_damage"],
    "RAD51":    ["DNA_damage"],

    # ---- Autophagy ----
    "ATG5":     ["autophagy"],
    "ATG7":     ["autophagy"],
    "ATG12":    ["autophagy"],
    "BECN1":    ["autophagy"],
    "SQSTM1":   ["autophagy"],
    "MAP1LC3B": ["autophagy"],              # LC3B
    "PINK1":    ["mitochondria", "autophagy"],
    "PARK2":    ["mitochondria", "autophagy"],

    # ---- Lysosome ----
    "GBA":      ["lysosome"],
    "NPC1":     ["lysosome"],
    "LAMP1":    ["lysosome"],
    "LAMP2":    ["lysosome", "autophagy"],
    "CTSD":     ["lysosome"],
    "CTSB":     ["lysosome"],
    "TFEB":     ["lysosome", "autophagy"],

    # ---- Proteasome / ubiquitin ----
    "PSMB8":    ["proteasome_ubiquitin"],
    "PSMB9":    ["proteasome_ubiquitin"],
    "UBB":      ["proteasome_ubiquitin"],
    "UBC":      ["proteasome_ubiquitin"],

    # ---- ER stress / UPR ----
    "XBP1":     ["ER_stress_UPR"],
    "ATF6":     ["ER_stress_UPR"],
    "EIF2AK3":  ["ER_stress_UPR"],          # PERK
    "ERN1":     ["ER_stress_UPR"],           # IRE1α
    "HSPA5":    ["ER_stress_UPR"],           # GRP78 / BiP

    # ---- Mitochondria ----
    "POLG":     ["mitochondria", "DNA_damage"],

    # ---- Oxidative stress ----
    "NFE2L2":   ["oxidative_stress"],       # NRF2
    "SOD1":     ["oxidative_stress"],
    "SOD2":     ["oxidative_stress"],
    "GPX1":     ["oxidative_stress"],

    # ---- Cardio: ion channels ----
    "SCN5A":    ["ion_channels"],
    "KCNQ1":    ["ion_channels"],
    "KCNH2":    ["ion_channels"],            # hERG
    "RYR2":     ["ion_channels", "sarcomere"],
    "CACNA1C":  ["ion_channels"],
    "SCN1A":    ["ion_channels"],

    # ---- Cardio: sarcomere ----
    "MYH7":     ["sarcomere"],
    "MYBPC3":   ["sarcomere"],
    "TNNI3":    ["sarcomere"],
    "TNNT2":    ["sarcomere"],
    "TPM1":     ["sarcomere"],
    "ACTC1":    ["sarcomere"],

    # ---- ECM / collagen ----
    "FBN1":     ["ECM_collagen"],
    "FBN2":     ["ECM_collagen"],
    "COL1A1":   ["ECM_collagen"],
    "COL3A1":   ["ECM_collagen"],
    "COL5A1":   ["ECM_collagen"],
    "ELN":      ["ECM_collagen"],

    # ---- Fibrosis / TGF-β ----
    "TGFB1":    ["fibrosis_TGFb"],
    "TGFBR1":   ["fibrosis_TGFb", "ECM_collagen"],
    "TGFBR2":   ["fibrosis_TGFb", "ECM_collagen"],
    "SMAD2":    ["fibrosis_TGFb"],
    "SMAD3":    ["fibrosis_TGFb"],
    "CCN2":     ["fibrosis_TGFb", "ECM_collagen"],  # CTGF
}
