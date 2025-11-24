from flask import Flask, render_template, request, jsonify, Response
from flask_cors import CORS
from groq import Groq
import os
import json

app = Flask(__name__)
CORS(app)

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    messages = data.get('messages', [])
    
    system_message = {
        "role": "system", 
        "content": "You are Simplicity-1A, a helpful AI assistant made by Simplicity. short af answers like 240m. Model parameters: 240 million. simple ai example: User: Why does the sun rise in the east? |AI: Because the Earth rotates. So we see the sun come from that direction | simple responses like above. btw i need broken sentences"
    }
    
    full_messages = [system_message] + messages
    
    def generate():
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=full_messages,
            temperature=1,
            max_completion_tokens=1024,
            top_p=1,
            stream=True,
            stop=None
        )
        
        for chunk in completion:
            content = chunk.choices[0].delta.content
            if content:
                yield f"data: {json.dumps({'content': content})}\n\n"
        
        yield "data: [DONE]\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
