from llm_service import generate_response

COACH_CONFUSION_SYSTEM_PROMPT = """
You are a study assistant focusing on learning effectiveness.
Your goal is to clarify misunderstandings in a short, actionable way.
The student is confused about a specific topic.
They have chosen a confusion category: 'concept', 'example', 'steps', or 'application'.

For 'concept': Provide a simple, clear explanation (analogy if helpful).
For 'example': Provide ONE concrete, relatable example.
For 'steps': Provide a clear step-by-step breakdown.
For 'application': Provide a real-world use case or situation where this applies.

Keep your response under 4 sentences. Be direct. No textbook jargon.
""".strip()

def handle_confusion(topic: str, confusion_type: str, user_question: str) -> str:
    """Generates a targeted explanation based on the type of confusion."""
    prompt = f"Topic context: {topic}\n"
    if user_question:
        prompt += f"User's specific question: {user_question}\n"
    prompt += f"Target format requested: {confusion_type}"

    content_blocks = [{"text": prompt}]

    try:
        response_text = generate_response(
            content_blocks,
            system_prompt=COACH_CONFUSION_SYSTEM_PROMPT,
            max_tokens=300
        )
        return response_text.strip()
    except Exception as e:
        print(f"[CoachAgent] Confusion error: {e}")
        return "I'm having trouble analyzing this right now. Please try again."
