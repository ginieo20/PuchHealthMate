import { WorkerMCP } from "workers-mcp";
import { z } from "zod";

export default {
  fetch: WorkerMCP({
    name: "medline-mcp-server",
    version: "1.0.0",
    tools: {
      validate: {
        description: "Validation hook required by some MCP clients.",
        schema: z.object({}),
        async run() {
          return { type: "text" as const, text: "918830273648" };
        },
      },
      medline_search: {
        description: "Search MedlinePlus for health information",
        schema: z.object({
          query: z.string().describe("Health topic to search for"),
        }),
        async run({ query }) {
          const url = `https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&term=${encodeURIComponent(query)}`;
          const res = await fetch(url);
          if (!res.ok) {
            return {
              type: "text" as const,
              text: `Request failed with status ${res.status}`,
            };
          }
          const xml = await res.text();
          return {
            type: "text" as const,
            text: xml,
          };
        },
      },
      medline_search_parsed: {
        description: "Search MedlinePlus and return a concise summary with titles and links.",
        schema: z.object({
          query: z.string().describe("Health topic to search for"),
          maxItems: z.number().int().min(1).max(10).default(5),
        }),
        async run({ query, maxItems }) {
          const url = `https://wsearch.nlm.nih.gov/ws/query?db=healthTopics&term=${encodeURIComponent(query)}`;
          const res = await fetch(url);
          if (!res.ok) {
            return { type: "text" as const, text: `Request failed with status ${res.status}` };
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
            return { type: "text" as const, text: "No results found." };
          }
          const md = [
            `MedlinePlus results for: ${query}`,
            "",
            ...items.map((it, i) => `${i + 1}. ${it.title}\n${it.url}`),
          ].join("\n");
          return { type: "text" as const, text: md };
        },
      },
    },
  }),
};