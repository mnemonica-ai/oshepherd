# Oshepherd in JavaScript/TypeScript

This example demonstrates how to use Oshepherd and Ollama with a JavaScript/TypeScript streaming client.

## Requirements

Before running this example, follow the Oshepherd setup instructions in the [main README](../../README.md#usage):

For this TypeScript example, you'll also need:

- Node.js (v16 or higher)
- Yarn package manager

## Usage

```bash
yarn install
yarn dev
```

This script will send requests to the Oshepherd API, which will orchestrate Ollama models inference execution on available Oshepherd Workers using streaming mode.
