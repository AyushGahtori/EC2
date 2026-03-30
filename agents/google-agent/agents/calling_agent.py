"""
Calling Agent - Phone calls via Twilio
"""

import logging
import os
import httpx
from typing import Dict, Any
from agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)


class CallingAgent(BaseAgent):
    async def handle(self, user_message: str, context: Dict[str, Any]) -> Dict[str, Any]:
        params = await self.extract_parameters(
            user_message=user_message,
            schema_description="""
- phone_number: the phone number to call (with country code)
- message: optional message or purpose of the call
            """,
            example_output='{"phone_number": "+1234567890", "message": "Confirm appointment"}'
        )

        phone = params.get("phone_number", "")
        message = params.get("message", "")

        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        twilio_from = os.getenv("TWILIO_PHONE_NUMBER", "")

        if twilio_sid and twilio_token and twilio_from and phone:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Calls.json",
                        auth=(twilio_sid, twilio_token),
                        data={
                            "From": twilio_from,
                            "To": phone,
                            "Url": "http://demo.twilio.com/docs/voice.xml"
                        }
                    )
                    if response.status_code == 201:
                        return self.success(
                            summary=f"📞 Call initiated to {phone}",
                            data=response.json()
                        )
            except Exception as e:
                logger.error(f"Twilio error: {e}")

        return self.success(
            summary=f"[SIMULATED] Would initiate a phone call to {phone or 'the specified number'}.\nPurpose: {message}\n\nTo enable real calls, configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in .env",
            data={"simulated": True, "phone": phone}
        )
