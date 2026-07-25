output_prompt = """
[OUTPUT STRUCTURE]
The output structure should always be:
{
    message: "Response message",
    task_status: "success"|"failure"|"replan",
    context: "Reason and necessary context for task_status"
}
"""