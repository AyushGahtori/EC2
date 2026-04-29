from __future__ import annotations

import os

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate

from llm import create_restaurant_llm
from prompts.menu_agent_prompts import MENU_AGENT_PROMPT
from tools.menu_tools import (
    filter_menu_by_category,
    format_menu_display,
    get_chef_recommendations,
    get_menu_item_by_name,
    get_popular_items,
    load_menu_from_file,
    search_menu_items,
)
from tools.validation_tools import sanitize_input, validate_dietary_restrictions


class MenuAgent:
    def __init__(self, llm=None):
        self.llm = llm or create_restaurant_llm()
        self.menu = self.load_menu()
        self.prompt_template = PromptTemplate(
            input_variables=["menu", "customer_input"],
            template=MENU_AGENT_PROMPT,
        )
        self.menu_chain = self.prompt_template | self.llm | StrOutputParser()

    def load_menu(self):
        menu_file_path = os.path.join(os.path.dirname(__file__), "..", "data", "menu.json")
        return load_menu_from_file(menu_file_path)

    def display_menu(self, category=None, dietary_filter=None):
        menu_to_display = self.menu

        if category:
            menu_to_display = filter_menu_by_category(menu_to_display, category)

        if dietary_filter:
            menu_to_display = [
                item
                for item in menu_to_display
                if validate_dietary_restrictions(item, [dietary_filter])
            ]

        return format_menu_display(menu_to_display)

    def get_menu_item(self, item_name):
        sanitized_name = sanitize_input(item_name)
        return get_menu_item_by_name(self.menu, sanitized_name)

    def search_menu(self, query):
        sanitized_query = sanitize_input(query)
        results = search_menu_items(self.menu, sanitized_query)
        return format_menu_display(results)

    def get_recommendations(self):
        recommendations = get_chef_recommendations(self.menu)
        popular = get_popular_items(self.menu)

        response = "CHEF RECOMMENDATIONS\n"
        response += format_menu_display(recommendations)
        response += "\nPOPULAR ITEMS\n"
        response += format_menu_display(popular)
        return response

    def handle_menu_query(self, customer_input):
        sanitized_input = sanitize_input(customer_input)
        try:
            return self.menu_chain.invoke(
                {
                    "menu": format_menu_display(self.menu),
                    "customer_input": sanitized_input,
                }
            )
        except Exception:
            return (
                "I can help with the menu, recommendations, ingredients, and dietary filters. "
                "Ask about any item or say 'show menu' to browse everything."
            )
