"""
Chronodrive MCP server — exposes cart management as Claude tools.
"""
import asyncio, json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

import client as cd

app = Server("chronodrive")


# ── Tool definitions ───────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search",
            description=(
                "Recherche des produits Chronodrive par mot-clé libre. "
                "Retourne les meilleurs candidats avec nom, marque, taille, prix, score."
            ),
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query":    {"type": "string",  "description": "Terme de recherche libre, ex: 'tomates cerises', 'lait demi-écrémé'"},
                    "quantity": {"type": "number",  "description": "Quantité souhaitée (défaut: 1)"},
                    "unit":     {"type": "string",  "description": "Unité: 'g', 'ml', ou 'pcs' (défaut: 'pcs')"},
                    "top_n":    {"type": "integer", "description": "Nombre de résultats à retourner (défaut: 5)"},
                },
            },
        ),
        Tool(
            name="add_to_cart",
            description=(
                "Ajoute un produit au panier Chronodrive. "
                "Utiliser le productId retourné par 'search'."
            ),
            inputSchema={
                "type": "object",
                "required": ["product_id", "quantity"],
                "properties": {
                    "product_id": {"type": "string",  "description": "ID du produit (champ productId retourné par search)"},
                    "quantity":   {"type": "integer", "description": "Nombre d'unités à ajouter"},
                },
            },
        ),
        Tool(
            name="remove_from_cart",
            description="Supprime un produit spécifique du panier Chronodrive.",
            inputSchema={
                "type": "object",
                "required": ["product_id"],
                "properties": {
                    "product_id": {"type": "string", "description": "ID du produit à supprimer (champ productId retourné par search)"},
                },
            },
        ),
        Tool(
            name="reset_cart",
            description="Vide entièrement le panier Chronodrive.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_cart",
            description="Retourne le contenu actuel du panier Chronodrive (produits, quantités, prix).",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="auth",
            description="Force le renouvellement du token d'authentification Chronodrive.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ── Tool implementations ───────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _handle(name, arguments)
        )
    except Exception as e:
        result = f"Erreur: {e}"
    return [TextContent(type="text", text=result)]


def _handle(name: str, args: dict) -> str:
    if name == "auth":
        # Bust frontend config cache so fresh API keys are fetched
        if cd._CONFIG_CACHE_FILE.exists():
            cd._CONFIG_CACHE_FILE.unlink()
        session = cd.authenticate()
        return f"Authentifié — cart: {session['cart_id']}"

    if name == "get_cart":
        session = cd.ensure_session()
        items = cd.get_cart(session)
        if not items:
            return "Panier vide."
        total = sum(float(i["price"].rstrip("€")) * i["quantity"] for i in items if i.get("price"))
        return json.dumps({"items": items, "total": f"{total:.2f}€"}, ensure_ascii=False, indent=2)

    if name == "reset_cart":
        session = cd.ensure_session()
        removed, failed = cd.reset_cart(session)
        return f"Panier vidé : {removed} supprimé(s), {failed} échec(s)"

    if name == "search":
        query    = args["query"]
        quantity = int(args.get("quantity", 1))
        unit     = args.get("unit", "pcs")
        top_n    = int(args.get("top_n", 5))
        session  = cd.ensure_session()
        products = cd.search_products(query, session)
        if not products:
            return json.dumps({"query": query, "results": []}, ensure_ascii=False)
        candidates = cd.pick_candidates(products, query, unit, quantity, top_n=top_n)
        return json.dumps({"query": query, "results": candidates}, ensure_ascii=False, indent=2)

    if name == "add_to_cart":
        product_id = args["product_id"]
        quantity   = int(args["quantity"])
        session    = cd.ensure_session()
        ok = cd.add_item_to_cart(product_id, quantity, session)
        return "Ajouté au panier." if ok else "Échec de l'ajout."

    if name == "remove_from_cart":
        product_id = args["product_id"]
        session    = cd.ensure_session()
        ok = cd.remove_item_from_cart(product_id, session)
        return "Supprimé du panier." if ok else "Échec de la suppression."

    return f"Tool inconnu: {name}"


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
