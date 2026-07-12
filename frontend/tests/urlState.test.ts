import { describe, expect, it } from "vitest";
import {
  readEnumParam,
  readPositiveIntegerParam,
  readTextParam,
  replaceSearchParams,
} from "../src/urlState";

describe("URL-backed state", () => {
  it("reads valid filters and updates only the requested parameters", () => {
    window.history.replaceState(
      {},
      "",
      "/?search=platform+engineer&status=interesting&page=3&company=Keep+Me&invalid=1"
    );

    expect(readTextParam("search")).toBe("platform engineer");
    expect(readEnumParam("status", ["unreviewed", "interesting"] as const)).toBe("interesting");
    expect(readEnumParam("status", ["applied", "rejected"] as const)).toBe("");
    expect(readPositiveIntegerParam("page", 1)).toBe(3);

    replaceSearchParams({ search: "  staff engineer  ", status: "", page: undefined });

    const params = new URLSearchParams(window.location.search);
    expect(params.get("search")).toBe("staff engineer");
    expect(params.has("status")).toBe(false);
    expect(params.has("page")).toBe(false);
    expect(params.get("company")).toBe("Keep Me");
    expect(params.get("invalid")).toBe("1");
  });
});
