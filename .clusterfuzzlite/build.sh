#!/bin/bash -eu

pip3 install --ignore-requires-python --no-deps .
pip3 install pydantic pyyaml

for fuzzer in $(find "$SRC/agy-missioncontrol/fuzz" -name '*_fuzzer.py'); do
  fuzzer_basename=$(basename -s .py "$fuzzer")
  fuzzer_package="${fuzzer_basename}.pkg"

  pyinstaller --distpath "$OUT" --onefile --name "$fuzzer_package" \
    --paths "$SRC/agy-missioncontrol/plugin/scripts" \
    "$fuzzer"

  echo "#!/bin/sh
# LLVMFuzzerTestOneInput for fuzzer detection.
this_dir=\$(dirname \"\$0\")
\$this_dir/$fuzzer_package \"\$@\"" > "$OUT/$fuzzer_basename"
  chmod +x "$OUT/$fuzzer_basename"
done
