"use client";

import "highlight.js/styles/github-dark.css";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";

/** Isolated so the chat route can lazy-load highlight.js only after a turn finishes. */
export function RichMarkdown({ children }: { children: string }) {
  return <ReactMarkdown rehypePlugins={[rehypeHighlight]}>{children}</ReactMarkdown>;
}
