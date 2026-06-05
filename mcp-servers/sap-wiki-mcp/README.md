# SAP Wiki MCP Server

A comprehensive Model Context Protocol (MCP) server that provides seamless integration with SAP Confluence Wiki spaces. This server enables AI assistants to interact with Confluence pages through a standardized interface, supporting complete page lifecycle management operations across multiple spaces.

## 🚀 Features

- **Multi-Space Support**: Configure and operate across multiple Confluence spaces simultaneously
- **Complete Page Management**: List, retrieve, create, and update Confluence pages
- **Advanced Search**: Search pages by title and content with optional space-specific targeting
- **Hierarchical Navigation**: Navigate parent-child page relationships
- **Intelligent Caching**: Dual-layer caching system (memory + disk) for optimal performance
- **Flexible Configuration**: Unified space configuration supporting both single and multiple spaces
- **Comprehensive Logging**: Structured logging with multiple levels and transports
- **Type Safety**: Built with TypeScript for robust development experience
- **Backward Compatibility**: Seamless upgrade path from single-space configurations

## 🛠️ Available Tools


| Tool Name             | Description                               | Parameters                                                       |
| --------------------- | ----------------------------------------- | ---------------------------------------------------------------- |
| `list_pages`          | List pages across all configured spaces   | `limit` (optional, max 100)                                      |
| `list_spaces`         | List all configured Confluence spaces     | None                                                             |
| `get_page`            | Get full content of a specific page by ID | `pageId` (required)                                              |
| `search_pages`        | Search pages by title and content         | `query` (required), `limit` (optional), `spaceKey` (optional)    |
| `get_child_pages`     | Get all child pages of a parent page      | `pageId` (required)                                              |
| `create_page`         | Create a new page                         | `title`, `content` (required), `spaceKey`, `parentId` (optional) |
| `update_page`         | Update an existing page                   | `pageId`, `title`, `content` (required)                          |
| `clear_cache`         | Clear all cached data                     | None                                                             |
| `cache_stats`         | Get cache statistics                      | None                                                             |
| `refresh_page_cache`  | Force refresh specific page cache         | `pageId` (required)                                              |
| `clean_expired_cache` | Clean up expired cache files              | None                                                             |

## 🎯 New Search Capabilities

### Global Search (Default)

Search across all configured spaces:

```json
{
  "tool": "search_pages",
  "arguments": {
    "query": "API documentation",
    "limit": 20
  }
}
```

### 🆕 Space-Specific Search

Target a specific space for precise results:

```json
{
  "tool": "search_pages",
  "arguments": {
    "query": "deployment guide",
    "limit": 20,
    "spaceKey": "PROD"
  }
}
```

### Recommended Workflow

1. **Explore**: Use global search to discover content across all spaces
2. **Identify**: Find which spaces contain relevant information
3. **Focus**: Use space-specific search for detailed exploration

## 📁 Project Structure

```
sap-wiki-mcp/
├── src/                    # Source code
│   ├── server.ts          # MCP server main logic
│   ├── tools.ts           # Tool definitions and execution
│   ├── confluence.ts      # Confluence API client
│   ├── cache.ts           # Caching system
│   ├── config.ts          # Configuration management
│   └── logger.ts          # Logging system
├── dist/                  # Compiled output
├── cache/                 # Cache data
├── logs/                  # Log files
├── tsconfig.json          # TypeScript configuration
├── package.json           # Project configuration
├── README.md              # This file
└── env.example            # Environment variables example
```

## 🚀 Quick Start

### Prerequisites

- **Node.js** (version 18 or higher)
- **npm** or **yarn** package manager
- **Access to SAP Confluence Wiki** with appropriate permissions
- **API Token** for Confluence authentication

### Standard Installation

1. **Clone and install dependencies:**

   ```bash
   git clone <repository-url>
   cd sap-wiki-mcp
   npm install
   ```
2. **Configure environment:**

   ```bash
   cp env.example .env
   # Edit .env with your Confluence credentials
   ```
3. **Build and start:**

   ```bash
   npm run build
   npm start
   ```

## ⚙️ Configuration

### Required Environment Variables


| Variable                | Description                          | Example                    |
| ----------------------- | ------------------------------------ | -------------------------- |
| `CONFLUENCE_BASE_URL`   | Base URL of your Confluence instance | `https://wiki.company.com` |
| `CONFLUENCE_API_TOKEN`  | API token for authentication         | `abc123xyz789`             |
| `CONFLUENCE_SPACE_KEYS` | Space key(s) to operate within       | `DEV` or `DEV,TEST,PROD`   |

### 🆕 Flexible Space Configuration

The server now supports flexible space configuration with a single variable:

```bash
# Single space
CONFLUENCE_SPACE_KEYS=DEV

# Multiple spaces (recommended for multi-space workflows)
CONFLUENCE_SPACE_KEYS=DEV,TEST,PROD,DOC

# Legacy support (still works)
CONFLUENCE_SPACE_KEY=DEV
```

### Optional Environment Variables


| Variable        | Description                  | Default   | Example   |
| --------------- | ---------------------------- | --------- | --------- |
| `CACHE_DIR`     | Directory for caching        | `./cache` | `./cache` |
| `CACHE_TTL`     | Cache time-to-live (seconds) | `3600`    | `7200`    |
| `AUTO_SYNC`     | Enable automatic cache sync  | `false`   | `true`    |
| `SYNC_INTERVAL` | Sync interval (seconds)      | `86400`   | `43200`   |
| `LOG_LEVEL`     | Logging level                | `info`    | `debug`   |
| `LOG_DIR`       | Log directory                | `./logs`  | `./logs`  |

## 🔧 Development

### Available Scripts

```bash
# Development
npm run dev          # Start in development mode with auto-reload
npm run build        # Build TypeScript to JavaScript
npm run clean        # Clean build directory
```

## 📖 Usage with MCP Clients

Once the server is running, configure it in your MCP client:

```json
{
  "mcpServers": {
    "sap-wiki": {
      "command": "node",
      "args": ["/path/to/sap-wiki-mcp/dist/server.js"]
    }
  }
}
```

## 🐛 Troubleshooting

### Common Issues

1. **Authentication Errors**

   - Verify your API token is correct
   - Check that the token has appropriate permissions
   - Ensure the Confluence base URL is correct
2. **Space Access Issues**

   - Confirm the space key(s) are correct
   - Verify you have access to the specified space(s)
   - Check space permissions
3. **Connection Problems**

   - Ensure the Confluence URL is accessible
   - Check network connectivity
   - Verify firewall settings

### Debug Mode

Enable debug logging for detailed troubleshooting:

```env
LOG_LEVEL=debug
```
