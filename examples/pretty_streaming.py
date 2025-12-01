#!/usr/bin/env python3
"""
This script will send requests to the Oshepherd API, which will orchestrate Ollama models inference execution
on available Oshepherd Workers using streaming mode.
"""

import ollama


def print_header():
    """Display the application header."""
    print("\n" + "=" * 60)
    print("  🤖 Ollama Streaming Chat")
    print("=" * 60 + "\n")


def print_user_message(prompt, model):
    """Display the user's message and model info."""
    print(f"📝 You: {prompt}")
    print(f"🧠 Model: {model}\n")
    print("💬 Assistant: ", end="", flush=True)


def stream_response(client, model, prompt):
    """Stream and display the response in real-time."""
    for chunk in client.generate(model=model, prompt=prompt, stream=True):
        response_text = chunk['response']
        print(response_text, end='', flush=True)


def print_footer():
    """Display the completion footer."""
    print("\n\n" + "-" * 60)
    print("✓ Streaming response completed.")
    print("-" * 60 + "\n")


def main():
    # Initialize the Ollama client
    client = ollama.Client(host="http://127.0.0.1:5001")

    # Model and prompt to use
    model = "mistral"
    prompt = "Why is the sky blue?"

    # Display the interface
    print_header()
    print_user_message(prompt, model)

    # Stream the response
    stream_response(client, model, prompt)

    print_footer()


if __name__ == "__main__":
    main()
