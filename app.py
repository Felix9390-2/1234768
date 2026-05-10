from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
from groq import Groq
import os
import json

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
CORS(app)


def get_groq_client():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY environment variable")
    return Groq(api_key=api_key)

A1_MODEL = os.environ.get("A1_MODEL", "llama-3.1-8b-instant")
R1_MODEL = os.environ.get("R1_MODEL", "openai/gpt-oss-20b")
_r1_reasoning_env = os.environ.get("R1_SUPPORTS_REASONING_EFFORT", "true").lower()
R1_SUPPORTS_REASONING = _r1_reasoning_env in ("1", "true", "yes")
REASONING_TEMPERATURE = {"low": 0.4, "medium": 0.7, "high": 0.9}

MODELS = {
    "a1": A1_MODEL,
    "r1": R1_MODEL
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
        "You are Simplicity-R1, 140b params AI an advanced reasoning AI by Simplicity. "
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
    return send_from_directory(APP_ROOT, 'index.html')


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.route('/plan', methods=['POST'])
def plan():
    data = request.json
    message = data.get('message', '')

    prompt = PLANNING_PROMPT.format(message=message)

    try:
        client = get_groq_client()
        kwargs = {
            "model": MODELS["r1"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_completion_tokens": 512,
            "top_p": 1,
            "stream": False,
            "stop": None
        }
        if R1_SUPPORTS_REASONING:
            kwargs["reasoning_effort"] = "low"

        completion = client.chat.completions.create(**kwargs)

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

        plan_data = None
        try:
            plan_data = json.loads(response_text)
        except Exception:
            start = response_text.find('{')
            end = response_text.rfind('}')
            if start != -1 and end != -1 and end > start:
                candidate = response_text[start:end + 1]
                plan_data = json.loads(candidate)
            else:
                raise

        if not isinstance(plan_data, dict) or 'isHeavy' not in plan_data:
            raise ValueError('Invalid plan response')

        return jsonify(plan_data)

    except Exception as e:
        return jsonify({"isHeavy": False, "taskTitle": "", "tasks": [], "error": str(e)})


@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    messages = data.get('messages', [])
    model_key = data.get('model', 'a1')
    tasks = data.get('tasks', None)
    reasoning = data.get('reasoning', 'medium')

    if reasoning not in ('low', 'medium', 'high'):
        reasoning = 'medium'

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
        try:
            client = get_groq_client()
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            yield "data: [DONE]\n\n"
            return

        kwargs = {
            "model": model,
            "messages": full_messages,
            "temperature": 1,
            "max_completion_tokens": 8192 if model_key == 'r1' else 1024,
            "top_p": 1,
            "stream": True,
            "stop": None
        }

        if model_key == 'r1':
            kwargs["temperature"] = REASONING_TEMPERATURE.get(reasoning, 0.7)
            if R1_SUPPORTS_REASONING:
                kwargs["reasoning_effort"] = reasoning

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


@app.route('/api/chat', methods=['POST'])
def api_chat():
    auth_header = request.headers.get('Authorization')
    if auth_header != 'Bearer A4r3av_K8y':
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    model_key = data.get('model', 'a1')
    messages = data.get('messages', [])
    reasoning = data.get('reasoning_effort', 'medium')
    stream = data.get('stream', False)

    if reasoning not in ('low', 'medium', 'high'):
        reasoning = 'medium'

    model = MODELS.get(model_key, MODELS['a1'])
    
    # Optional: Inject system prompts if not provided, or let user provide them.
    # To act as a pure bridge, we might just pass the user's messages + system prompt.
    # We will prepend our system prompt if no system prompt is present.
    has_system = any(m.get('role') == 'system' for m in messages)
    full_messages = list(messages)
    if not has_system:
        system_content = SYSTEM_PROMPTS.get(model_key, SYSTEM_PROMPTS['a1'])
        full_messages.insert(0, {"role": "system", "content": system_content})

    try:
        client = get_groq_client()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    kwargs = {
        "model": model,
        "messages": full_messages,
        "temperature": data.get("temperature", 1),
        "max_completion_tokens": data.get("max_completion_tokens", 8192 if model_key == 'r1' else 1024),
        "top_p": data.get("top_p", 1),
        "stream": stream,
        "stop": None
    }

    if model_key == 'r1':
        kwargs["temperature"] = REASONING_TEMPERATURE.get(reasoning, 0.7)
        if R1_SUPPORTS_REASONING:
            kwargs["reasoning_effort"] = reasoning

    try:
        completion = client.chat.completions.create(**kwargs)

        if not stream:
            content = completion.choices[0].message.content
            reasoning_content = getattr(completion.choices[0].message, 'reasoning_content', None)
            return jsonify({
                "content": content,
                "reasoning_content": reasoning_content
            })

        def generate():
            try:
                for chunk in completion:
                    delta = chunk.choices[0].delta
                    r_content = getattr(delta, 'reasoning_content', None)
                    if r_content:
                        yield f"data: {json.dumps({'type': 'reasoning', 'content': r_content})}\n\n"
                    
                    c_content = delta.content
                    if c_content:
                        yield f"data: {json.dumps({'type': 'content', 'content': c_content})}\n\n"
                        
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                yield f"data: {json.dumps({'type': 'error', 'content': str(stream_err)})}\n\n"
                yield "data: [DONE]\n\n"

        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
