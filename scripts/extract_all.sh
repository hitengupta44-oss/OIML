#!/usr/bin/env bash
# Re-run the full extraction from the source PDFs.
# Usage: ./extract_all.sh /path/to/'26035 Data'
set -euo pipefail
SRC="${1:?usage: extract_all.sh <source-pdf-dir>}"
OUT="$(dirname "$0")/../extracted/text"
mkdir -p "$OUT"

echo "== native text layers =="
for f in "$SRC"/*.pdf "$SRC"/*.PDF; do
  [ -e "$f" ] || continue
  b=$(basename "$f"); b="${b%.*}"
  pdftotext -layout "$f" "$OUT/$b.txt" 2>/dev/null || true
  n=$(wc -c < "$OUT/$b.txt" 2>/dev/null || echo 0)
  if [ "$n" -lt 100 ]; then rm -f "$OUT/$b.txt"; echo "  scan (needs OCR): $b"; else echo "  ok: $b"; fi
done

echo "== OCR for image-only scans =="
ocr(){
  name="$1"; src="$2"
  tmp=$(mktemp -d)
  pdftoppm -r 200 -gray -png "$src" "$tmp/p"
  : > "$OUT/$name.txt"
  for img in "$tmp"/p-*.png; do
    tesseract "$img" - --psm 6 2>/dev/null >> "$OUT/$name.txt"
    printf '\n\f\n' >> "$OUT/$name.txt"
  done
  rm -rf "$tmp"
  echo "  ocr: $name ($(wc -l < "$OUT/$name.txt") lines)"
}
[ -f "$SRC/gatc_1732710153.pdf" ] && ocr gatc_rules_2013_OCR "$SRC/gatc_1732710153.pdf"
[ -f "$SRC/3_0_0_1732709063.pdf" ] && ocr numeration_rules_2011_OCR "$SRC/3_0_0_1732709063.pdf"
[ -f "$SRC/stdrules-compressed_0_1732708966.pdf" ] && ocr national_standards_rules_2011_OCR "$SRC/stdrules-compressed_0_1732708966.pdf"
[ -f "$SRC/Frequently_Asked_Questions_on_Legal_Metrology_whatsnews.pdf" ] && ocr lm_faq_OCR "$SRC/Frequently_Asked_Questions_on_Legal_Metrology_whatsnews.pdf"

echo "== Indian NAWI schedule =="
python3 "$(dirname "$0")/slice_indian_schedule.py"
echo "done"
