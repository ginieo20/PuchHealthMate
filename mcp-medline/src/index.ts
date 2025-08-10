import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { McpAgent } from "agents/mcp";
import { z } from "zod";

type Env = Record<string, never>;

export class MedlineMCP extends McpAgent<Env, Record<string, never>, Record<string, never>> {
  server = new McpServer({ name: "HealthMate", version: "1.0.0" });

  async init() {
    // Validation tool
    this.server.tool("validate", "Validation hook for clients", {}, async () => ({
      content: [{ type: "text", text: "918830273648" }],
    }));

    // Raw XML search
    this.server.tool(
      "medline_search",
      "Search MedlinePlus for health information (returns XML)",
      { query: z.string().describe("Health topic to search for") },
      async ({ query }: { query: string }) => {
        const url = `https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&term=${encodeURIComponent(query)}`;
        const res = await fetch(url);
        if (!res.ok) {
          return { content: [{ type: "text", text: `Request failed with status ${res.status}` }] };
        }
        const xml = await res.text();
        return { content: [{ type: "text", text: xml }] };
      },
    );

    // Parsed summary search
    this.server.tool(
      "medline_search_parsed",
      "Search MedlinePlus and return a concise summary with titles and links",
      {
        query: z.string().describe("Health topic to search for"),
        maxItems: z.number().int().min(1).max(10).default(5),
      },
      async ({ query, maxItems }: { query: string; maxItems: number }) => {
        const url = `https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&term=${encodeURIComponent(query)}`;
        const res = await fetch(url);
        if (!res.ok) {
          return { content: [{ type: "text", text: `Request failed with status ${res.status}` }] };
        }
        const xml = await res.text();
        const docs = xml.match(/<document[\s\S]*?<\/document>/g) || [];
        const items: { title: string; url: string }[] = [];
        for (const d of docs) {
          const t = d.match(/<content[^>]*name=\"title\"[^>]*>([\s\S]*?)<\/content>/);
          const u = d.match(/<content[^>]*name=\"url\"[^>]*>([\s\S]*?)<\/content>/);
          if (t && u) {
            const title = t[1].replace(/\s+/g, " ").trim();
            const urlVal = u[1].trim();
            items.push({ title, url: urlVal });
            if (items.length >= maxItems) break;
          }
        }
        if (items.length === 0) {
          return { content: [{ type: "text", text: "No results found." }] };
        }
        const md = [
          `MedlinePlus results for: ${query}`,
          "",
          ...items.map((it, i) => `${i + 1}. ${it.title}\n${it.url}`),
        ].join("\n");
        return { content: [{ type: "text", text: md }] };
      },
    );
  }
}

export default { fetch: MedlineMCP.mount("/sse") as any };