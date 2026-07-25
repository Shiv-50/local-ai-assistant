output_prompt = """
[OUTPUT STRUCTURE]
Before returning "success", you MUST verify that your actions actually had the intended effect. Do not blindly assume success just because a tool returned without error.
For example, if you type text, check the screen to ensure it was typed into the correct field. If you send a message, verify it appears in the chat.

The final output structure should always be valid JSON:
{
    "message": "Response message",
    "task_status": "success" | "failure" | "replan",
    "context": "Reason and necessary context for task_status"
}
"""