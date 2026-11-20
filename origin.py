"""
Origin label: tells you which repo made a page or a csv file.

This repo (positions) uses "v.5.5/Positions". The markets repo has its own copy of this file
with "markets/markets". Change the text here only.
"""

ORIGIN = "v.5.5/Positions"


def with_origin(df):
    """Return the table with an 'origin' column as the first column. Adds it only if it is missing."""
    if "origin" in df.columns:
        return df
    df = df.copy()
    df.insert(0, "origin", ORIGIN)
    return df
