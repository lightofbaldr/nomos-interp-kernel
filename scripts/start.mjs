// Set the default before adapter-node reads its environment. An explicit HOST
// remains the operator's opt-in; unset or empty must never expose private data.
process.env.HOST ||= '127.0.0.1';
await import('../build/index.js');
