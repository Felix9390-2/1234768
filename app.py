from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
from groq import Groq
import os
import json

app = Flask(__name__)
CORS(app)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

MODELS = {
    "a1": "llama-3.1-8b-instant",
    "r1": "openai/gpt-oss-120b"
}

SYSTEM_PROMPTS = {
    "a1": (
        "You are Simplicity-1A, a helpful AI assistant made by Simplicity. "
        "short af answers like 240m. Model parameters: 240 million. "
        "simple ai example: User: Why does the sun rise in the east? "
        "|AI: Because the Earth rotates. So we see the sun come from that direction | "
        "simple responses like above. btw i need broken sentences"
    ),
    "r1": (
        "You are Simplicity-R1, 120b parameters model and u can give long answers if instructions given an advanced reasoning AI by Simplicity. "
        "You think deeply and provide thorough, well-structured answers. "
        "When executing a task plan, you will be given numbered steps. "
        "After completing each step in your response, emit the exact marker §STEP_DONE:N§ "
        "(where N is the step number) on its own line immediately after finishing that step. "
        "These markers are system signals — do not reference or explain them in your text."
    )
}

PLANNING_PROMPT = """Analyze this user request and determine if it is a heavy/complex task or a simple query.

SIMPLE (isHeavy: false): greetings, basic questions, simple lookups, casual conversation, single-fact answers, anything trivial.
HEAVY (isHeavy: true): coding tasks, debugging, multi-step analysis, writing documents, research, system design, data processing, complex explanations with multiple distinct parts, anything requiring sustained effort.

User request: "{message}"

Respond ONLY with valid JSON, no markdown fences, no extra text:
{{
  "isHeavy": true,
  "taskTitle": "Short descriptive title for this task",
  "tasks": [
    {{"id": 1, "title": "Step title", "description": "Brief description of what this step does"}},
    {{"id": 2, "title": "Step title", "description": "Brief description"}},
    ...
  ]
}}

For simple queries respond ONLY with: {{"isHeavy": false, "taskTitle": "", "tasks": []}}
For heavy tasks generate 3 to 6 concrete, meaningful, sequential steps."""


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/plan', methods=['POST'])
def plan():
    data = request.json
    message = data.get('message', '')
    reasoning_effort = data.get('reasoning', data.get('reasoningEffort', 'low'))

    prompt = PLANNING_PROMPT.format(message=message)

    try:
        completion = client.chat.completions.create(
            model=MODELS["r1"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=512,
            reasoning_effort=reasoning_effort,
            top_p=1,
            stream=False,
            stop=None
        )

        response_text = completion.choices[0].message.content.strip()

        # Strip markdown fences if model added them
        if "```" in response_text:
            parts = response_text.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    json.loads(part)
                    response_text = part
                    break
                except Exception:
                    continue

        plan_data = json.loads(response_text)
        return jsonify(plan_data)

    except Exception as e:
        return jsonify({"isHeavy": False, "taskTitle": "", "tasks": [], "error": str(e)})


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    messages = data.get('messages', [])
    model_key = data.get('model', 'a1')
    tasks = data.get('tasks', None)
    reasoning_effort = data.get('reasoning', data.get('reasoningEffort', 'medium'))

    model = MODELS.get(model_key, MODELS['a1'])
    system_content = SYSTEM_PROMPTS.get(model_key, SYSTEM_PROMPTS['a1'])

    if tasks and model_key == 'r1':
        task_lines = '\n'.join(
            [f"{t['id']}. {t['title']}: {t['description']}" for t in tasks]
        )
        system_content += (
            f"\n\nTask plan to execute:\n{task_lines}\n\n"
            "Work through each step in order. After completing each step, "
            "emit §STEP_DONE:N§ on its own line."
        )

    system_message = {"role": "system", "content": system_content}
    full_messages = [system_message] + messages

    def generate():
        kwargs = {
            "model": model,
            "messages": full_messages,
            "frequency_penalty": 0.6,
            "presence_penalty": 0.4,
            "max_completion_tokens": 4096 if model_key == 'r1' else 1024,
            "top_p": 1,
            "stream": True,
            "stop": None
        }

        if model_key == 'r1':
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs["temperature"] = 0.7

        try:
            completion = client.chat.completions.create(**kwargs)

            for chunk in completion:
                delta = chunk.choices[0].delta

                # Reasoning / thinking tokens
                reasoning = getattr(delta, 'reasoning_content', None)
                if reasoning:
                    yield f"data: {json.dumps({'type': 'reasoning', 'content': reasoning})}\n\n"

                content = delta.content
                if content:
                    yield f"data: {json.dumps({'type': 'content', 'content': content})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield "data: [DONE]\n\n"

    return Response(generate(), mimetype='text/event-stream')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
