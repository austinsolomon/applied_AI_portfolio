"""copy.py — outreach copy generation through Claude, and the send channel.

TWO PROVIDERS LIVE HERE because they are two halves of one stage: something has
to WRITE the message, and something has to SEND it. Both are mocked by default.

THE GAME LESSON THIS MIRRORS (external_levels/delivery_city). Delivery is a
fleet of cars running routes: a message that goes out is not a message that
arrived. The level's scoreboard reads SENT -> DELIVERED -> CONVERTED per channel,
with deliberately different rates per channel, and the player re-allocates leads
across rounds against MEASURED results rather than a favourite. Our channel model
below copies the game's numbers exactly (channel_rounds.js MODEL) so the repo's
report and the level's scoreboard tell the same story.

WHY THE COPY MODEL IS `claude-opus-5`. Copy that goes out under a real person's
name is the last place to economize: one bad line is a burned domain and a burned
prospect. The stage still runs entirely on mocks with no key, so the default cost
of this choice is zero.
"""

from __future__ import annotations

import json
from typing import Any

from .base import ProviderError, announce, env_key

# The measured channel model, copied from delivery_city/src/channel_rounds.js.
# deliver = fraction of sends that land; convert = fraction of DELIVERED that
# become interested. The pairing is the lesson: the channel with the best
# delivery rate has the worst conversion rate, so "just send more email" is a
# strategy that looks good on the SENT column and bad on the only column that
# pays. Do not "tidy" these numbers — they are the game's.
CHANNEL_MODEL = {
    "email":            {"deliver": 0.88, "convert": 0.10},
    "linkedin_inmail":  {"deliver": 0.52, "convert": 0.50},
    "instagram_dm":     {"deliver": 0.70, "convert": 0.22},
    "email_campaign_2": {"deliver": 0.92, "convert": 0.05},
}


# --------------------------------------------------------------- copy writers --
SYSTEM_PROMPT = """\
You write one short first message per bakery for a one-person web studio. The
seller builds simple websites that let local bakeries take orders online.

Rules, in priority order:
1. Under 90 words. A message that needs scrolling does not get read.
2. One specific, checkable reason this bakery: its review count, how it takes
   orders today, its Instagram. Generic flattery is worse than no message.
3. One link at most, and only if it earns its place.
4. No all-capital words, at most one exclamation mark, and none of "guarantee",
   "free", "act now", "risk-free", "limited time". Spam filters score these and
   so do people. Mind "gluten-free" and "sugar-free": they contain "free".
5. End with one low-effort question, not a calendar demand.
6. Sign with the seller's name exactly as given.

Return JSON only: {"subject": str, "body": str}."""

SELLER_SIGNATURE = "Jordan Ellis"


def bakery_facts_for_the_prompt(lead: dict[str, Any], offer: str) -> str:
    """Everything Claude is told about one bakery. Nothing else reaches the model."""
    # Imported here because the fixture module imports CHANNEL_MODEL from this one.
    from .bakery_outreach_fixture import how_orders_work_today_for
    return (
        f"Offer: {offer}\n"
        f"Sign as: {SELLER_SIGNATURE}\n"
        f"Bakery: {lead.get('name')} in {lead.get('city') or 'Chicago'}\n"
        f"Write to: {lead.get('owner_name') or 'the owner'} "
        f"({lead.get('owner_role') or 'role unknown'})\n"
        f"Google Maps reviews: {lead.get('review_count')}\n"
        f"Locations: {lead.get('location_count')}\n"
        f"Online ordering today: {lead.get('ordering_platform')}\n"
        f"How orders work today: {how_orders_work_today_for(lead)}"
    )


class ClaudeCopyProvider:
    """Live copy generation via the Anthropic SDK.

    The SDK is imported INSIDE the method rather than at module scope so that a
    checkout with no `anthropic` package installed can still import the pipeline
    and run the whole mocked test suite. A missing optional dependency must
    never be able to break `pytest`.
    """

    live = True
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-5") -> None:
        self.api_key = api_key
        self.model = model

    def write(self, lead: dict[str, Any], offer: str) -> dict[str, str]:
        try:
            import anthropic
        except ImportError as missing_package:  # pragma: no cover - exercised only with a key
            raise ProviderError(
                "ANTHROPIC_API_KEY is set but the `anthropic` package is not "
                "installed. `pip install -e '.[live]'` or unset the key to run mocked."
            ) from missing_package

        client = anthropic.Anthropic(api_key=self.api_key)
        prompt = bakery_facts_for_the_prompt(lead, offer)
        # Structured outputs rather than "please reply with JSON" plus a regex:
        # the schema is enforced server-side, so a malformed response is not a
        # failure mode we have to write recovery code for.
        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": {
                "type": "object",
                "properties": {"subject": {"type": "string"},
                               "body": {"type": "string"}},
                "required": ["subject", "body"],
                "additionalProperties": False,
            }}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            raise ProviderError(f"claude declined to write copy for {lead['id']}")
        reply_text = next(block.text for block in response.content if block.type == "text")
        return json.loads(reply_text)


def get_copy_provider(api_key: str | None = None, model: str = "claude-opus-5"):
    """ANTHROPIC_API_KEY set -> Claude writes. Unset -> the bakery templates."""
    _, value = ("ANTHROPIC_API_KEY", api_key) if api_key else env_key("ANTHROPIC_API_KEY")
    announce("claude", "ANTHROPIC_API_KEY", bool(value))
    if value:
        return ClaudeCopyProvider(value, model=model)
    # Imported here because the fixture module imports CHANNEL_MODEL from this one.
    from .bakery_outreach_fixture import BakeryTemplateCopyProvider
    return BakeryTemplateCopyProvider()


# ----------------------------------------------------------------- the sender --
class HttpSendProvider:
    """Live sender adapter. Vendor-specific by nature — hence the explicit stub."""

    live = True

    def __init__(self, key: str, vendor: str = "sender") -> None:
        self.key = key
        self.name = vendor

    def send(self, lead: dict[str, Any], channel: str,
             message: dict[str, str]) -> dict[str, Any]:
        raise ProviderError(
            f"{self.name}: live sending is not wired to a vendor endpoint. Implement "
            "send() against your sequencer's API (Instantly / Resend / SMTP), or unset "
            "EMAIL_SENDER_API_KEY to run the offline sender.\n"
            "SAFETY: this stub is deliberately unimplemented — an accidentally live "
            "outreach run mails real people, which is not an error you can take back."
        )


def get_send_provider(api_key: str | None = None):
    """EMAIL_SENDER_API_KEY set -> live adapter (stubbed). Unset -> the offline sender."""
    name, value = (("EMAIL_SENDER_API_KEY", api_key) if api_key
                   else env_key("EMAIL_SENDER_API_KEY", "INSTANTLY_API_KEY", "RESEND_API_KEY"))
    announce("sender", name or "EMAIL_SENDER_API_KEY", bool(value))
    if value:
        return HttpSendProvider(value, name or "sender")
    # Imported here because the fixture module imports CHANNEL_MODEL from this one.
    from .bakery_outreach_fixture import BakeryFixtureSendProvider
    return BakeryFixtureSendProvider()
