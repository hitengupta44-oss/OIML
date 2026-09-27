"""Slice the Seventh Schedule Heading A (NAWI specification) out of the
Legal Metrology (General) Rules, 2011 text edition."""
import os
HERE = os.path.dirname(__file__)
SRC = os.path.join(HERE, "..", "extracted", "text", "The_Legal_Metrology_General_Rules_2011.txt")
OUT = os.path.join(HERE, "..", "extracted", "IN_7th_schedule_A_NAWI.txt")

lines = open(SRC, encoding="utf-8", errors="ignore").read().splitlines()
start = next(i for i, l in enumerate(lines)
             if "Specification for Non-Automatic Weighing Instruments" in l
             and "[See Rule 13]" in l)
end = next((i for i, l in enumerate(lines[start + 50:], start + 50)
            if "Eighth Schedule" in l), len(lines))
open(OUT, "w", encoding="utf-8").write("\n".join(lines[start:end]))
print(f"wrote {end - start} lines to {OUT} (source lines {start}-{end})")
