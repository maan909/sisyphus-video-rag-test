import json
import time
import logging
import boto3
from boto3.dynamodb.conditions import Key

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

KNOWLEDGE_BASE_ID = "qwertjyui"
AWS_REGION = "ap-south-1"
CHAT_TABLE_NAME = "sisyphus-chat-"

BEDROCK_MODEL_ID = "qwen.qwen3-235b-a22b-2507-v1:0"

bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
bedrock_runtime = boto3.client("bedrock-runtime", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
chat_table = dynamodb.Table(CHAT_TABLE_NAME)


def rewrite_query_with_context(query: str, history: list) -> str:
    """
    Use LLM to rewrite a follow-up query into a standalone query.
    Example: "What complications can occur in it?" → "What complications can occur in pancreatitis?"
    """
    if not history:
        logger.info("No history - using original query")
        return query
    
    # Format history for the rewrite prompt
    history_text = []
    for msg in history:
        if msg.get("message_id") == "META":
            continue
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            history_text.append(f"User: {content}")
        elif role == "assistant" and content:
            # Truncate long assistant responses
            truncated = content[:300] + "..." if len(content) > 300 else content
            history_text.append(f"Assistant: {truncated}")
    
    if not history_text:
        return query
    
    conversation = "\n".join(history_text)
    
    rewrite_prompt = f"""Given the conversation history below, rewrite the follow-up question to be a standalone question that includes all necessary context.

Conversation History:
{conversation}

Follow-up Question: {query}

Rewrite the follow-up question to be standalone (include the topic/subject explicitly). 
Only output the rewritten question, nothing else."""

    logger.info("Rewriting query with LLM...")
    logger.info(f"Original query: {query}")
    
    try:
        response = bedrock_runtime.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": rewrite_prompt}]}],
            inferenceConfig={
                "maxTokens": 150,
                "temperature": 0.1  # Low temperature for consistent rewrites
            }
        )
        
        output = response.get("output", {})
        message = output.get("message", {})
        content = message.get("content", [])
        
        if content and len(content) > 0:
            rewritten = content[0].get("text", "").strip()
            logger.info(f"Rewritten query: {rewritten}")
            return rewritten
        
        logger.warning("No rewrite generated, using original query")
        return query
        
    except Exception as e:
        logger.error(f"Query rewrite failed: {str(e)}, using original query")
        return query


def retrieve_context(query: str, history: list = None, number_of_results: int = 5):
    """Retrieve relevant context from Knowledge Base. Returns (context, citations, rewritten_query)."""
    # Rewrite query if there's history (for follow-up questions)
    search_query = rewrite_query_with_context(query, history) if history else query
    
    logger.info(f"Retrieving context from Knowledge Base: {KNOWLEDGE_BASE_ID}")
    logger.info(f"Original query: {query}")
    logger.info(f"Search query: {search_query}")
    
    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": search_query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": number_of_results
            }
        }
    )
    results = response.get("retrievalResults", [])
    logger.info(f"Retrieved {len(results)} results from Knowledge Base")
    
    context_chunks = []
    citations = []
    for idx, item in enumerate(results, start=1):
        text = item.get("content", {}).get("text", "")
        location = item.get("location", {})
        score = item.get("score", None)
        if text:
            context_chunks.append(f"[Source {idx}]\n{text}")
        citations.append({
            "source_number": idx,
            "score": score,
            "location": location
        })
    return "\n\n".join(context_chunks), citations, search_query


def get_chat_history(chat_id: str, limit: int = 10):
    """Fetch chat history from DynamoDB."""
    logger.info(f"Fetching chat history for chat_id: {chat_id}")
    try:
        response = chat_table.query(
            KeyConditionExpression=Key("chat_id").eq(chat_id),
            ScanIndexForward=True
        )
        items = response.get("Items", [])
        logger.info(f"Found {len(items)} messages in chat history")
        return items[-limit:]
    except Exception as e:
        logger.error(f"Failed to fetch chat history: {str(e)}")
        return []


def save_message(chat_id: str, message_id: str, role: str, content: str, citations=None):
    """Save a message to DynamoDB."""
    logger.info(f"Saving message to DynamoDB - chat_id: {chat_id}, message_id: {message_id}, role: {role}")
    item = {
        "chat_id": chat_id,
        "message_id": message_id,
        "role": role,
        "content": content,
        "timestamp": int(time.time())
    }
    if citations:
        item["citations"] = json.dumps(citations)
    
    try:
        chat_table.put_item(Item=item)
        logger.info(f"Successfully saved {role} message to DynamoDB")
    except Exception as e:
        logger.error(f"Failed to save message to DynamoDB: {str(e)}")
        raise


def update_chat_title(chat_id: str, title: str):
    """Update chat title in DynamoDB."""
    logger.info(f"Updating chat title - chat_id: {chat_id}, title: {title}")
    try:
        chat_table.put_item(Item={
            "chat_id": chat_id,
            "message_id": "META",
            "role": "system",
            "content": title,
            "timestamp": int(time.time())
        })
        logger.info("Successfully updated chat title")
    except Exception as e:
        logger.error(f"Failed to update chat title: {str(e)}")


def format_history_for_prompt(history: list) -> str:
    """Format chat history as Previous Context for the prompt."""
    if not history:
        return ""
    
    formatted = []
    for msg in history:
        if msg.get("message_id") == "META":
            continue
        
        role = msg.get("role", "")
        content = msg.get("content", "")
        
        if role == "user" and content:
            formatted.append(f"User: {content}")
        elif role == "assistant" and content:
            formatted.append(f"Assistant: {content}")
    
    if not formatted:
        return ""
    
    return "\n".join(formatted)


def build_prompt(current_query: str, context: str, history: list) -> str:
    """Build the complete prompt with history, context, and current query."""
    
    # Format previous conversation
    previous_context = format_history_for_prompt(history)
    
    prompt = """You are an AI teaching assistant helping students learn from educational content.

Use the following context from the knowledge base to answer the question. 
If the answer is not in the context, say "This topic is not available in the provided course material."
"""
    
    # Add previous conversation if exists
    if previous_context:
        prompt += f"""
Previous Context:
{previous_context}
"""
    
    prompt += f"""
Knowledge Base Context:
{context}

Current Question: {current_query}

Provide a clear, student-friendly answer based on the context above."""

    return prompt


def generate_answer(prompt: str) -> str:
    """Generate answer using Bedrock Converse API."""
    logger.info(f"Generating answer using Bedrock model: {BEDROCK_MODEL_ID}")
    logger.info(f"Prompt length: {len(prompt)} characters")
    
    # Log the full prompt for debugging
    logger.info("=" * 60)
    logger.info("FULL PROMPT BEING SENT TO BEDROCK:")
    logger.info("=" * 60)
    logger.info(prompt)
    logger.info("=" * 60)
    
    messages = [
        {
            "role": "user",
            "content": [{"text": prompt}]
        }
    ]
    
    response = bedrock_runtime.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=messages,
        inferenceConfig={
            "maxTokens": 4096,
            "temperature": 0.6,
            "topP": 0.95
        }
    )
    
    output = response.get("output", {})
    message = output.get("message", {})
    content = message.get("content", [])
    
    if content and len(content) > 0:
        answer = content[0].get("text", "").strip()
        logger.info(f"Generated answer length: {len(answer)} characters")
        return answer
    
    logger.warning("No response generated from Bedrock")
    return "No response generated."


def lambda_handler(event, context):
    logger.info("Lambda invoked")
    logger.info(f"Event: {json.dumps(event)[:500]}")
    
    try:
        body = event
        if "body" in event:
            body = event["body"]
            if isinstance(body, str):
                body = json.loads(body)

        query = body.get("query")
        chat_id = body.get("chat_id")
        message_id = body.get("message_id")
        
        logger.info(f"Request - chat_id: {chat_id}, message_id: {message_id}")
        logger.info(f"Query: {query[:100] if query else 'None'}...")

        if not query:
            logger.warning("Missing query in request")
            return {"statusCode": 400, "body": json.dumps({"error": "Missing 'query'"})}

        # Get chat history BEFORE saving current message
        history = get_chat_history(chat_id, limit=10) if chat_id else []
        logger.info(f"History messages for context: {len(history)}")

        # Save user message AFTER fetching history
        if chat_id and message_id:
            logger.info("Saving user message to chat history")
            save_message(chat_id, message_id, "user", query)

        # Retrieve from Knowledge Base - pass history for query rewriting
        retrieved_context, citations, rewritten_query = retrieve_context(query=query, history=history, number_of_results=5)

        if not retrieved_context.strip():
            logger.warning("No context retrieved from Knowledge Base")
            answer = "I could not find the answer in the knowledge base."
            if chat_id and message_id:
                save_message(chat_id, message_id + "-resp", "assistant", answer, [])
            return {
                "statusCode": 200,
                "body": json.dumps({"query": query, "answer": answer, "citations": []})
            }

        # Build prompt with history and context - use rewritten query for Current Question
        prompt = build_prompt(rewritten_query, retrieved_context, history)
        
        # Generate answer
        answer = generate_answer(prompt)

        # Save assistant response
        if chat_id and message_id:
            logger.info("Saving assistant response to chat history")
            save_message(chat_id, message_id + "-resp", "assistant", answer, citations)
            if len(history) <= 2:
                title = query[:50] + ("..." if len(query) > 50 else "")
                update_chat_title(chat_id, title)

        logger.info("Request completed successfully")
        return {
            "statusCode": 200,
            "body": json.dumps({"query": query, "answer": answer, "citations": citations})
        }

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": "Unexpected error", "message": str(e)})}
