"""Offline workload definitions; safe for the coordinator without game imports.

Membership follows the published descriptions/design archive. These are mixed
synthetic rosters, not a reconstruction of a live shopping history.
"""

SERIES_CHESS_FIXTURES = {
    "hell-heavy": {
        "label": "hell",
        # R241 is not public. The other ten published Hell heroes are covered.
        "members": tuple(f"excel_r{row}" for row in (238, 239, 240, 242, 243, 244, 245, 246, 247, 248)),
        "roster": tuple(f"excel_r{row}" for row in (238, 239, 240, 242, 243, 244, 245, 246, 247, 248))
                  + ("bard", "elite_soldier"),
        "focus": ("excel_r239", "excel_r247", "excel_r246", "excel_r248"),
        "seed": 83,
    },
    "electronic-heavy": {
        "label": "electronic",
        "members": tuple(f"excel_r{row}" for row in (221, 222, 223, 224, 225, 305)),
        "roster": tuple(f"excel_r{row}" for row in (221, 224, 225, 223, 305, 222, 221, 224, 225, 223))
                  + ("bard", "elite_soldier"),
        "focus": ("excel_r221", "excel_r222", "excel_r223", "excel_r305"),
        "seed": 97,
    },
}

SERIES_MINIMUM_NUMERATOR = 3
SERIES_MINIMUM_DENOMINATOR = 4
