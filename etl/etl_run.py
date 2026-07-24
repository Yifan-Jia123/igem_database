"""ETL Runner — execute all ETL steps in dependency order."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import etl_compounds
import etl_enzymes
import etl_reactions
import etl_edges
import etl_master

STEPS = [
    ("1/5 compounds", etl_compounds.run),
    ("2/5 enzymes", etl_enzymes.run),
    ("3/5 reactions", etl_reactions.run),
    ("4/5 edges", etl_edges.run),
    ("5/5 master (sequence + gene + evidence)", etl_master.run),
]

if __name__ == "__main__":
    for label, fn in STEPS:
        print(f"\n[{label}]")
        try:
            fn()
            print(f"[{label}] OK")
        except Exception as e:
            print(f"[{label}] FAILED: {e}")
            sys.exit(1)

    print("\n=== ETL complete ===")
