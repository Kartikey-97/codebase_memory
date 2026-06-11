#!/bin/bash
OUTPUT="codebase_dump.txt"
> "$OUTPUT"
for ext in "py" "js" "jsx" "ts" "tsx" "css" "json" "md" "yaml" "env.example"; do
  find . -type d \( -name node_modules -o -name .venv -o -name dist -o -name .git -o -name __pycache__ \) -prune -o -type f -name "*.$ext" -print | while read -r file; do
    echo "--- $file ---" >> "$OUTPUT"
    cat "$file" >> "$OUTPUT"
    echo -e "\n" >> "$OUTPUT"
  done
done
