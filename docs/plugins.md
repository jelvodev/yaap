# Writing a plugin

A plugin is your own Python package in `data/plugins/<name>/`. When it is switched on in **Settings › System › Plugins**,
YAAP imports it at start and calls its `register(yaap)` function, which adds tools for the agent, model providers
and kinds of keys for the vault through YAAP's own registries. A plugin can also bring a page of plain HTML, shown in
Settings in a sandboxed frame, that calls YAAP's API through the plugin SDK.

There is no plugin hub and no install from a link: YAAP runs only code you put in the data folder yourself.

> **A plugin runs with YAAP's rights.** It runs inside YAAP's process: it can read and change everything in the data
> folder, use the keys in the vault, run commands in the container and reach the network. Switch on only code you
> wrote or read yourself.

## A minimal plugin

```
data/plugins/hello/
├── plugin.json
├── __init__.py
└── page/
    ├── index.html
    └── app.js
```

`plugin.json`, the manifest. YAAP reads it without importing anything, to list the plugin in Settings:

```json
{
  "name": "hello",
  "version": "1.0.0",
  "description": "Greets people, and shows a small page.",
  "registers": { "tools": ["hello_greet"] },
  "page": "page/index.html"
}
```

`__init__.py`, the package:

```python
"""A minimal YAAP plugin: one tool and a page."""

from app.plugins.interface import STRING, PluginContext, Tool, ToolError, schema


async def greet(run, arguments):
    name = str(arguments.get("name") or "").strip()
    if len(name) > 100:
        raise ToolError("The name is longer than 100 characters.")
    return f"Hello, {name or 'world'}!"


def register(yaap: PluginContext) -> None:
    yaap.register_tool(
        Tool(
            "hello_greet",
            "Greet someone by name. Returns the greeting as text.",
            schema({"name": STRING}, []),
            "auto",
            greet,
            scope="always",
            read_only=True,
        )
    )
```

`page/index.html` and `page/app.js`:

```html
<!doctype html>
<html lang="en">
  <head><meta charset="utf-8"><title>Hello</title></head>
  <body>
    <h1>Hello plugin</h1>
    <p id="status">The script has not run.</p>
    <script src="app.js"></script>
  </body>
</html>
```

```js
document.getElementById("status").textContent = "The script ran at " + new Date().toLocaleTimeString() + ".";
```

Then open **Settings › System › Plugins** (Global), choose **Switch on…** for `hello`, and **Restart YAAP now**. The agent now
has `hello_greet` in every conversation, and **Open page** shows the page.

## The manifest

| Field | Meaning |
|---|---|
| `name` | The folder's name: lowercase letters, digits and `_`, starting with a letter, at most 40 characters |
| `version` | Any text, such as `1.0.0`; shown in Settings |
| `description` | One or two sentences, shown in Settings |
| `registers` | Lists of what `register()` adds: `tools` (tool names), `providers` (provider names), `credential_types` (kinds). Optional; empty lists by default |
| `page` | Optional: the page's entry file, inside a subfolder of the plugin that is not hidden, such as `page/index.html`. Hidden files in the page's folder are not served |
| `api` | Optional, with a page: the API routes the page may call, such as `"GET /api/v1/environments"` (see [The page](#the-page)) |

The lists must match what `register()` does: registering something that is not listed, or listing something that is
not registered, fails the plugin. So what Settings shows before you switch a plugin on is what it adds.

## What `register(yaap)` can do

`register` is a plain (not async) function. It receives a `PluginContext` with:

| | |
|---|---|
| `yaap.register_tool(tool)` | A `Tool` for the agent (below) |
| `yaap.register_provider(spec, models)` | A `ProviderSpec` and its `ModelInfo` list. The provider's name must be new; its models appear in every model list, grouped under the spec's `display_name` |
| `yaap.register_credential_type(credential_type)` | A `CredentialType`: a kind of key in the vault, tested with its `test` before it is stored, encrypted with the master key |
| `yaap.name`, `yaap.version`, `yaap.folder` | The plugin's name, version and folder |
| `yaap.logger` | A logger; its lines go to YAAP's log (`docker compose logs yaap`) |

Import everything from `app.plugins.interface`, the module YAAP keeps stable for plugins: `Tool`, `ToolError`,
`ToolOutput`, `schema`, `STRING`, `text_argument`, `truncate`, `AgentRun`, `ProviderSpec`, `ModelInfo`,
`ModelPricing`, `PriceTier`, `ModelProvider`, `AgentRequest`, `TextDelta`, `ReasoningDelta`, `Completed`,
`TokenUsage`, `ProviderError`, `CredentialType`, `CredentialTestResult`, `PluginContext`, `AgentEvent`. Other modules under `app`
may change in any version.

A plugin can use the Python packages installed in YAAP's image and its own modules (relative imports work: the
package is imported as `yaap_plugins.<name>`). There is no way to install extra packages for a plugin.

### Tools

`Tool(name, description, input_schema, permission, handler, ...)`:

- `name`: at most 64 letters, digits, `_` and `-`, starting with a letter, without `__` (kept for MCP servers), and not a name
  YAAP or another plugin already uses. Prefixing with the plugin's name (`hello_greet`) avoids clashes.
- `description`: what it does and what it returns; the first sentence is shown in the Blueprint's tool lists.
- `input_schema`: a JSON Schema object; `schema(properties, required)` makes one.
- `permission`: `auto` (runs without asking), `ask` (waits for approval in the chat) or `conditional` (asks when
  `needs_approval(run, arguments)` says so). Give anything that changes things outside YAAP `ask`.
- `handler`: `async def handler(run, arguments) -> str`. Raise `ToolError("…")` for a failure the model can act on;
  return `ToolOutput.with_images(text, [(media_type, base64)])` to return images.
- `scope`: `project` (default: only in project conversations), `always` (every conversation), `attachments` (conversations with attachments), `project_attachments` (project conversations with attachments).
- `read_only=True` for tools that only read; `describe=lambda arguments: "…"` for the line on the approval card.

A plugin tool is offered like YAAP's own tools: to every conversation it fits, unless an agent definition lists its
own `tools` (then it must name the plugin tool). Text a tool fetches from outside (a web page, an e-mail) is best
passed through YAAP's outside-content check by the tools that already do that; a plugin tool's output is trusted.

## The page

A plugin's page is plain HTML, CSS and ES modules in the page's folder, loaded by the browser as they are: no React,
no npm, no build. The `page` entry file and everything in its folder are served while the plugin is loaded, at an
address with a token that changes at every start (`page_url` in `GET /api/v1/plugins`). Settings shows the page in
a frame that is sandboxed without `allow-same-origin`, so the page runs on an opaque origin: no cookies, no storage,
no access to YAAP's own page.

To talk to YAAP, the page imports the plugin SDK that YAAP serves at `/plugin-sdk.js`, and lists in `plugin.json`
the API routes it calls. The page never calls YAAP's API itself and holds no token: the SDK hands each call by
`postMessage` to YAAP's page around the frame, which makes it with your session only when the manifest lists the
route, and YAAP's server holds the call to the manifest once more. Every other route is refused with 403, whatever
the page's script does. So the manifest, not the SDK, is the boundary.

### An example page

Add the routes to `plugin.json`, written as in YAAP's OpenAPI description (`/docs` on a running YAAP, or the
declarations in `/plugin-sdk.d.ts`):

```json
{
  "name": "projects",
  "version": "1.0.0",
  "description": "Shows the projects of the active environment.",
  "page": "page/index.html",
  "api": ["GET /api/v1/environments/{environment_id}/projects"]
}
```

`page/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>Projects</title>
    <link rel="stylesheet" href="style.css">
  </head>
  <body>
    <h1>Projects</h1>
    <p id="where">Connecting to YAAP…</p>
    <ul id="projects"></ul>
    <script type="module" src="app.js"></script>
  </body>
</html>
```

`page/style.css`, using YAAP's theme tokens, which the SDK puts on the page:

```css
body { font-family: var(--font, sans-serif); color: var(--text); background: var(--bg); margin: 16px; }
h1 { color: var(--accent); font-size: 1.2rem; }
```

`page/app.js`:

```js
import { connect } from "/plugin-sdk.js";

const yaap = await connect();

async function show() {
  const { environment, project } = yaap;
  document.getElementById("where").textContent = `${environment.name}${project ? ` · ${project.name}` : ""}`;
  const { data, error } = await yaap.api.GET("/api/v1/environments/{environment_id}/projects", {
    params: { path: { environment_id: environment.id } },
  });
  const list = document.getElementById("projects");
  if (error) {
    list.textContent = `YAAP refused: ${error.detail}`;
    return;
  }
  list.replaceChildren(...data.map((item) => Object.assign(document.createElement("li"), { textContent: item.name })));
}

yaap.on("context", show);
await show();
```

Switch the plugin on, restart YAAP, and **Open page**. A call to a route the manifest does not list, such as
`yaap.api.GET("/api/v1/settings")`, comes back with status 403 and `error.detail` "The plugin projects does not list
GET /api/v1/settings under api in its plugin.json."

### The SDK

`/plugin-sdk.js` is one ES module, generated from YAAP's OpenAPI description by every build of YAAP's frontend, so
it matches the API of the YAAP that serves it. `/plugin-sdk.d.ts` next to it holds its TypeScript declarations, with
the types of every route; a plugin written in TypeScript downloads that file and compiles its own JavaScript
(`import { connect } from "/plugin-sdk.js"` stays as it is in the output).

| | |
|---|---|
| `await connect(options?)` | Connects to the YAAP page that shows this one and returns the connection. Rejects after `timeoutMs` (10 s) when the page is not shown by YAAP, for example when its address is opened on its own. `applyTheme: false` leaves the theme tokens off the page |
| `yaap.api.GET(path, init?)`, `POST`, `PUT`, `PATCH`, `DELETE` | Asks YAAP to call its API for the page. `path` is the route as the OpenAPI description writes it; `init` holds `params: { path, query }`, a JSON `body` and a `signal`. Returns `{ data, status }` or `{ error, status }`, where `error` is YAAP's `{ detail }`. A path that is not a route of YAAP's API, or a missing path parameter, rejects at once, before anything is sent; a call rejects when YAAP does not answer within `callTimeoutMs` (30 s) or the signal aborts it |
| `yaap.environment`, `yaap.project` | The environment active in YAAP (`id`, `name`, `colour`) and the project the chat shows in it (`id`, `name`), or `null` outside a project |
| `yaap.theme` | `scheme` (`dark` or `light`) and `tokens`: YAAP's colours and fonts as CSS custom properties (`--bg`, `--text`, `--accent`, `--font` and more), also put on the page's root element |
| `yaap.on("context", handler)` | The active environment or project changed; the handler gets `{ environment, project }`. Returns a function that stops following |
| `yaap.on("theme", handler)` | The user switched between dark and light, or the accent changed with the environment |
| `yaap.plugin`, `yaap.routes` | The plugin's name and the routes its manifest lists |
| `yaap.apiVersion` | The version of YAAP's API the SDK was generated from |
| `createClient({ timeoutMs })`, `applyTheme(theme)` | The client and the theme helper on their own |

How the page talks to YAAP: `connect()` sends a hello to the YAAP page around the frame with `postMessage`, and
YAAP answers that frame, and only that frame, with the plugin's routes, the active environment and project, and the
theme; changes follow the same way. Each API call is a message with an id; YAAP checks it against the manifest, makes
it with the session (and its CSRF token), naming the plugin in the `X-YAAP-Plugin` header, and posts the answer back
under the same id. YAAP recognises the frame by its window, not by an origin, as the frame's origin is opaque.

### What the page may do

The page's Content-Security-Policy allows:

- scripts, styles, images, fonts and media inline or from the page's own folder, plus the SDK at `/plugin-sdk.js`;
  no outside addresses;
- `fetch` to files in the page's own folder only; YAAP's API is reached through the SDK;
- no forms, no frames, no storage (the page runs on an opaque origin).

The routes a manifest may list are YAAP's API routes under `/api/v1/`, with `{name}` for a path parameter, which
matches exactly one path segment; a manifest lists at most 200 routes. The account, sessions and API tokens (`/api/v1/auth/…`)
and the plugin routes (`/api/v1/plugins…`) are never open to a page: a manifest that lists one is refused, so the
plugin cannot be switched on. Everything else follows from what you list: a page
that lists `PUT /api/v1/settings/{key}` can change settings, so list what the page needs and nothing more.

The frame runs on an opaque origin, so the browser sends `Origin: null` when it loads the page's ES modules and the
SDK. YAAP accepts `Origin: null` for exactly that, a `GET` or `HEAD` of `/plugin-sdk.js` or of a file under the page's address,
and refuses it everywhere else, its API included.

## When a plugin fails

A plugin that raises an error while it is imported or in `register()` (a missing package, a typo, a name that
clashes, a mismatch with the manifest) is **switched off**, and Settings shows the error with the file and line it
came from. YAAP starts without it; the full traceback is in YAAP's log. Fix the code and switch it on again.

If YAAP stops while loading a plugin (the import hangs and YAAP is restarted, or the process dies), the next start
switches that plugin off too. Deleting the plugin's folder also works: a switched-on plugin whose folder is gone is
switched off at the next start.

## Changing a plugin

Python cannot replace an imported package safely in a running process, so every change takes effect at the next
start: switching on or off, and editing the code of a switched-on plugin. While a switch on or off waits, **Restart
YAAP now** in Settings restarts the container (Docker starts it again); replies in progress stop. The button does not
show for an edit of the code: restart with `docker compose restart yaap`.

`data/plugins` is part of backups, and the on/off switches are in the database.
