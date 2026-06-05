// src/server.ts
// Main MCP server implementation for SAP Wiki integration
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import { config, validateConfig, getAllSpaceKeys } from './config.js';
import { logger } from './logger.js';
import { tools, executeTool } from './tools.js';

/**
 * SAP Wiki MCP Server class
 * Implements the Model Context Protocol server for Confluence integration
 */
export class SAPWikiMCP {
  private server: Server;

  constructor() {
    // Initialize MCP server with configuration
    this.server = new Server(
      {
        name: config.server.name,
        version: config.server.version,
      },
      {
        capabilities: {
          tools: {}, // Declare tools capability
        },
      }
    );

    this.setupHandlers();
  }

  /**
   * Set up MCP request handlers
   */
  private setupHandlers() {
    // Handler for listing available tools
    this.server.setRequestHandler(ListToolsRequestSchema, async () => {
      logger.info('Listing available tools');
      return { tools };
    });

    // Handler for executing tools
    this.server.setRequestHandler(CallToolRequestSchema, async (request) => {
      const { name, arguments: args } = request.params;
      logger.info(`Tool called: ${name}`, { args });

      try {
        // Execute the requested tool with provided arguments
        const result = await executeTool(name, args || {});
        return result;
      } catch (error: any) {
        logger.error(`Tool execution failed: ${name}`, { error: error.message });
        
        // Return error response in MCP format
        return {
          content: [
            {
              type: 'text',
              text: `Error: ${error.message}`,
            },
          ],
          isError: true,
        };
      }
    });
  }

  /**
   * Start the MCP server
   */
  async run() {
    // Validate configuration before starting
    try {
      validateConfig();
    } catch (error: any) {
      logger.error('Configuration validation failed', { error: error.message });
      throw error;
    }

    // Create stdio transport and connect server
    const transport = new StdioServerTransport();
    await this.server.connect(transport);
    
    logger.info('MCP Server started successfully', {
      name: config.server.name,
      version: config.server.version,
      spaceKeys: getAllSpaceKeys().join(', '),
      toolsCount: tools.length,
    });
  }
}

// Initialize and start the server
const server = new SAPWikiMCP();
server.run().catch((error) => {
  logger.error('Failed to start server', { error: error.message });
  process.exit(1);
});
