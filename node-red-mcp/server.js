import axios from "axios";

import { Server } from "@modelcontextprotocol/sdk/server/index.js";

import {
    StdioServerTransport
} from "@modelcontextprotocol/sdk/server/stdio.js";

import {
    CallToolRequestSchema,
    ListToolsRequestSchema
} from "@modelcontextprotocol/sdk/types.js";

const server = new Server(
    {
        name: "node-red-mcp",
        version: "1.0.0"
    },
    {
        capabilities: {
            tools: {}
        }
    }
);

server.setRequestHandler(
    ListToolsRequestSchema,
    async () => ({
        tools: [
            {
                name: "test_api",
                description: "測試 Node-RED API",
                inputSchema: {
                    type: "object",
                    properties: {},
                    required: []
                }
            }
        ]
    })
);

server.setRequestHandler(
    CallToolRequestSchema,
    async (request) => {

        if (request.params.name === "test_api") {

            const result = await axios.get(
                "http://127.0.0.1:1880/ai/test"
            );

            return {
                content: [
                    {
                        type: "text",
                        text: JSON.stringify(result.data)
                    }
                ]
            };
        }
    }
);

const transport = new StdioServerTransport();

await server.connect(transport);