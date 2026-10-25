import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";
import { Message } from "./Message.jsx";

function renderMsg(message) {
  const { container } = render(<Message message={message} />);
  return container;
}

describe("Message tool pill (m-42)", () => {
  it("says Using while streaming", () => {
    const c = renderMsg({
      role: "assistant",
      content: "hi",
      tools: ["calculate"],
      streaming: true,
    });
    expect(c.querySelector(".tool-pill").textContent).toContain(
      "Using calculate"
    );
  });

  it("says Used only for a completed turn", () => {
    const c = renderMsg({
      role: "assistant",
      content: "hi",
      tools: ["calculate"],
      streaming: false,
    });
    expect(c.querySelector(".tool-pill").textContent).toBe(
      "Used calculate"
    );
  });

  it("says Attempted on an error turn, not Used", () => {
    const c = renderMsg({
      role: "assistant",
      content: "half",
      tools: ["calculate"],
      streaming: false,
      errorMessage: "boom",
    });
    expect(c.querySelector(".tool-pill").textContent).toContain(
      "Attempted calculate"
    );
  });

  it("says Interrupted on an aborted turn, not Used", () => {
    const c = renderMsg({
      role: "assistant",
      content: "half",
      tools: ["calculate"],
      streaming: false,
      aborted: true,
    });
    expect(c.querySelector(".tool-pill").textContent).toContain(
      "Interrupted calculate"
    );
  });

  it("marks the error note as an alert (m-44)", () => {
    const c = renderMsg({
      role: "assistant",
      content: "half",
      tools: [],
      streaming: false,
      errorMessage: "boom",
    });
    expect(c.querySelector('.error-note[role="alert"]')).not.toBeNull();
  });
});

describe("Message empty replies (m-45)", () => {
  it("renders an explicit note for a finished empty reply", () => {
    const c = renderMsg({
      role: "assistant",
      content: "",
      tools: [],
      streaming: false,
    });
    expect(c.querySelector(".empty-note").textContent).toBe(
      "No reply received."
    );
  });

  it("does not show the note while still streaming", () => {
    const c = renderMsg({
      role: "assistant",
      content: "",
      tools: [],
      streaming: true,
    });
    expect(c.querySelector(".empty-note")).toBeNull();
  });
});
