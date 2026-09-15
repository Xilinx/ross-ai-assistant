<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# init-component

Initialize the HLS component (ensure `vitis-comp.json` exists) and the git repository (if needed), then commit source/config files as baseline.

## Check the vitis-comp.json file exists

Check the `vitis-comp.json` file in the component directory. If it does not exist, you must create one before running HLS flow. You must use the following format to create the component folder and the `vitis-comp.json` file:

```
## folder structure:
hls_component/
├── vitis-comp.json

{
  "name": "hls_component",
  "type": "HLS",
  "configuration": {
    "componentType": "HLS",
    "configFiles": [
      "<hls_config.cfg relative path>"
    ]
  }
}
```

## Init-git: How to run

Run the following commands in the root directory of your HLS project, don`t change the command, just copy and paste it to terminal and execute.
```bash
# Step 1: Initialize git if needed
if [ ! -d ".git" ]; then
    git init 2>/dev/null
    git config user.email "vitis-ide@amd.com"
    git config user.name "HLS_optimizer"
    echo "Git repository initialized."
fi

# Step 2: Create or update .gitignore
HLS_PATTERNS="_ide/
**/.cache/
**/hls/
**/reports/
**/logs/
*summary"

if [ ! -f ".gitignore" ]; then
    echo "$HLS_PATTERNS" > .gitignore
    echo ".gitignore created."
else
    # Append missing patterns
    while IFS= read -r pattern; do
        if ! grep -qxF "$pattern" .gitignore; then
            echo "$pattern" >> .gitignore
        fi
    done <<< "$HLS_PATTERNS"
    echo ".gitignore updated with HLS patterns."
fi

# Step 3: Add files and commit if no commits yet
if ! git rev-parse HEAD >/dev/null 2>&1; then
    # Find and add C/C++ source files (excluding hls directories)
    find . -type d -name "hls" -prune -o \( -name "*.cpp" -o -name "*.hpp" -o -name "*.h" -o -name "*.c" \) -print | xargs -r git add
    # Find and add config files (excluding hls directories)
    find . -type d -name "hls" -prune -o \( -name "*.cfg" -o -name "*.tcl" \) -print | xargs -r git add
    # Add .gitignore
    git add .gitignore 2>/dev/null
    git commit -m "Initial commit: baseline HLS kernel"
    echo "Initial commit created."
else
    echo "Git repository already has commits."
    git log --oneline -3
fi
```

## Verify

```bash
git status && git log --oneline -3
```
