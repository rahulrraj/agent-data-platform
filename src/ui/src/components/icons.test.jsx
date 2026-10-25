import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import React from "react";
import { Sparkle } from "./icons.jsx";

describe("Sparkle gradient ids (Nit)", () => {
  it("gives each instance a unique gradient id", () => {
    const { container } = render(
      <div>
        <Sparkle size={20} />
        <Sparkle size={44} />
      </div>
    );
    const gradients = container.querySelectorAll("linearGradient");
    expect(gradients).toHaveLength(2);
    expect(gradients[0].id).not.toBe(gradients[1].id);
    // each path's fill must reference its own gradient
    const paths = container.querySelectorAll("path");
    expect(paths[0].getAttribute("fill")).toBe(
      `url(#${gradients[0].id})`
    );
  });
});
