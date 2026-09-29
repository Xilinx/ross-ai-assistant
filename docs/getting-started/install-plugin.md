<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Install as a plugin

This repository is an [Agent Plugins 1.0](https://agent-plugins.org/specification) package: `plugin.json` at the root, with every skill under `skills/`. A local clone of it is the plugin, and every client below installs from that clone.

Find your client in [step 2](#2-configure-your-client) and follow that one section.

> **Choose one install method.** If you install the plugin, do not also install the skills with `npx skills add` or by copying them into `~/.claude/skills/`. Every skill would then reach the model twice.

The plugin contains skills only. The Vivado MCP Server or the Vivado AI Extension from the [Prerequisites](../../README.md#prerequisites) is still installed separately.

## 1. Clone the repository

```bash
git clone https://github.com/Xilinx/ross-ai-assistant /path/to/ross-ai-assistant
```

The clone is the **plugin root** below. Keep it intact: skills read their own `scripts/` and `reference/` files by relative path. To stay on a specific release, check out its tag, for example `git -C /path/to/ross-ai-assistant checkout <tag>`.

Cursor is the exception: it loads local plugins only from one fixed directory, so clone there instead. See [Cursor](#cursor).

## 2. Configure your client

### Cursor

Cursor has no "install from disk" action. It scans `~/.cursor/plugins/local/`, so cloning there *is* the install.

1. Clone so that `plugin.json` lands **exactly one level** below `local/`:

    ```bash
    git clone https://github.com/Xilinx/ross-ai-assistant ~/.cursor/plugins/local/ross-ai-assistant
    ```

    ```powershell
    git clone https://github.com/Xilinx/ross-ai-assistant "$env:USERPROFILE\.cursor\plugins\local\ross-ai-assistant"
    ```

    The result must be `~/.cursor/plugins/local/ross-ai-assistant/plugin.json`, with no extra level in between.

2. Enable **Cursor Settings → Rules, Skills, Subagents → Include third-party Plugins, Skills, and other configs**. Local plugins do not load without it.

3. Restart Cursor.

Clone or copy, do not link: Cursor rejects a symlink or junction whose target lies outside `local/`. If you also use Claude Code, see [Several clients on one machine](#several-clients-on-one-machine) first.

### VS Code / GitHub Copilot

VS Code reads the clone where it lies.

1. Open **Preferences: Open User Settings (JSON)**.

2. Register the plugin root:

    ```json
    {
      "chat.plugins.enabled": true,
      "chat.pluginLocations": {
        "/path/to/ross-ai-assistant": true
      }
    }
    ```

    On Windows the key is a JSON string, so escape the separators: `"C:\\Users\\you\\ross-ai-assistant": true`.

3. Reload the window.

`chat.plugins.enabled` is required. Without it VS Code ignores plugins and gives no error.

### Claude Code

Claude Code installs only through a *marketplace*, so the repository ships a single-entry one that points at itself:

```shell
/plugin marketplace add /path/to/ross-ai-assistant
/plugin install amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
```

The repeated name is expected: `/plugin install` takes `plugin@marketplace`, and here both are this repository.

Claude Code copies the plugin into `~/.claude/plugins/cache/`, so it survives moving the clone. To try it for one session without installing:

```bash
claude --plugin-dir /path/to/ross-ai-assistant
```

### Codex

Codex also installs through a marketplace, shipped at `.agents/plugins/marketplace.json`:

```bash
codex plugin marketplace add /path/to/ross-ai-assistant
codex plugin add amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
```

Codex copies the plugin into `~/.codex/plugins/cache/`. Browse what is installed with `/plugins`.

### Kiro

Kiro calls Agent Plugins **powers** and reads the same manifest.

1. Open the **Powers** panel → **Add Custom Power**
2. Choose **Import power from a folder**
3. Select the plugin root
4. Click **Install**

Powers activate on context, keyed off the `keywords` in `plugin.json`, such as `vivado`, `vitis-hls`, `chipscope`, `versal` and `timing-closure`. Mentioning one in a prompt brings the skills into play.

### Devin CLI

```bash
devin plugins install --local /path/to/ross-ai-assistant
```

A `--local` install links to the clone, so changes to it apply from the next session. Devin lists the skills as `/amd-ross-agentic-ai-assistant:<skill>`.

### Antigravity

Antigravity uses its own plugin manifest, and its schema does not allow the extra fields in this repository's `plugin.json`. Instead of the clone from step 1, paste this prompt into Antigravity's agent. It clones the repository into Antigravity's plugin directory and writes a minimal manifest:

```text
Install the AMD Ross Agentic AI Assistant skills as an Antigravity plugin.

1. Set DIR to ~/.gemini/config/plugins/amd-ross-agentic-ai-assistant
   (on Windows, %USERPROFILE%\.gemini\config\plugins\amd-ross-agentic-ai-assistant).
2. If DIR does not exist, create its parent directories and clone
   https://github.com/Xilinx/ross-ai-assistant into it. If DIR already exists,
   update it in place instead: git -C DIR fetch origin, then
   git -C DIR reset --hard origin/main.
3. Overwrite DIR/plugin.json so it contains exactly this and nothing else:
   {
     "name": "amd-ross-agentic-ai-assistant",
     "description": "Agent Skills for Developing Products with AMD Embedded Technologies"
   }
   The repository ships a cross-client manifest with extra fields that
   Antigravity's plugin schema does not allow.
4. Check that DIR/plugin.json exists and count the DIR/skills/*/SKILL.md
   files. Report the count. If it is zero, stop and say the install failed.
5. Tell me to reload Antigravity, and that re-running this prompt is how to
   update to a later release.
```

Re-run the prompt whenever you want the latest release. To pin a specific release instead, tell the agent to check out that tag in step 2. If you use the Antigravity CLI rather than the IDE, clone the repository anywhere and run `agy plugin install <path to the clone>` after step 3.

## 3. Verify

| Client | Where the skills appear |
|---|---|
| Cursor | **Customize → Skills**, under "Agent Decides" |
| VS Code | **Chat: Configure Skills**, and the plugin under **Agent Plugins - Installed** |
| Claude Code | `claude plugin list`, or `/plugin` |
| Codex | `/plugins` |
| Kiro | the **Powers** panel |
| Devin CLI | `devin plugins list` |
| Antigravity | **Customizations** → plugins and skills |

Then ask your agent something a skill owns, for example *"check whether this loop nest can be pipelined in HLS"*, or work through the [pre-flight checklist](preflight-checklist.md).

## Several clients on one machine

Each client keeps its own plugin state, with one exception: **Cursor also loads Claude Code's installed plugins**. With both, install once, in Claude Code. A second copy under `~/.cursor/plugins/local/` causes [Every skill appears twice](#every-skill-appears-twice). Cursor's **Plugins** panel then stays empty, but the skills still appear under **Skills**.

Any other combination installs into each client separately, and one clone can serve several of them. Keep every client on the same release.

## Updating to a new release

Pull the clone, or check out the new release tag:

```bash
git -C /path/to/ross-ai-assistant pull
```

Then, per client:

- **Cursor**: pull the clone under `~/.cursor/plugins/local/`, then restart Cursor.
- **VS Code**: reads the clone in place; reload the window.
- **Claude Code** and **Codex**: both copied the plugin into their own cache, so pulling the clone alone changes nothing. Remove the plugin and install it again.
- **Kiro**: re-import the folder.
- **Devin CLI**: start a new session.
- **Antigravity**: re-run the install prompt.

## Troubleshooting

### Cursor: the plugin does not appear

In order of likelihood:

- **Wrong depth.** `plugin.json` must sit exactly one level below `local/`. A clone into a folder you created first yields `local/ross-ai-assistant/ross-ai-assistant/`, which is not found.
- **Linked from outside.** A symlink under `local/` is accepted only when its target resolves inside `local/`.
- **Third-party plugins disabled.** See step 2 of [Cursor](#cursor).
- **Blocked by policy.** On Cursor Teams and Enterprise, **Allow Local Plugin Imports** (Dashboard → Settings → Security & Identity → Marketplace and Plugins) is off by default.

### Every skill appears twice

Two copies are loaded: the plugin and an `npx skills add` install, or a Cursor install alongside a Claude Code one, which Cursor also reads. Remove one.

This matters beyond the clutter. In Claude Code the copy that is not the plugin takes the bare command name, so `/hls-optimize` runs that copy, and the plugin's copy answers only to `/amd-ross-agentic-ai-assistant:hls-optimize`. Because the skills call each other by bare name, the other copy keeps winning even after the plugin updates.

### Skills missing

A skill is skipped without warning if its directory name does not match the `name` in its `SKILL.md` frontmatter. Discovery is also non-recursive: only immediate children of `skills/` count.
