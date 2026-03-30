"""
Contacts Agent
Google Contacts operations
"""

import logging
from typing import Dict, Any

from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

PEOPLE_SEARCH_URL = "https://people.googleapis.com/v1/people:searchContacts"


class ContactsAgent(BaseAgent):
    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description='name: the contact name to search for',
            example_output='{"name": "John Smith"}',
            context=context,
        )
        name = (params.get("name") or "").strip()
        if not name:
            return self.failure(
                error="VALIDATION_ERROR",
                message="Please tell me which contact you want me to look up.",
            )

        try:
            response = await self.request_google_api(
                "GET",
                PEOPLE_SEARCH_URL,
                params={
                    "query": name,
                    "readMask": "names,emailAddresses,phoneNumbers",
                    "pageSize": 5,
                },
                retry_on_failure=True,
            )
        except Exception as exc:
            return self.handle_google_exception("Contacts", exc, data={"name": name})

        if response.status_code == 200:
            results = response.json().get("results", [])
            if not results:
                return self.success(summary=f"No contacts found for '{name}'.", data={"contacts": []})

            contacts = []
            for result in results[:5]:
                person = result.get("person", {})
                contacts.append(
                    {
                        "name": person.get("names", [{}])[0].get("displayName", "Unknown"),
                        "emailAddresses": [
                            entry.get("value", "")
                            for entry in person.get("emailAddresses", [])
                            if entry.get("value")
                        ],
                        "phoneNumbers": [
                            entry.get("value", "")
                            for entry in person.get("phoneNumbers", [])
                            if entry.get("value")
                        ],
                    }
                )

            return self.success(
                summary="Found contacts:\n"
                + "\n".join(
                    f"- {contact['name']} | "
                    f"{', '.join(contact['emailAddresses']) or 'No email'} | "
                    f"{', '.join(contact['phoneNumbers']) or 'No phone'}"
                    for contact in contacts
                ),
                data={"contacts": contacts},
            )

        return self.handle_google_api_error("Contacts", response, data={"name": name})
