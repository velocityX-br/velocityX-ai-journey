// src/tools.ts
import { Tool } from '@modelcontextprotocol/sdk/types.js';
import { confluenceClient } from './confluence.js';
import { logger } from './logger.js';
import { CacheManager } from './cache.js';
import { MarkdownConverter } from './markdown-converter.js';

// Initialize cache manager
const cacheManager = new CacheManager();

/**
 * Define all available tools
 */
export const tools: Tool[] = [
  {
    name: 'list_pages',
    description: 'List pages across all configured Confluence spaces. Returns a list of pages with their titles, IDs, and basic metadata.',
    inputSchema: {
      type: 'object',
      properties: {
        limit: {
          type: 'number',
          description: 'Maximum number of pages to return (default: 50, max: 100)',
          default: 50,
        },
      },
    },
  },
  {
    name: 'list_spaces',
    description: 'List all configured Confluence spaces with their information including names, keys, and descriptions.',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'get_page',
    description: 'Get the full content of a specific Confluence page by its ID. Returns the page title, content, version, and metadata.',
    inputSchema: {
      type: 'object',
      properties: {
        pageId: {
          type: 'string',
          description: 'The ID of the page to retrieve',
        },
      },
      required: ['pageId'],
    },
  },
  {
    name: 'search_pages',
    description: 'Search for pages by title in the configured Confluence spaces. Returns matching pages with their IDs and metadata.',
    inputSchema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Search query to match against page titles',
        },
        limit: {
          type: 'number',
          description: 'Maximum number of results to return (default: 20)',
          default: 20,
        },
        spaceKey: {
          type: 'string',
          description: 'Optional: Specific space key to search in. If not provided, searches across all configured spaces.',
        },
      },
      required: ['query'],
    },
  },
  {
    name: 'get_child_pages',
    description: 'Get all child pages of a specific parent page. Useful for navigating the page hierarchy.',
    inputSchema: {
      type: 'object',
      properties: {
        pageId: {
          type: 'string',
          description: 'The ID of the parent page',
        },
      },
      required: ['pageId'],
    },
  },
  {
    name: 'create_page',
    description: 'Create a new page in a Confluence space. Requires title and content. Optionally can specify a space key and parent page.',
    inputSchema: {
      type: 'object',
      properties: {
        title: {
          type: 'string',
          description: 'Title of the new page',
        },
        content: {
          type: 'string',
          description: 'Content of the page in Confluence storage format (HTML)',
        },
        spaceKey: {
          type: 'string',
          description: 'Optional: Space key where to create the page. If not provided, uses the first configured space.',
        },
        parentId: {
          type: 'string',
          description: 'Optional: ID of the parent page',
        },
      },
      required: ['title', 'content'],
    },
  },
  {
    name: 'update_page',
    description: 'Update an existing Confluence page. Requires page ID, new title, and new content.',
    inputSchema: {
      type: 'object',
      properties: {
        pageId: {
          type: 'string',
          description: 'ID of the page to update',
        },
        title: {
          type: 'string',
          description: 'New title for the page',
        },
        content: {
          type: 'string',
          description: 'New content in Confluence storage format (HTML)',
        },
      },
      required: ['pageId', 'title', 'content'],
    },
  },
  {
    name: 'clear_cache',
    description: 'Clear all cached data to force fresh retrieval from API. Useful when cached content is outdated.',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'cache_stats',
    description: 'Get cache statistics including number of cached pages and cache directory information.',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  },
  {
    name: 'refresh_page_cache',
    description: 'Force refresh a specific page cache by fetching latest content from API.',
    inputSchema: {
      type: 'object',
      properties: {
        pageId: {
          type: 'string',
          description: 'ID of the page to refresh in cache',
        },
      },
      required: ['pageId'],
    },
  },
  {
    name: 'clean_expired_cache',
    description: 'Clean up expired cache files from disk to free up space and remove outdated content.',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  },
];

/**
 * Execute a tool by name with given arguments
 */
export async function executeTool(name: string, args: any): Promise<any> {
  logger.info(`Executing tool: ${name}`, { args });

  try {
    switch (name) {
      case 'list_pages':
        return await handleListPages(args);

      case 'list_spaces':
        return await handleListSpaces(args);

      case 'get_page':
        return await handleGetPage(args);

      case 'search_pages':
        return await handleSearchPages(args);

      case 'get_child_pages':
        return await handleGetChildPages(args);

      case 'create_page':
        return await handleCreatePage(args);

      case 'update_page':
        return await handleUpdatePage(args);

      case 'clear_cache':
        return await handleClearCache(args);

      case 'cache_stats':
        return await handleCacheStats(args);

      case 'refresh_page_cache':
        return await handleRefreshPageCache(args);

      case 'clean_expired_cache':
        return await handleCleanExpiredCache(args);

      default:
        throw new Error(`Unknown tool: ${name}`);
    }
  } catch (error: any) {
    logger.error(`Tool execution error: ${name}`, { error: error.message });
    throw error;
  }
}

/**
 * Tool handlers
 */

async function handleListPages(args: any) {
  const limit = Math.min(args.limit || 50, 100);
  
  // Check if we have cached pages
  let pages = cacheManager.listPages();
  let cacheHit = false;
  
  if (pages.length >= limit) {
    logger.info(`Using cached pages list (${pages.length} total cached)`);
    cacheHit = true;
    // Limit the cached results
    pages = pages.slice(0, limit);
  } else {
    logger.info(`Fetching pages from API (only ${pages.length} cached)`);
    pages = await confluenceClient.listPages(limit);
    // Save all pages to cache
    pages.forEach(page => cacheManager.savePage(page));
  }

  const summary = pages
    .map((p, i) => `${i + 1}. ${p.title} (Space: ${p.spaceKey}, ID: ${p.id})`)
    .join('\n');

  const cacheStatus = cacheHit ? ' (from cache)' : ' (from API)';

  return {
    content: [
      {
        type: 'text',
        text: `Found ${pages.length} pages${cacheStatus}:\n\n${summary}\n\nUse get_page tool with a page ID to view full content.`,
      },
    ],
  };
}

async function handleListSpaces(args: any) {
  const spaces = await confluenceClient.listSpaces();

  if (spaces.length === 0) {
    return {
      content: [
        {
          type: 'text',
          text: 'No spaces configured or accessible.',
        },
      ],
    };
  }

  const summary = spaces
    .map((space, i) => {
      const name = space.name || space.key;
      const description = space.description?.plain ? ` - ${space.description.plain}` : '';
      const status = space.status === 'error' ? ' (Error accessing space)' : '';
      return `${i + 1}. **${name}** (${space.key})${description}${status}`;
    })
    .join('\n');

  return {
    content: [
      {
        type: 'text',
        text: `## Configured Confluence Spaces\n\n${summary}\n\nUse the space keys when creating pages to specify which space to create them in.`,
      },
    ],
  };
}

async function handleGetPage(args: any) {
  const { pageId } = args;
  
  // Use smart caching with ETag validation
  let page = await cacheManager.getPageSmart(pageId, confluenceClient);
  
  if (!page) {
    logger.info(`No cache for page: ${pageId}, fetching from API`);
    page = await confluenceClient.getPage(pageId);
    // Save to cache with ETag
    cacheManager.savePage(page);
  }

  // Convert content to markdown for LLM consumption (but don't cache this format)
  const markdownConverter = new MarkdownConverter();
  const markdownContent = markdownConverter.convertToMarkdown(page.content);

  return {
    content: [
      {
        type: 'text',
        text: `# ${page.title}\n\n` +
              `**Page ID:** ${page.id}\n` +
              `**Space:** ${page.spaceKey}\n` +
              `**Version:** ${page.version}\n` +
              `**Last Modified:** ${page.lastModified}\n` +
              `**URL:** ${page.url}\n\n` +
              `## Content\n\n${markdownContent}`,
      },
    ],
  };
}

async function handleSearchPages(args: any) {
  const { query, limit = 20, spaceKey } = args;
  
  // Try cache search first
  let pages = cacheManager.search(query);
  let cacheHit = false;
  
  if (pages.length > 0) {
    logger.info(`Cache search hit for query: "${query}", found ${pages.length} cached results`);
    cacheHit = true;
    
    // Filter by spaceKey if specified
    if (spaceKey) {
      pages = pages.filter(page => page.spaceKey === spaceKey);
      logger.info(`Filtered cached results to space "${spaceKey}": ${pages.length} results`);
    }
    
    // Limit the cached results
    pages = pages.slice(0, limit);
  } else {
    logger.info(`Cache search miss for query: "${query}", fetching from API`);
    pages = await confluenceClient.searchPages(query, limit, spaceKey);
    // Save all found pages to cache
    pages.forEach(page => cacheManager.savePage(page));
  }

  if (pages.length === 0) {
    const spaceMsg = spaceKey ? ` in space "${spaceKey}"` : '';
    return {
      content: [
        {
          type: 'text',
          text: `No pages found matching "${query}"${spaceMsg}`,
        },
      ],
    };
  }

  const summary = pages
    .map((p, i) => `${i + 1}. ${p.title} (Space: ${p.spaceKey}, ID: ${p.id})`)
    .join('\n');

  const cacheStatus = cacheHit ? ' (from cache)' : ' (from API)';
  const spaceMsg = spaceKey ? ` in space "${spaceKey}"` : ' across all spaces';

  return {
    content: [
      {
        type: 'text',
        text: `Found ${pages.length} pages matching "${query}"${spaceMsg}${cacheStatus}:\n\n${summary}`,
      },
    ],
  };
}

async function handleGetChildPages(args: any) {
  const { pageId } = args;
  const pages = await confluenceClient.getChildPages(pageId);

  if (pages.length === 0) {
    return {
      content: [
        {
          type: 'text',
          text: `No child pages found for page ID: ${pageId}`,
        },
      ],
    };
  }

  const summary = pages
    .map((p, i) => `${i + 1}. ${p.title} (ID: ${p.id})`)
    .join('\n');

  return {
    content: [
      {
        type: 'text',
        text: `Found ${pages.length} child pages:\n\n${summary}`,
      },
    ],
  };
}

async function handleCreatePage(args: any) {
  const { title, content, spaceKey, parentId } = args;
  const page = await confluenceClient.createPage(title, content, spaceKey, parentId);

  return {
    content: [
      {
        type: 'text',
        text: `Page created successfully!\n\n` +
              `**Title:** ${page.title}\n` +
              `**ID:** ${page.id}\n` +
              `**Space:** ${page.spaceKey}\n` +
              `**URL:** ${page.url}`,
      },
    ],
  };
}

async function handleUpdatePage(args: any) {
  const { pageId, title, content } = args;
  const page = await confluenceClient.updatePage(pageId, title, content);

  return {
    content: [
      {
        type: 'text',
        text: `Page updated successfully!\n\n` +
              `**Title:** ${page.title}\n` +
              `**ID:** ${page.id}\n` +
              `**New Version:** ${page.version}\n` +
              `**URL:** ${page.url}`,
      },
    ],
  };
}

async function handleClearCache(args: any) {
  const stats = cacheManager.getStats();
  cacheManager.clear();
  
  logger.info('Cache cleared by user request');

  return {
    content: [
      {
        type: 'text',
        text: `Cache cleared successfully!\n\n` +
              `**Cleared:**\n` +
              `- Memory entries: ${stats.memoryEntries}\n` +
              `- Disk files: ${stats.diskFiles}\n` +
              `- Cache directory: ${stats.cacheDir}\n\n` +
              `All subsequent requests will fetch fresh data from the API.`,
      },
    ],
  };
}

async function handleCacheStats(args: any) {
  const stats = cacheManager.getStats();
  const cachedPages = cacheManager.listPages();
  
  // Get some sample cached page titles
  const sampleTitles = cachedPages
    .slice(0, 5)
    .map(p => `- ${p.title} (ID: ${p.id})`)
    .join('\n');

  return {
    content: [
      {
        type: 'text',
        text: `## Cache Statistics\n\n` +
              `**Memory Cache:** ${stats.memoryEntries} entries\n` +
              `**Disk Cache:** ${stats.diskFiles} files\n` +
              `**Cache Directory:** ${stats.cacheDir}\n` +
              `**Total Cached Pages:** ${cachedPages.length}\n\n` +
              (cachedPages.length > 0 ? 
                `**Sample Cached Pages:**\n${sampleTitles}${cachedPages.length > 5 ? '\n- ...' : ''}` :
                `**No pages currently cached.**`) +
              `\n\nUse \`clear_cache\` tool to clear all cached data if needed.`,
      },
    ],
  };
}

async function handleRefreshPageCache(args: any) {
  const { pageId } = args;
  
  logger.info(`Smart refreshing cache for page: ${pageId}`);
  
  // Use smart refresh with ETag validation
  const page = await cacheManager.refreshPageCache(pageId, confluenceClient);

  return {
    content: [
      {
        type: 'text',
        text: `Page cache refreshed successfully!\n\n` +
              `**Page:** ${page.title}\n` +
              `**ID:** ${page.id}\n` +
              `**Version:** ${page.version}\n` +
              `**Last Modified:** ${page.lastModified}\n\n` +
              `The cache now contains the latest version of this page. ` +
              `ETag validation ensures efficient updates only when content changes.`,
      },
    ],
  };
}

async function handleCleanExpiredCache(args: any) {
  logger.info('Starting expired cache cleanup');
  
  const cleanedCount = cacheManager.cleanExpired();
  const stats = cacheManager.getStats();

  return {
    content: [
      {
        type: 'text',
        text: `Expired cache cleanup completed!\n\n` +
              `**Cleaned:** ${cleanedCount} expired/corrupted files\n` +
              `**Remaining:**\n` +
              `- Memory entries: ${stats.memoryEntries}\n` +
              `- Disk files: ${stats.diskFiles}\n` +
              `- Cache directory: ${stats.cacheDir}\n\n` +
              (cleanedCount > 0 
                ? `${cleanedCount} outdated cache files have been removed to free up space.`
                : `No expired cache files found. All cached content is still valid.`),
      },
    ],
  };
}
