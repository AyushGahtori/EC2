import json
import os
from typing import Dict, List, Optional


def load_menu_from_file(file_path: str) -> List[Dict]:
    """
    Load menu data from JSON file and flatten categorized structure.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file_handle:
            menu_data = json.load(file_handle)

        if isinstance(menu_data, list):
            return menu_data

        if isinstance(menu_data, dict):
            flattened_menu = []
            for items in menu_data.values():
                if isinstance(items, list):
                    flattened_menu.extend(items)
            return flattened_menu

        return get_default_menu()
    except FileNotFoundError:
        return get_default_menu()
    except json.JSONDecodeError:
        if os.getenv("DEBUG_MODE", "false").lower() == "true":
            print("Error: Invalid JSON format in menu file. Using default menu.")
        return get_default_menu()


def get_default_menu() -> List[Dict]:
    """
    Return a default menu if file is not found.
    """
    return [
        {
            "id": "butter_chicken",
            "name": "Butter Chicken",
            "description": "Tandoor-grilled chicken simmered in a rich tomato-butter gravy",
            "price": 399.0,
            "category": "mains",
            "dietary": [],
            "popular": True,
            "chef_recommendation": True,
        },
        {
            "id": "paneer_butter_masala",
            "name": "Paneer Butter Masala",
            "description": "Soft paneer cubes in creamy tomato-cashew masala",
            "price": 349.0,
            "category": "mains",
            "dietary": ["vegetarian", "gluten_free"],
            "popular": True,
            "chef_recommendation": True,
        },
        {
            "id": "chicken_biryani",
            "name": "Chicken Biryani",
            "description": "Fragrant basmati rice layered with spiced chicken and herbs",
            "price": 329.0,
            "category": "mains",
            "dietary": [],
            "popular": True,
            "chef_recommendation": False,
        },
        {
            "id": "masala_chai",
            "name": "Masala Chai",
            "description": "Indian spiced milk tea",
            "price": 79.0,
            "category": "beverages",
            "dietary": ["vegetarian"],
            "popular": True,
            "chef_recommendation": False,
        },
    ]


def search_menu_items(menu: List[Dict], query: str) -> List[Dict]:
    """
    Search menu items by name or description.
    """
    lowered_query = query.lower()
    results = []
    for item in menu:
        if (
            lowered_query in item["name"].lower()
            or lowered_query in item["description"].lower()
            or lowered_query in item["category"].lower()
        ):
            results.append(item)
    return results


def get_menu_item_by_name(menu: List[Dict], item_name: str) -> Optional[Dict]:
    """
    Get a specific menu item by name.
    """
    lowered_name = item_name.lower()
    for item in menu:
        if item["name"].lower() == lowered_name:
            return item
    return None


def filter_menu_by_category(menu: List[Dict], category: str) -> List[Dict]:
    """
    Filter menu items by category.
    """
    lowered_category = category.lower()
    return [item for item in menu if item["category"].lower() == lowered_category]


def filter_menu_by_dietary(menu: List[Dict], dietary_requirement: str) -> List[Dict]:
    """
    Filter menu items by dietary requirements.
    """
    lowered_requirement = dietary_requirement.lower()
    if lowered_requirement == "vegetarian":
        return [item for item in menu if "vegetarian" in item.get("dietary", [])]
    if lowered_requirement == "vegan":
        return [item for item in menu if "vegan" in item.get("dietary", [])]
    if lowered_requirement in {"gluten-free", "gluten_free"}:
        return [item for item in menu if "gluten_free" in item.get("dietary", [])]
    return menu


def get_popular_items(menu: List[Dict]) -> List[Dict]:
    """
    Get popular menu items.
    """
    return [item for item in menu if item.get("popular", False)]


def get_chef_recommendations(menu: List[Dict]) -> List[Dict]:
    """
    Get chef recommended items.
    """
    return [item for item in menu if item.get("chef_recommendation", False)]


def format_menu_display(menu: List[Dict]) -> str:
    """
    Format menu for display to customer.
    """
    if not menu:
        return "No menu items available."

    categories: Dict[str, List[Dict]] = {}
    for item in menu:
        category = item["category"].title()
        categories.setdefault(category, []).append(item)

    display = "**AI BISTRO MENU**\n\n"
    for category, items in categories.items():
        display += f"**{category.upper()}**\n"
        display += "-" * 40 + "\n"
        for item in items:
            display += f"- {item['name']} - INR {item['price']:.2f}"
            if item.get("popular"):
                display += " [Popular]"
            if item.get("chef_recommendation"):
                display += " [Chef Pick]"
            display += f"\n  {item['description']}\n\n"

    display += "[Popular] = Popular item | [Chef Pick] = Chef recommendation\n"
    return display
