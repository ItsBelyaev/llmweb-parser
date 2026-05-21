"""Шаблоны промптов для LangChain.

Каждый промпт — это отдельный ``PromptTemplate``. Преимущества такого
подхода:

* Промпт можно править без перетряхивания основной логики.
* Промпт можно тестировать как обычный текст.
* LangChain автоматически собирает finalized prompt и передаёт его в LLM
  через композицию ``template | llm``.

Все промпты написаны по-английски: открытые модели Llama/Qwen/Mistral
заметно лучше следуют инструкциям, когда инструкции на английском, даже
если данные внутри (HTML) — на русском.
"""

from langchain_core.prompts import PromptTemplate


# --------------------------------------------------------------------------- #
# Прямое извлечение                                                            #
# --------------------------------------------------------------------------- #


PRODUCT_EXTRACTION_TEMPLATE = PromptTemplate(
    input_variables=["source", "html"],
    template="""You are an expert at extracting product data from e-commerce HTML pages.

Extract the following fields from the {source} product page HTML below:
- title: product name/title (string)
- price: numeric price in rubles, no currency symbols (number or null)
- article: product SKU/article number (string or null)
- image_url: full URL of the main product image starting with http (string or null)

Rules:
1. Return ONLY valid JSON, nothing else
2. Do not wrap in markdown code blocks
3. If a field cannot be found, use null
4. Price must be a number (e.g. 1990.00), not a string
5. image_url must be a full URL starting with https:// or http://

HTML:
{html}

JSON response:""",
)


# --------------------------------------------------------------------------- #
# Промпт-классификатор: товарная страница или нет                              #
# --------------------------------------------------------------------------- #


IS_PRODUCT_PAGE_TEMPLATE = PromptTemplate(
    input_variables=["html"],
    template="""Analyze this HTML and determine if it is an e-commerce product page.
A product page typically has: a product title, price, and product image.

Answer with ONLY "yes" or "no".

HTML (first 2000 chars):
{html}

Answer:""",
)


# --------------------------------------------------------------------------- #
# Генерация CSS-селекторов                                                     #
# --------------------------------------------------------------------------- #


INTERACTIVE_ELEMENT_TEMPLATE = PromptTemplate(
    input_variables=["intent", "html"],
    template="""You are analyzing an HTML page from an online store.

Your task: find the CSS selector for an element that performs the
following user intent:

  Intent: "{intent}"

Common phrasings (in any language) for "add_to_cart": Add to cart,
В корзину, Купить, Добавить в корзину, Buy now, Add to bag, Acheter,
In den Warenkorb. AVOID buttons that say "wishlist", "избранное",
"compare" — those are different intents.

Common phrasings for "buy_now": Buy now, Купить сейчас, Checkout,
Оформить заказ, Proceed to checkout.

Common phrasings for "open_product": View, Подробнее, Перейти,
See more, Details.

Return ONLY valid JSON, no markdown, no prose:

{{
  "selector": "<a robust CSS selector>",
  "confidence": <number from 0 to 100>,
  "reasoning": "<one short sentence why this element matches>"
}}

Rules:
1. Prefer button/a/input tags with clear class names or data-* attributes.
2. Avoid auto-generated hashes like .css-1a2b3c.
3. The selector must uniquely identify exactly one element in the HTML below.
4. If no suitable element exists, return: {{"selector": null, "confidence": 0, "reasoning": "not found"}}
5. confidence < 50 means you are guessing — be honest.

HTML:
{html}

JSON:""",
)


SELECTORS_GENERATION_TEMPLATE = PromptTemplate(
    input_variables=["html"],
    template="""You are an expert in CSS selectors and HTML structure analysis.

Below is a cleaned HTML snippet of a product page from an online store.
Produce robust CSS selectors that locate the listed fields on this page.

Return ONLY valid JSON with the following shape (no markdown, no prose):

{{
  "name":     "<CSS selector for the product title>",
  "price":    "<CSS selector for the current selling price>",
  "currency": "<CSS selector for the currency element (or null)>",
  "pictures": "<CSS selector matching the main product image(s)>",
  "category": "<CSS selector for the breadcrumb / category link (or null)>",
  "article":  "<CSS selector for the SKU / article number (or null)>"
}}

Rules:
1. Prefer class- or attribute-based selectors over deep descendant chains.
2. Avoid selectors that rely on auto-generated hashes (e.g. ".css-1a2b3c").
3. Only use class names, ids or attributes that actually appear in the HTML below.
4. A good "name" / "price" / "article" selector should match exactly one element.
5. Use null (not empty string) for fields you cannot locate.

HTML:
{html}

JSON:""",
)
