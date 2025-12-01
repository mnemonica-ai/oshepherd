/**
 * Example script demonstrating streaming requests to an Ollama server.
 * Displays a chatbot-style interface with real-time streaming responses.
 */

import { Ollama } from "ollama";

function printHeader(): void {
  console.log("\n" + "=".repeat(60));
  console.log("  🤖 Ollama Streaming Chat");
  console.log("=".repeat(60) + "\n");
}

function printUserMessage(prompt: string, model: string): void {
  console.log(`📝 You: ${prompt}`);
  console.log(`🧠 Model: ${model}\n`);
  process.stdout.write("💬 Assistant: ");
}

async function streamResponse(
  client: Ollama,
  model: string,
  prompt: string
): Promise<void> {
  const response = await client.generate({
    model: model,
    prompt: prompt,
    stream: true,
  });

  for await (const chunk of response) {
    process.stdout.write(chunk.response);
  }
}

function printFooter(): void {
  console.log("\n\n" + "-".repeat(60));
  console.log("✓ Streaming response completed.");
  console.log("-".repeat(60) + "\n");
}

async function main(): Promise<void> {
  // Initialize the Ollama client
  const client = new Ollama({ host: "http://127.0.0.1:5001" });

  // Model and prompt to use
  const model = "mistral";
  const prompt = "Why is the sky blue?";

  // Display the interface
  printHeader();
  printUserMessage(prompt, model);

  // Stream the response
  await streamResponse(client, model, prompt);

  printFooter();
}

main().catch(console.error);
