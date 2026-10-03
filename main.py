"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the project instructions and rubric for guidance.
Work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSAI_Agent")

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

# TODO: Create the BedrockAgentCoreApp instance
app = BedrockAgentCoreApp()  # Replace this line


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in the infrastructure setup section of the project instructions.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# This starter uses an unsigned MCP connection and therefore assumes the
# project Gateway is configured with the NONE authorizer.
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-snj1daspyw.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"   # TODO: Replace with your Gateway URL
KB_ID       = "ZUFRWWGIJT"          # TODO: Replace with your Knowledge Base ID
REGION      = "us-east-1"        # TODO: Replace with your AWS region
MEMORY_ID   = "CustomerSupportMemory-zcqcpn22a4"        # TODO: Replace with your Memory ID


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "global.amazon.nova-2-lite-v1:0"

# TODO: Create the BedrockModel instance
model = BedrockModel(model_id=model_id)  # Replace this line

# TODO: Create the MemoryClient instance
memory_client = MemoryClient(region_name=REGION)  # Replace this line

# TODO: Create the boto3 bedrock-agent-runtime client
_bedrock_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=REGION,
)  # Replace this line

# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Read the namespace from strategy["namespaceTemplates"][0].
#      For compatibility with older AgentCore responses, fall back to
#      strategy["namespaces"][0] when namespaceTemplates is absent.
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    # TODO: Implement this function
    response = mem_client.get_memory_strategies(memory_id)

    if isinstance(response, list):
        strategies = response
    else:
        strategies = response.get("strategies", [])

    namespaces = {}

    for strategy in strategies:
        strategy_type = strategy.get("type")

        namespace_templates = strategy.get("namespaceTemplates")

        # Support older API response format
        if namespace_templates is None:
            namespace_templates = strategy.get("namespaces", [])

        if strategy_type and namespace_templates:
            namespaces[strategy_type] = namespace_templates[0]

    return namespaces


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        # TODO: Call get_namespaces() and store the result as self.namespaces
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(memory_client, memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        #   2. Check it is a user message and not a tool result
        #   3. Extract the user query text
        #   4. For each namespace in self.namespaces, call retrieve_memories()
        #   5. Collect non-empty memory texts with strategy type tags
        #   6. If any found, prepend them to the user message
        logger.info("=== MEMORY RETRIEVAL HOOK START ===")

        message = event.message

        logger.info("Message received by hook: %r", message)

        if message.get("role") != "user":
            logger.info("Message is not a user message.")
            return

        content_blocks = message.get("content", [])

        if not isinstance(content_blocks, list):
            logger.info("Message content is not a list: %r", content_blocks)
            return

        text_parts = []

        for block in content_blocks:
            if isinstance(block, dict) and "text" in block:
                text_parts.append(block.get("text", ""))

        original_message = "\n".join(
            text for text in text_parts if text
        ).strip()

        if not original_message:
            logger.info("No text found in user message.")
            return

        logger.info("Original user query: %s", original_message)
        logger.info("Actor ID: %s", self.actor_id)
        logger.info("Memory ID: %s", self.memory_id)
        logger.info("Namespaces: %r", self.namespaces)

        memories = []

        for strategy_type, namespace_template in self.namespaces.items():
            namespace = namespace_template.replace(
                "{actorId}",
                self.actor_id,
            )

            logger.info(
                "Retrieving %s from namespace %s",
                strategy_type,
                namespace,
            )

            try:
                results = self.memory_client.retrieve_memories(
                    memory_id=self.memory_id,
                    namespace=namespace,
                    query=original_message,
                    top_k=5,
                )

                logger.info(
                    "Retrieved %d results from %s",
                    len(results),
                    namespace,
                )

                for result in results:
                    logger.info("Memory result: %r", result)

                    memory_content = result.get("content", {})

                    if isinstance(memory_content, dict):
                        memory_text = memory_content.get("text", "")
                    else:
                        memory_text = str(memory_content)

                    if memory_text:
                        memories.append(
                            f"[{strategy_type}] {memory_text}"
                        )

            except Exception:
                logger.exception(
                    "Failed to retrieve memories from %s",
                    namespace,
                )

        if memories:
            context = "Customer Context:\n" + "\n".join(memories)

            logger.info(
                "INJECTING MEMORY CONTEXT: %s",
                context,
            )

            message["content"] = [
                {
                    "text": context + "\n\n" + original_message
                }
            ]

        else:
            logger.info("NO MEMORIES RETRIEVED.")

        logger.info("=== MEMORY RETRIEVAL HOOK END ===")

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        # TODO: Implement memory saving
        # Steps:
        #   1. Get messages from event.agent.messages
        #   2. Walk backwards to find the last user query (plain text)
        #      and the last assistant response
        #   3. Call memory_client.create_event() with both messages
        logger.info("=== MEMORY SAVE HOOK START ===")

        messages = event.agent.messages

        if not messages:
            logger.info("No agent messages found.")
            return

        customer_query = None

        # Find the most recent user message.
        for message in reversed(messages):
            if message.get("role") != "user":
                continue

            content_blocks = message.get("content", [])

            if not isinstance(content_blocks, list):
                continue

            text_parts = []

            for block in content_blocks:
                if isinstance(block, dict) and "text" in block:
                    text_parts.append(block.get("text", ""))

            text = "\n".join(
                part for part in text_parts if part
            ).strip()

            if text:
                customer_query = text
                break

        # Get the final assistant response from AgentResult.
        agent_response = None

        if event.result is not None:
            response_message = event.result.message
            content_blocks = response_message.get("content", [])

            if isinstance(content_blocks, list):
                text_parts = []

                for block in content_blocks:
                    if isinstance(block, dict) and "text" in block:
                        text_parts.append(block.get("text", ""))

                text = "\n".join(
                    part for part in text_parts if part
                ).strip()

                if text:
                    agent_response = text

        logger.info("Customer query: %r", customer_query)
        logger.info("Agent response: %r", agent_response)

        if not customer_query or not agent_response:
            logger.info("Missing customer query or agent response; nothing to save.")
            return

        try:
            response = self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[
                    (customer_query, "USER"),
                    (agent_response, "ASSISTANT"),
                ],
            )

            logger.info("Memory event created successfully: %r", response)

        except Exception:
            logger.exception(
                "Failed to save support interaction to memory"
            )

        logger.info("=== MEMORY SAVE HOOK END ===")

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        # TODO: Register save_support_interaction on AfterInvocationEvent
        registry.add_callback(
            MessageAddedEvent,
            self.retrieve_customer_context,
        )

        registry.add_callback(
            AfterInvocationEvent,
            self.save_support_interaction,
        )


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    # TODO: Implement the Knowledge Base search
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        response = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )

        results = response.get("retrievalResults", [])

        if not results:
            return "No relevant information found in the knowledge base."

        chunks = []

        for result in results:
            text = result.get("content", {}).get("text", "")

            if text:
                chunks.append(text)

        if not chunks:
            return "No relevant information found in the knowledge base."

        return "\n---\n".join(chunks)

    except Exception as e:
        logger.exception("Knowledge base retrieval failed")
        return f"Knowledge base search failed: {type(e).__name__}: {e}"



# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # TODO: Build the code string (use an f-string to inject the arguments)
    code = f"""
    import json
    import math

    loyalty_points = {loyalty_points}
    tier = {tier!r}
    order_total = {order_total}
    product_category = {product_category!r}

    earn_rates = {{
        "standard": 1,
        "device": 2,
        "fresh": 5
    }}

    tier_rates = {{
        "Silver": 0.00,
        "Gold": 0.10,
        "Platinum": 0.15
    }}

    # Floor available points to the nearest 500.
    points_available = math.floor(loyalty_points / 500) * 500

    # Points may cover at most 50% of the order.
    max_discount_value = order_total * 0.50
    max_points = math.floor(max_discount_value * 100 / 500) * 500

    points_redeemed = min(points_available, max_points)

    # 100 points = $1
    points_discount = points_redeemed / 100

    subtotal_after_points = order_total - points_discount

    tier_discount_pct = tier_rates.get(tier, 0.00)
    tier_discount = subtotal_after_points * tier_discount_pct

    final_total = subtotal_after_points - tier_discount
    total_savings = order_total - final_total

    points_earned = math.floor(
        order_total * earn_rates.get(product_category, 1)
    )

    remaining_points = loyalty_points - points_redeemed

    result = {{
        "points_redeemed": points_redeemed,
        "tier_discount_pct": tier_discount_pct,
        "final_total": round(final_total, 2),
        "total_savings": round(total_savings, 2),
        "points_earned": points_earned,
        "remaining_points": remaining_points
    }}

    print(json.dumps(result))
    """
    # Replace with your code string

    try:
        # TODO: Execute the code using code_session and return the result
        with code_session(REGION) as code_client:
            response = code_client.invoke(
                "executeCode",
                {
                    "language": "python",
                    "code": code,
                    "clearContext": True,
                },
            )

            # The SDK returns metadata plus a nested EventStream.
            stream = response.get("stream")

            if stream is None:
                return json.dumps({"error": "No execution stream returned"})

            for event in stream:
                if isinstance(event, dict):
                    # Return the first actual execution result.
                    for key in ("result", "output", "stdout"):
                        if key in event:
                            return json.dumps(event[key])

            return json.dumps({"error": "No execution result returned"})

    except Exception as e:
        # TODO: Implement fallback calculation using tier discount only
        logger.warning(
            "Code Interpreter unavailable: %s. Using tier-only fallback.",
            e,
        )

        tier_rates = {
            "Silver": 0.00,
            "Gold": 0.10,
            "Platinum": 0.15,
        }

        tier_discount_pct = tier_rates.get(tier, 0.00)
        tier_discount = order_total * tier_discount_pct
        final_total = order_total - tier_discount

        fallback = {
            "points_redeemed": 0,
            "tier_discount_pct": tier_discount_pct,
            "final_total": round(final_total, 2),
            "total_savings": round(tier_discount, 2),
            "points_earned": 0,
            "remaining_points": loyalty_points,
        }

        return json.dumps(fallback)



# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """
    # TODO: Implement the agent invocation
    try:
        user_input = payload.get("prompt", "")

        if not user_input:
            return "Please provide a customer support question."

        actor_id = payload.get("customer_id", "anonymous")
        session_id = payload.get("session_id") or str(uuid.uuid4())

        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        agent_core_browser = AgentCoreBrowser(region=REGION)

        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        mcp_client = MCPClient(
            lambda: streamable_http_client(GATEWAY_URL)
        )

        with mcp_client:
            try:
                gateway_tools = mcp_client.list_tools_sync()
                tools.extend(gateway_tools)

                logger.info(
                    "Gateway connected successfully. Loaded %d tools.",
                    len(gateway_tools),
                )

            except TimeoutError:
                logger.exception("Gateway tool loading timed out")

            except ConnectionError:
                logger.exception("Gateway connection failed")

            except Exception as exc:
                logger.exception(
                    "Gateway tool loading failed: %s", exc
                )

            system_prompt = """
                You are a helpful customer support AI agent.

                Use the available tools when appropriate:
                - Use the knowledge base for product information, policies, warranties,
                loyalty program details, and support information.
                - Use Gateway tools for customer and order information and refund operations.
                - For ANY loyalty discount calculation, you MUST call the
                calculate_loyalty_discount tool. Never calculate loyalty discounts yourself.
                - When using the loyalty discount calculator, pass the customer's
                loyalty_points, tier, order_total, and product_category.
                - Report the numerical results returned by the tool, including points
                redeemed, tier discount, final total, total savings, points earned,
                and remaining points.
                - Use the browser when current web information is required.
                - Use customer context from memory to provide continuity across sessions.

                Do not invent customer, order, refund, product, or policy information.
                Base your answers on information returned by the available tools.
                Be concise, clear, and helpful.
                """

            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=system_prompt,
            )

            response = await agent.invoke_async(user_input)

            if hasattr(response, "content") and response.content:
                first_block = response.content[0]

                if isinstance(first_block, dict):
                    return first_block.get("text", str(first_block))

                return str(first_block)

            return str(response)

    except Exception as e:
        logger.exception("Agent invocation failed")
        return f"Sorry, I encountered an error while processing your request. Please try again."



# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
