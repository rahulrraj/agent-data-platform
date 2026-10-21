import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { renderMarkdown } from "./markdown.jsx";

function renderMd(text) {
  const { container } = render(<div>{renderMarkdown(text)}</div>);
  return container;
}

describe("renderMarkdown", () => {
  it("renders plain paragraphs", () => {
    const c = renderMd("Hello world");
    expect(c.querySelector(".md-para").textContent).toBe("Hello world");
  });

  it("renders bold, italic and inline code", () => {
    const c = renderMd("This is **bold**, *italic* and `code`.");
    expect(c.querySelector("strong").textContent).toBe("bold");
    expect(c.querySelector("em").textContent).toBe("italic");
    const code = c.querySelector("code.inline-code");
    expect(code.textContent).toBe("code");
  });

  it("does not mistake a single asterisk in math for emphasis", () => {
    const c = renderMd("What is 6 * 7?");
    expect(c.querySelector("em")).toBeNull();
    expect(c.textContent).toContain("6 * 7?");
  });

  it("renders fenced code blocks with language and copy button", () => {
    const c = renderMd("```python\nprint(42)\n```");
    const block = c.querySelector(".code-block");
    expect(block).not.toBeNull();
    expect(block.querySelector(".code-head span").textContent).toBe("python");
    expect(block.querySelector("pre code").textContent).toBe("print(42)");
    expect(block.querySelector(".copy-btn").textContent).toBe("Copy");
  });

  it("renders text around code blocks", () => {
    const c = renderMd("before\n```\nx = 1\n```\nafter");
    expect(c.textContent).toContain("before");
    expect(c.textContent).toContain("after");
    expect(c.querySelector(".code-block pre code").textContent).toBe("x = 1");
  });

  it("renders unordered and ordered lists", () => {
    const c = renderMd("- apples\n- oranges\n\n1. first\n2. second");
    const ul = c.querySelector("ul.md-list");
    const ol = c.querySelector("ol.md-list");
    expect(ul.querySelectorAll("li")).toHaveLength(2);
    expect(ol.querySelectorAll("li")[0].textContent).toBe("first");
  });

  it("turns single line breaks into <br>", () => {
    const c = renderMd("line one\nline two");
    expect(c.querySelectorAll("br")).toHaveLength(1);
  });

  it("escapes HTML instead of rendering it", () => {
    const c = renderMd("<script>alert(1)</script>");
    expect(c.querySelector("script")).toBeNull();
    expect(c.textContent).toContain("<script>alert(1)</script>");
  });

  it("formats inline markup inside list items", () => {
    const c = renderMd("- use the `calculate` tool");
    const li = c.querySelector("ul.md-list li");
    expect(li.querySelector("code.inline-code").textContent).toBe("calculate");
  });

  it("leaves an unclosed fence as plain text", () => {
    const c = renderMd("```python\nprint(42)");
    expect(c.querySelector(".code-block")).toBeNull();
    expect(c.textContent).toContain("print(42)");
  });
});
