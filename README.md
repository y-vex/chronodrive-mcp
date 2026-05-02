# chronodrive-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes [Chronodrive](https://www.chronodrive.com) grocery cart management as tools for any MCP-compatible LLM.

Search products, add them to your cart, and reset it — all from a conversation with your AI assistant.

## Tools

| Tool | Description |
|---|---|
| `search` | Free-text product search. Returns ranked candidates with price, stock, flags, promo, and more. |
| `add_to_cart` | Add a product to the cart by `productId` and quantity. |
| `remove_from_cart` | Remove a specific product from the cart by `productId`. |
| `get_cart` | Return current cart contents — products, quantities, and total. |
| `reset_cart` | Empty the entire cart. |
| `auth` | Force token renewal (called automatically on 401). |

### `search` response fields

Each candidate includes:

- `name`, `brand`, `size`, `productId`
- `price` — unit price
- `price_per_kg` — price per kg or litre
- `lowest_30d` — lowest price over the past 30 days
- `promo` — current promotion label if any
- `stock` — `HIGH_STOCK`, `LOW_STOCK`, etc.
- `flags` — `fresh`, `organic`, `french`, `local`, `new`
- `origin` — country of origin
- `dims` — packaging dimensions (weight, height, length, width)
- `complementary` — IDs of suggested complementary products
- `substitutions` — IDs of substitution products
- `units_to_add` — how many packs to reach the requested quantity
- `score` — relevance score (word overlap minus noise penalty)

Frozen products, out-of-stock items, and processed/ready-made products (via a keyword blacklist) are filtered out automatically.

## Use case: meal-based grocery automation

The primary use case is letting your AI assistant turn a weekly meal plan into a filled cart automatically.

Give it your recipes (as a file, a list, or in conversation) and ask it to fill your Chronodrive cart. It will:

1. Extract each ingredient with its quantity and unit from the recipes
2. Call `search` for each one, picking the best raw product (fresh, in stock, right size)
3. Present the selection for your review — you can swap any item before it goes in
4. Call `add_to_cart` for each validated product

Example prompt:
```
Here are my 3 meals for the week: [pasta bolognese for 4, chicken stir-fry for 4, lentil soup for 4].
Consolidate the ingredients, search for each one on Chronodrive, and add them to my cart.
```

The assistant handles unit consolidation (e.g. garlic needed in two recipes summed into one search), pack size calculation (how many 500 g bags to cover 800 g), and filtering out processed or frozen variants that don't match a raw ingredient.

## Setup

**Requirements:** Python 3.11+, a Chronodrive account, your store ID.

```bash
git clone https://github.com/y-vex/chronodrive-mcp
cd chronodrive-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in your credentials
```

Your store ID appears in the URL when you log in: `chronodrive.com/store/XXXX`.

## Client configuration

This server works with any MCP-compatible client: Claude Code, Claude Desktop, Cursor, Windsurf, Zed, ChatGPT, or any custom agent built with the MCP SDK.

Each client has its own way to register an MCP server. The common parameters are always:

- **command** — path to the Python interpreter in your venv
- **args** — path to `server.py`
- **env** — your three credentials (`CHRONODRIVE_EMAIL`, `CHRONODRIVE_PASSWORD`, `CHRONODRIVE_STORE_ID`)

**Claude Code** — create a `.mcp.json` file in your project directory (keep it out of git):

```json
{
  "mcpServers": {
    "chronodrive": {
      "command": "/absolute/path/to/chronodrive-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/chronodrive-mcp/server.py"],
      "env": {
        "CHRONODRIVE_EMAIL": "your@email.com",
        "CHRONODRIVE_PASSWORD": "yourpassword",
        "CHRONODRIVE_STORE_ID": "1234"
      }
    }
  }
}
```

**Claude Desktop** — add the same block under `mcpServers` in `~/Library/Application Support/Claude/claude_desktop_config.json`.

**Cursor / Windsurf / Zed** — refer to each editor's MCP documentation; the server command and env vars are the same.

## Session caching

The bearer token is cached at `~/.chronodrive-mcp/session.json` and reused across calls. A 401 response triggers automatic re-authentication. Override the path with `CHRONODRIVE_SESSION_FILE`.

## Legal

This project uses Chronodrive's public frontend API (the same endpoints the website uses). It does not scrape HTML or bypass any access control. Use it for your own account only. Refer to Chronodrive's [CGU](https://www.chronodrive.com) for their terms of service.
