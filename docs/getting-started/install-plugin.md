<!--
Copyright (C) 2026, Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
-->

# Install as a plugin

This repository is an [Agent Plugins 1.0](https://agent-plugins.org/specification) package: `plugin.json` at the root, with every skill under `skills/`. Claude Code and Codex can install directly from the public GitHub repository. ChatGPT supports importing that repository into a workspace. Cursor can import it into your personal marketplace, or load a local clone as described below. Other clients below use a local clone.

Find your client in [step 2](#2-configure-your-client) and follow that section. Clone first only when your client needs a folder. After installation, see [Updating to a new release](#updating-to-a-new-release).

> **Choose one install method.** If you install the plugin, do not also install the skills with `npx skills add` or by copying them into `~/.claude/skills/`. Every skill would then reach the model twice.

The plugin contains skills only. The Vivado MCP Server or the Vivado AI Extension from the [Prerequisites](../../README.md#prerequisites) is still installed separately.

## 1. Choose your install source

For **Claude Code**, **Codex**, **ChatGPT workspace import**, or a **Cursor personal GitHub import**, skip cloning and go directly to your client below.

For clients that require a folder, clone the public repository:

```bash
git clone https://github.com/Xilinx/ross-ai-assistant /path/to/ross-ai-assistant
```

The clone is the **plugin root** below. Keep it intact: skills read their own `scripts/` and `reference/` files by relative path. To stay on a specific release, check out its tag, for example `git -C /path/to/ross-ai-assistant checkout <tag>`.

For Cursor local installs, use its fixed local-plugin directory instead of an arbitrary clone location. See [Cursor](#cursor).

## 2. Configure your client

### Claude Code

Add the public GitHub repository as a marketplace, then install its plugin. No manual clone is needed. Run these commands inside Claude Code:

```shell
/plugin marketplace add Xilinx/ross-ai-assistant
/plugin install amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
```

The first command takes the GitHub `owner/repo`. The second takes `plugin@marketplace`; both names are `amd-ross-agentic-ai-assistant` in the shipped marketplace manifest. Choose the installation scope when prompted.

Claude Code manages its own download in `~/.claude/plugins/cache/`. For local development only, clone the repository as described above and try it for one session without installing:

```bash
claude --plugin-dir /path/to/ross-ai-assistant
```

### Cursor

#### Personal marketplace

1. In Cursor, open **Customize** and choose **From GitHub Repository**.
2. Enter `https://github.com/Xilinx/ross-ai-assistant` and complete the import. No manual clone is needed.
3. Find **AMD Ross Agentic AI Assistant** in **Customize** and install it at project or user scope if it is not already installed.
4. Check **Dashboard → Plugins & MCPs → Personal**: the plugin should appear in the list, with `https://github.com/Xilinx/ross-ai-assistant` under **Your Marketplaces**.

The repository includes the Cursor marketplace manifest this import requires. See [Cursor's repository import instructions](https://cursor.com/docs/skills#installing-skills-from-a-repository).

#### Local folder

Cursor supports **Import from Disk** in **Customize** when local imports are allowed. The local-clone route remains available: Cursor scans `~/.cursor/plugins/local/`, so cloning there installs the plugin locally.

1. Clone so that `plugin.json` lands **exactly one level** below `local/`:

    ```bash
    git clone https://github.com/Xilinx/ross-ai-assistant ~/.cursor/plugins/local/ross-ai-assistant
    ```

    ```powershell
    git clone https://github.com/Xilinx/ross-ai-assistant "$env:USERPROFILE\.cursor\plugins\local\ross-ai-assistant"
    ```

    The result must be `~/.cursor/plugins/local/ross-ai-assistant/plugin.json`, with no extra level in between.

2. Allow local plugin imports. On Enterprise, an admin enables **Dashboard → Settings → Security & Identity → Marketplace and Plugins → Allow Local Plugin Imports**; it is off by default and also controls **Import from Disk**. If your Cursor version shows **Cursor Settings → Rules, Skills, Subagents → Include third-party Plugins, Skills, and other configs**, enable that setting too.

3. Restart Cursor or run **Developer: Reload Window**, then inspect the plugin in **Customize**.

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

### Codex

Add the public GitHub repository as a marketplace, then install the plugin. No manual clone is needed. Run these commands in your terminal:

```bash
codex plugin marketplace add Xilinx/ross-ai-assistant
codex plugin add amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
```

The install command uses the plugin and marketplace names from this repository. Keep that selector unchanged when using the GitHub source.

Codex installs a copy into `~/.codex/plugins/cache/`. Run `codex plugin list` to confirm installation, then start a new chat. In the desktop app, use the Plugins directory to review installed plugins.

### ChatGPT

To import the GitHub marketplace into a ChatGPT workspace:

1. Open **Admin → Plugins → Add → Import marketplace**.
2. Set **Source** to `https://github.com/Xilinx/ross-ai-assistant`.
3. Leave **Path** empty: the marketplace is at the repository root. Do not enter `.agents/plugins/marketplace.json` in this field.
4. Leave **Branch, tag, or commit** empty to follow the default branch, or select a release tag or commit to pin a version.
5. Import the marketplace, authorize GitHub access when prompted, and review the import results and plugin access policy.

Then open the **Plugins** directory, select your workspace, and install **AMD Ross Agentic AI Assistant**. Start a new chat after installation. No manual clone or Codex CLI command is required for this workspace import.

A public GitHub repository does not automatically appear in ChatGPT's public Plugins directory. The plugin supplies skills, and access to local tools and the separately configured MCP servers still depends on the ChatGPT environment.

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

Antigravity uses its own plugin manifest, and its schema does not allow the extra fields in this repository's `plugin.json`. Instead of cloning manually, paste this prompt into Antigravity's agent. It clones the repository into Antigravity's plugin directory and writes a minimal manifest:

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
| Cursor | **Customize** for the installed plugin; **Customize → Skills**, under "Agent Decides", for skills |
| VS Code | **Chat: Configure Skills**, and the plugin under **Agent Plugins - Installed** |
| Claude Code | `claude plugin list`, or `/plugin` |
| Codex | `codex plugin list`, or the desktop Plugins directory |
| ChatGPT | the **Installed** row in the Plugins directory |
| Kiro | the **Powers** panel |
| Devin CLI | `devin plugins list` |
| Antigravity | **Customizations** → plugins and skills |

Then ask your agent something a skill owns, for example *"check whether this loop nest can be pipelined in HLS"*, or work through the [pre-flight checklist](preflight-checklist.md).

## Several clients on one machine

Each client keeps its own plugin state, with one exception: **Cursor also loads Claude Code's installed plugins**. With both, install once, in Claude Code. A second copy under `~/.cursor/plugins/local/` causes [Every skill appears twice](#every-skill-appears-twice). Cursor's **Plugins** panel then stays empty, but the skills still appear under **Skills**.

For clients that read folders, one clone can serve several clients. GitHub marketplace installs manage their own downloads. Avoid adding a second copy of a plugin already supplied by your workspace or extension, and keep clients on the same release when comparing results.

## Updating to a new release

Use the update steps for your install source. Updating a separate clone does not update a cached marketplace installation.

### Cursor updates

A personal GitHub import has no documented update control. **Dashboard → Plugins & MCPs → Personal** lists the repository under **Your Marketplaces**.

To move to a chosen release, use the local folder:

1. Uninstall **AMD Ross Agentic AI Assistant** from **Customize** when it was installed from the personal marketplace. A marketplace install with the same name takes precedence over `~/.cursor/plugins/local`.
2. Follow [Clients installed from a folder](#clients-installed-from-a-folder): update `~/.cursor/plugins/local/ross-ai-assistant`, then restart Cursor.

If Cursor is loading the Claude Code installation, follow [Claude Code updates](#claude-code-updates).

### Claude Code updates

Run these commands in your terminal to refresh the catalog and update the installed plugin:

```bash
claude plugin marketplace update amd-ross-agentic-ai-assistant
claude plugin update amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
claude plugin list
```

If you installed at project or local scope, pass the matching `--scope project` or `--scope local` to `claude plugin update`. Start a new Claude Code session to load the update, or use `/reload-plugins` when prompted.

You can also open `/plugin`, select the plugin on **Installed**, and choose **Update now**. To receive future updates automatically, select this marketplace on **Marketplaces** and enable **auto-update**; third-party marketplaces have it off by default.

If the marketplace was originally registered from a local folder, run `git pull --ff-only` in that folder first, then refresh and update as above. To switch to GitHub, repeat the marketplace-add command in [Claude Code](#claude-code), then check `/plugin marketplace list` to confirm the source before updating.

### Codex updates

Refresh the Git marketplace snapshot:

```bash
codex plugin marketplace upgrade amd-ross-agentic-ai-assistant
```

Marketplace refresh and plugin installation are separate operations. To explicitly replace the installed cached copy with the refreshed version, close chats using the plugin and run:

```bash
codex plugin remove amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
codex plugin add amd-ross-agentic-ai-assistant@amd-ross-agentic-ai-assistant
codex plugin list
```

Start a new chat and verify a skill works. This remove/add sequence temporarily uninstalls the plugin; it does not remove your designs or projects. If installation fails, resolve the reported error and rerun `codex plugin add`.

For a marketplace originally registered from a local folder, pull that folder first and then use the remove/add sequence. To switch to GitHub, repeat the marketplace-add command in [Codex](#codex), check `codex plugin marketplace list`, and then refresh and reinstall.

### ChatGPT workspace updates

GitHub marketplace imports sync daily. To request an update sooner, open **Admin → Plugins → Marketplaces**, select this marketplace, and choose **Sync now**. Review the sync report for plugin errors; a completed sync can still contain failures. Start a new chat after the update.

A marketplace pinned to a tag or commit stays on that version. To move to a newer release, import the marketplace with the desired **Branch, tag, or commit** and review the results. Do not delete the existing marketplace as an update step: deleting it also deletes the plugins imported from it.

### Clients installed from a folder

Pull the clone used by your client:

```bash
git -C /path/to/ross-ai-assistant pull --ff-only
```

If you checked out a release tag, fetch tags and check out the desired new tag instead of pulling a detached checkout:

```bash
git -C /path/to/ross-ai-assistant fetch --tags origin
git -C /path/to/ross-ai-assistant checkout <tag>
```

Then reload the installed skills in your client:

- **Cursor**: update the clone under `~/.cursor/plugins/local/ross-ai-assistant`, then restart Cursor. If Cursor reads your Claude Code installation, use the Claude Code update steps instead.
- **VS Code / GitHub Copilot**: reload the window after updating the configured clone.
- **Kiro**: re-import the updated folder in the Powers panel.
- **Devin CLI**: start a new session after updating the linked clone.
- **Antigravity**: re-run its install prompt to update its managed clone and restore the minimal manifest, then reload Antigravity.

After any update, repeat [Verify](#3-verify) and confirm that each skill appears once.

## Client documentation

- [Cursor personal import](https://cursor.com/docs/skills#installing-skills-from-a-repository)
- [Cursor local plugins](https://cursor.com/docs/plugins#test-plugins-locally)
- [Claude Code installation and updates](https://code.claude.com/docs/en/discover-plugins)
- [Codex plugin and marketplace commands](https://learn.chatgpt.com/docs/developer-commands)
- [ChatGPT GitHub marketplace import and sync](https://learn.chatgpt.com/docs/enterprise/plugin-management)

## Troubleshooting

### Cursor: the plugin does not appear

In order of likelihood:

- **Wrong depth.** `plugin.json` must sit exactly one level below `local/`. A clone into a folder you created first yields `local/ross-ai-assistant/ross-ai-assistant/`, which is not found.
- **Linked from outside.** A symlink under `local/` is accepted only when its target resolves inside `local/`.
- **Local imports or third-party configs disabled.** See step 2 under [Cursor local folder](#local-folder).
- **Blocked by policy.** On Enterprise, **Allow Local Plugin Imports** (Dashboard → Settings → Security & Identity → Marketplace and Plugins) is off by default. Ask your admin to enable it for the local-folder or Import from Disk route.

### Every skill appears twice

Two copies are loaded: the plugin and an `npx skills add` install, or a Cursor install alongside a Claude Code one, which Cursor also reads. Remove one.

This matters beyond the clutter. In Claude Code the copy that is not the plugin takes the bare command name, so `/hls-optimize` runs that copy, and the plugin's copy answers only to `/amd-ross-agentic-ai-assistant:hls-optimize`. Because the skills call each other by bare name, the other copy keeps winning even after the plugin updates.

### Skills missing

A skill is skipped without warning if its directory name does not match the `name` in its `SKILL.md` frontmatter. Discovery is also non-recursive: only immediate children of `skills/` count.
