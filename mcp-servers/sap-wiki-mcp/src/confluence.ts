// src/confluence.ts
// Confluence API client for interacting with Confluence REST API
import axios, { AxiosInstance, AxiosError } from 'axios';
import { config, getAllSpaceKeys } from './config.js';
import { logger } from './logger.js';

/**
 * Interface representing a Confluence page
 */
export interface ConfluencePage {
  id: string;
  title: string;
  content: string;
  spaceKey: string;
  version: number;
  lastModified: string;
  url: string;
}

/**
 * Confluence API client class
 * Handles all interactions with the Confluence REST API
 */
export class ConfluenceClient {
  private client: AxiosInstance;

  constructor() {
    // Initialize Axios client with base configuration
    this.client = axios.create({
      baseURL: config.confluence.baseUrl + '/rest/api',
      timeout: config.confluence.timeout,
      headers: {
        'Authorization': `Bearer ${config.confluence.apiToken}`,
        'Accept': 'application/json',
        'Content-Type': 'application/json',
      },
    });

    // Add response interceptor for logging and error handling
    this.client.interceptors.response.use(
      (response) => {
        logger.debug(`API Success: ${response.config.method?.toUpperCase()} ${response.config.url} -> ${response.status}`);
        return response;
      },
      (error: AxiosError) => {
        const status = error.response?.status || 'unknown';
        const message = error.response?.data || error.message;
        const url = error.config?.url || 'unknown';
        
        // Handle specific network and timeout errors
        let enhancedError = error;
        if (error.code === 'ECONNABORTED') {
          const timeoutError = new Error(`Request timeout: The request to ${url} exceeded the configured timeout (${config.confluence.timeout}ms)`);
          timeoutError.name = 'TimeoutError';
          enhancedError = timeoutError as any;
          logger.error(`API Timeout: ${url}`, { 
            timeout: config.confluence.timeout,
            url,
          });
        } else if (error.code === 'ENOTFOUND' || error.code === 'ECONNREFUSED') {
          const connectionError = new Error(`Connection failed: Unable to connect to ${config.confluence.baseUrl}. Please check the URL and network connectivity.`);
          connectionError.name = 'ConnectionError';
          enhancedError = connectionError as any;
          logger.error(`API Connection Error: ${error.code}`, { 
            baseUrl: config.confluence.baseUrl,
            errorCode: error.code,
            url,
          });
        } else if (error.response?.status === 401) {
          const authError = new Error('Authentication failed: Invalid API token or insufficient permissions');
          authError.name = 'AuthenticationError';
          enhancedError = authError as any;
          logger.error('API Authentication Error', { 
            status: 401,
            url,
          });
        } else if (error.response?.status === 403) {
          const permissionError = new Error('Access denied: Insufficient permissions to access the requested resource');
          permissionError.name = 'PermissionError';
          enhancedError = permissionError as any;
          logger.error('API Permission Error', { 
            status: 403,
            url,
          });
        } else if (error.response?.status === 404) {
          const notFoundError = new Error('Resource not found: The requested page or space does not exist');
          notFoundError.name = 'NotFoundError';
          enhancedError = notFoundError as any;
          logger.error('API Not Found Error', { 
            status: 404,
            url,
          });
        } else {
          logger.error(`API Error: ${status} ${url}`, { 
            status,
            message: typeof message === 'string' ? message : JSON.stringify(message),
            url,
            errorCode: error.code,
          });
        }
        
        return Promise.reject(enhancedError);
      }
    );
  }

  /**
   * List pages across all configured spaces
   */
  async listPages(limit: number = 50): Promise<ConfluencePage[]> {
    const spaceKeys = getAllSpaceKeys();
    
    if (spaceKeys.length === 0) {
      throw new Error('No Confluence spaces configured');
    }

    try {
      logger.info(`Listing pages across ${spaceKeys.length} spaces: ${spaceKeys.join(', ')} (limit per space: ${Math.ceil(limit / spaceKeys.length)})`);
      
      const allPages: ConfluencePage[] = [];
      const limitPerSpace = Math.ceil(limit / spaceKeys.length);
      
      // Fetch pages from each space
      for (const spaceKey of spaceKeys) {
        try {
          const response = await this.client.get('/content', {
            params: {
              spaceKey,
              type: 'page',
              limit: limitPerSpace,
              expand: 'body.storage,version,space',
            },
          });

          const pages = response.data.results || [];
          logger.info(`Found ${pages.length} pages in space ${spaceKey}`);
          
          allPages.push(...pages.map((page: any) => this.transformPage(page)));
        } catch (error: any) {
          logger.error(`Failed to list pages in space ${spaceKey}:`, error.message);
          // Continue with other spaces even if one fails
        }
      }

      // Sort by last modified date (newest first) and limit results
      allPages.sort((a, b) => new Date(b.lastModified).getTime() - new Date(a.lastModified).getTime());
      const limitedPages = allPages.slice(0, limit);
      
      logger.info(`Total found ${allPages.length} pages across all spaces, returning ${limitedPages.length}`);
      return limitedPages;
      
    } catch (error: any) {
      logger.error(`Failed to list pages across spaces:`, error.message);
      throw error;
    }
  }

  /**
   * Get a specific page by ID
   */
  async getPage(pageId: string): Promise<ConfluencePage> {
    try {
      logger.info(`Getting page: ${pageId}`);

      const response = await this.client.get(`/content/${pageId}`, {
        params: {
          expand: 'body.storage,version,space,ancestors',  // Filter to get current/published page only
        },
      });

      return this.transformPage(response.data);
      
    } catch (error: any) {
      logger.error(`Failed to get page ${pageId}:`, error.message);
      throw error;
    }
  }

  /**
   * Search pages across all configured spaces or a specific space
   */
  async searchPages(query: string, limit: number = 20, spaceKey?: string): Promise<ConfluencePage[]> {
    const spaceKeys = getAllSpaceKeys();
    
    if (spaceKeys.length === 0) {
      throw new Error('No Confluence spaces configured');
    }

    // Determine which spaces to search
    const targetSpaces = spaceKey ? [spaceKey] : spaceKeys;
    
    // Validate that the specified space is configured
    if (spaceKey && !spaceKeys.includes(spaceKey)) {
      throw new Error(`Space "${spaceKey}" is not configured. Available spaces: ${spaceKeys.join(', ')}`);
    }

    try {
      const searchMsg = spaceKey ? `space "${spaceKey}"` : `${spaceKeys.length} spaces: ${spaceKeys.join(', ')}`;
      logger.info(`Searching pages: "${query}" in ${searchMsg}`);
      
      const allPages: ConfluencePage[] = [];
      const limitPerSpace = Math.ceil(limit / targetSpaces.length);
      
      // Search in target spaces
      for (const targetSpace of targetSpaces) {
        try {
          // Use CQL with space restriction
          const cql = `space = ${targetSpace} AND type = page AND (title ~ "${query}" OR text ~ "${query}")`;

          const response = await this.client.get('/content/search', {
            params: {
              cql,
              limit: limitPerSpace,
              expand: 'body.storage,version,space',
            },
          });

          const pages = response.data.results || [];
          logger.info(`Search found ${pages.length} pages in space ${targetSpace}`);
          
          allPages.push(...pages.map((page: any) => this.transformPage(page)));
        } catch (error: any) {
          logger.error(`Search failed in space ${targetSpace} for "${query}":`, error.message);
          // Continue with other spaces even if one fails
        }
      }

      // Sort by relevance (title matches first, then by last modified)
      allPages.sort((a, b) => {
        const aTitle = a.title.toLowerCase().includes(query.toLowerCase());
        const bTitle = b.title.toLowerCase().includes(query.toLowerCase());
        
        if (aTitle && !bTitle) return -1;
        if (!aTitle && bTitle) return 1;
        
        // If both match title or both don't, sort by last modified
        return new Date(b.lastModified).getTime() - new Date(a.lastModified).getTime();
      });
      
      const limitedPages = allPages.slice(0, limit);
      const resultMsg = spaceKey ? `space "${spaceKey}"` : 'all spaces';
      logger.info(`Total search found ${allPages.length} pages in ${resultMsg}, returning ${limitedPages.length}`);
      
      return limitedPages;
      
    } catch (error: any) {
      logger.error(`Search failed for "${query}":`, error.message);
      throw error;
    }
  }

  /**
   * Get child pages of a specific page
   */
  async getChildPages(pageId: string): Promise<ConfluencePage[]> {
    try {
      logger.info(`Getting child pages of: ${pageId}`);
      
      const response = await this.client.get(`/content/${pageId}/child/page`, {
        params: {
          expand: 'body.storage,version,space',
          limit: 100,  // Increase limit to get more child pages
        },
      });

      const pages = response.data.results || [];
      logger.info(`Found ${pages.length} child pages`);

      return pages.map((page: any) => this.transformPage(page));
      
    } catch (error: any) {
      logger.error(`Failed to get child pages of ${pageId}:`, error.message);
      throw error;
    }
  }

  /**
   * Get space information
   */
  async getSpace(spaceKey: string): Promise<any> {
    try {
      logger.info(`Getting space info: ${spaceKey}`);
      const response = await this.client.get(`/space/${spaceKey}`, {
        params: {
          expand: 'description.plain,homepage',
        },
      });
      return response.data;
    } catch (error: any) {
      logger.error(`Failed to get space ${spaceKey}:`, error.message);
      throw error;
    }
  }

  /**
   * Create a new page in a specific space
   */
  async createPage(
    title: string,
    content: string,
    spaceKey?: string,
    parentId?: string
  ): Promise<ConfluencePage> {
    // Use provided space key or fall back to first configured space
    const targetSpaceKey = spaceKey || getAllSpaceKeys()[0];
    
    if (!targetSpaceKey) {
      throw new Error('No space key provided and no spaces configured');
    }

    try {
      logger.info(`Creating page: "${title}" in space ${targetSpaceKey}`);

      const pageData: any = {
        type: 'page',
        title,
        space: {
          key: targetSpaceKey,
        },
        body: {
          storage: {
            value: content,
            representation: 'storage',
          },
        },
      };

      if (parentId) {
        pageData.ancestors = [{ id: parentId }];
      }

      const response = await this.client.post('/content', pageData);
      
      logger.info(`Page created successfully: ${response.data.id}`);
      return this.transformPage(response.data);
      
    } catch (error: any) {
      logger.error(`Failed to create page "${title}" in space ${targetSpaceKey}:`, error.message);
      throw error;
    }
  }

  /**
   * List all configured spaces with their information
   */
  async listSpaces(): Promise<any[]> {
    const spaceKeys = getAllSpaceKeys();
    
    if (spaceKeys.length === 0) {
      throw new Error('No Confluence spaces configured');
    }

    try {
      logger.info(`Getting information for ${spaceKeys.length} spaces: ${spaceKeys.join(', ')}`);
      
      const spaces = [];
      
      for (const spaceKey of spaceKeys) {
        try {
          const space = await this.getSpace(spaceKey);
          spaces.push(space);
        } catch (error: any) {
          logger.error(`Failed to get info for space ${spaceKey}:`, error.message);
          // Add a minimal entry for failed spaces
          spaces.push({
            key: spaceKey,
            name: `${spaceKey} (Error: ${error.message})`,
            type: 'unknown',
            status: 'error'
          });
        }
      }
      
      logger.info(`Retrieved information for ${spaces.length} spaces`);
      return spaces;
      
    } catch (error: any) {
      logger.error(`Failed to list spaces:`, error.message);
      throw error;
    }
  }

  /**
   * Update an existing page
   */
  async updatePage(
    pageId: string,
    title: string,
    content: string
  ): Promise<ConfluencePage> {
    try {
      logger.info(`Updating page: ${pageId}`);

      // First, get current version
      const currentPage = await this.getPage(pageId);

      const updateData = {
        id: pageId,
        type: 'page',
        title,
        space: {
          key: currentPage.spaceKey,
        },
        body: {
          storage: {
            value: content,
            representation: 'storage',
          },
        },
        version: {
          number: currentPage.version + 1,
        },
      };

      const response = await this.client.put(`/content/${pageId}`, updateData);
      
      logger.info(`Page updated successfully: ${pageId}`);
      return this.transformPage(response.data);
      
    } catch (error: any) {
      logger.error(`Failed to update page ${pageId}:`, error.message);
      throw error;
    }
  }

  /**
   * Delete a page
   */
  async deletePage(pageId: string): Promise<void> {
    try {
      logger.info(`Deleting page: ${pageId}`);
      
      await this.client.delete(`/content/${pageId}`);
      
      logger.info(`Page deleted successfully: ${pageId}`);
      
    } catch (error: any) {
      logger.error(`Failed to delete page ${pageId}:`, error.message);
      throw error;
    }
  }

  /**
   * Transform Confluence API response to our ConfluencePage format
   */
  private transformPage(apiPage: any): ConfluencePage {
    const baseUrl = config.confluence.baseUrl;
    
    return {
      id: apiPage.id,
      title: apiPage.title,
      content: apiPage.body?.storage?.value || '',
      spaceKey: apiPage.space?.key || '',
      version: apiPage.version?.number || 0,
      lastModified: apiPage.version?.when || '',
      url: `${baseUrl}/pages/viewpage.action?pageId=${apiPage.id}`,
    };
  }
}

// Export singleton instance
export const confluenceClient = new ConfluenceClient();
